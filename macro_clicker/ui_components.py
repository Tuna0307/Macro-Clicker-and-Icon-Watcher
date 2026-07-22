import io
import importlib
import math
import os
import struct
import threading
import tkinter as tk
import wave
from tkinter import ttk
from typing import Any

from .models import has_smart_rally_team_prefilter

try:
    ctk: Any = importlib.import_module("customtkinter")
except ImportError:  # Keep source checkouts usable before requirements are installed.
    ctk = None

try:
    winsound: Any = importlib.import_module("winsound")
except ImportError:  # pragma: no cover - Windows is the supported desktop target.
    winsound = None


CUSTOMTKINTER_AVAILABLE = ctk is not None
if CUSTOMTKINTER_AVAILABLE:
    # Detection and clicking use physical screen coordinates. Keep the
    # process's established DPI behavior instead of letting a presentation
    # library change coordinate semantics underneath mss/pyautogui.
    ctk.deactivate_automatic_dpi_awareness()

COLORS = {
    "app": "#f4f8ff",
    "surface": "#ffffff",
    "surface_alt": "#eaf3ff",
    "surface_warm": "#fff7e8",
    "border": "#cbdcf0",
    "text": "#17324d",
    "muted": "#61778d",
    "accent": "#1677e8",
    "accent_hover": "#0c65cb",
    "accent_pressed": "#0954ab",
    "accent_soft": "#dcecff",
    "danger": "#df4055",
    "danger_hover": "#c52f43",
    "danger_pressed": "#a92134",
    "warning": "#c96f12",
    "success": "#148b68",
    "success_bright": "#21ad82",
    "button": "#e9f2fc",
    "button_hover": "#d7e9fc",
    "button_pressed": "#bdd9f6",
    "button_disabled": "#eff4f9",
    "toolbar_hover": "#e1effd",
    "toolbar_pressed": "#c9e1fa",
}

BUTTON_STATE_COLORS = {
    "default": COLORS["button"],
    "hover": COLORS["button_hover"],
    "pressed": COLORS["button_pressed"],
    "disabled": COLORS["button_disabled"],
}


def create_root():
    """Create the stable Tk shell used by the light CTk/ttk hybrid UI."""
    if CUSTOMTKINTER_AVAILABLE:
        ctk.set_appearance_mode("light")
        ctk.set_default_color_theme("blue")
    return tk.Tk()


def center_window(window, parent=None):
    """Center a dialog over its parent after Tk has measured its contents."""
    window.update_idletasks()
    width = max(1, window.winfo_reqwidth())
    height = max(1, window.winfo_reqheight())
    parent = parent or window.master
    if parent is not None and parent.winfo_exists():
        x = parent.winfo_rootx() + max(0, (parent.winfo_width() - width) // 2)
        y = parent.winfo_rooty() + max(0, (parent.winfo_height() - height) // 2)
    else:
        x = max(0, (window.winfo_screenwidth() - width) // 2)
        y = max(0, (window.winfo_screenheight() - height) // 2)
    window.geometry(f"{width}x{height}+{x}+{y}")


if CUSTOMTKINTER_AVAILABLE:
    class _CompatCTkButton(ctk.CTkButton):
        """CTk button retaining Tk's widely-used ``config`` alias."""

        def config(self, *args, **kwargs):
            state = kwargs.get("state")
            if state is not None and "fg_color" not in kwargs:
                disabled = str(state) == "disabled"
                kwargs["fg_color"] = (
                    self._macro_disabled_color if disabled else self._macro_enabled_color
                )
                kwargs["hover_color"] = (
                    self._macro_disabled_color if disabled else self._macro_hover_color
                )
            return self.configure(*args, **kwargs)


def action_button(parent, text, command, kind="primary", state="normal", width=None):
    """Create a rounded CustomTkinter action button with a ttk fallback."""
    if kind not in {"primary", "danger"}:
        raise ValueError(f"Unknown action button kind: {kind}")
    if CUSTOMTKINTER_AVAILABLE:
        color = COLORS["accent"] if kind == "primary" else COLORS["danger"]
        hover = COLORS["accent_hover"] if kind == "primary" else COLORS["danger_hover"]
        button = _CompatCTkButton(
            parent,
            text=text,
            command=command,
            state=state,
            width=width or 96,
            height=38,
            corner_radius=9,
            border_width=0,
            fg_color=COLORS["button_disabled"] if state == "disabled" else color,
            hover_color=COLORS["button_disabled"] if state == "disabled" else hover,
            text_color="#ffffff",
            text_color_disabled=COLORS["muted"],
            font=("Segoe UI Semibold", 10),
        )
        button._macro_enabled_color = color
        button._macro_hover_color = hover
        button._macro_disabled_color = COLORS["button_disabled"]
        return button
    style = "Primary.TButton" if kind == "primary" else "Danger.TButton"
    options = {
        "text": text,
        "command": command,
        "state": state,
        "style": style,
    }
    if width is not None:
        options["width"] = max(1, int(width / 9))
    return ttk.Button(parent, **options)


class StatusPulse:
    """A subtle, cancel-safe status animation driven by Tk's event loop."""

    def __init__(self, widget, styles, interval_ms=650):
        self.widget = widget
        self.styles = tuple(styles)
        self.interval_ms = max(100, int(interval_ms))
        self._after_id = None
        self._index = 0

    def start(self):
        if self._after_id is not None or not self.styles:
            return
        self._index = 0
        self._tick()

    def _tick(self):
        try:
            self.widget.configure(style=self.styles[self._index])
            self._index = (self._index + 1) % len(self.styles)
            self._after_id = self.widget.after(self.interval_ms, self._tick)
        except tk.TclError:
            self._after_id = None

    def stop(self, final_style=None):
        after_id = self._after_id
        self._after_id = None
        if after_id is not None:
            try:
                self.widget.after_cancel(after_id)
            except tk.TclError:
                pass
        if final_style is not None:
            try:
                self.widget.configure(style=final_style)
            except tk.TclError:
                pass


def _tone_wave(pattern, volume=0.12, sample_rate=22050):
    """Build a short, gently enveloped PCM wave for non-blocking UI cues."""
    frames = bytearray()
    amplitude = max(0.0, min(1.0, float(volume))) * 32767
    for frequency, duration_ms in pattern:
        sample_count = max(1, int(sample_rate * duration_ms / 1000))
        edge = max(1, min(sample_count // 2, int(sample_rate * 0.012)))
        for index in range(sample_count):
            envelope = min(1.0, index / edge, (sample_count - index - 1) / edge)
            sample = int(
                amplitude
                * max(0.0, envelope)
                * math.sin(2.0 * math.pi * frequency * index / sample_rate)
            )
            frames.extend(struct.pack("<h", sample))
        frames.extend(b"\x00\x00" * int(sample_rate * 0.018))
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(frames)
    return output.getvalue()


class UiFeedback:
    """Coalesced, low-volume interface sounds that never block Tk."""

    PATTERNS = {
        "start": ((587, 55), (784, 85)),
        "stop": ((587, 55), (392, 90)),
        "success": ((659, 55), (880, 95)),
        "error": ((311, 90), (233, 125)),
    }

    def __init__(self, enabled=True):
        self.enabled = bool(enabled)
        self._condition = threading.Condition()
        self._pending = None
        self._worker = None
        self._waves = {}

    def play(self, cue):
        if not self.enabled or winsound is None or cue not in self.PATTERNS:
            return
        with self._condition:
            self._pending = cue
            if self._worker is not None and self._worker.is_alive():
                return
            self._worker = threading.Thread(target=self._run, daemon=True)
            self._worker.start()

    def _run(self):
        while True:
            with self._condition:
                cue = self._pending
                self._pending = None
                if cue is None:
                    self._worker = None
                    return
            try:
                sound = self._waves.get(cue)
                if sound is None:
                    sound = _tone_wave(self.PATTERNS[cue])
                    self._waves[cue] = sound
                winsound.PlaySound(
                    sound,
                    winsound.SND_MEMORY | winsound.SND_NODEFAULT,
                )
            except (RuntimeError, OSError):
                with self._condition:
                    self._worker = None
                return


def configure_theme(root):
    if CUSTOMTKINTER_AVAILABLE:
        ctk.set_appearance_mode("light")
    root.configure(background=COLORS["app"])
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    style.configure(".", font=("Segoe UI", 10), background=COLORS["app"], foreground=COLORS["text"])
    style.configure("TFrame", background=COLORS["app"])
    style.configure("Surface.TFrame", background=COLORS["surface"])
    style.configure("Toolbar.TFrame", background=COLORS["surface"])
    style.configure(
        "Card.TFrame",
        background=COLORS["surface"],
        bordercolor=COLORS["border"],
        lightcolor=COLORS["border"],
        darkcolor=COLORS["border"],
        borderwidth=1,
        relief="solid",
    )
    style.configure("TLabel", background=COLORS["app"], foreground=COLORS["text"])
    style.configure("Surface.TLabel", background=COLORS["surface"], foreground=COLORS["text"])
    style.configure("Muted.TLabel", background=COLORS["surface"], foreground=COLORS["muted"])
    style.configure("Title.TLabel", background=COLORS["surface"], foreground=COLORS["text"], font=("Segoe UI Semibold", 15))
    style.configure("Section.TLabel", background=COLORS["surface"], foreground=COLORS["text"], font=("Segoe UI Semibold", 11))
    style.configure("Status.TLabel", background=COLORS["surface"], foreground=COLORS["muted"], font=("Segoe UI Semibold", 10))

    style.configure(
        "TButton",
        background=BUTTON_STATE_COLORS["default"],
        foreground=COLORS["text"],
        bordercolor=COLORS["border"],
        lightcolor=BUTTON_STATE_COLORS["default"],
        darkcolor=BUTTON_STATE_COLORS["default"],
        padding=(11, 7),
        relief="flat",
    )
    style.map(
        "TButton",
        background=[
            ("disabled", BUTTON_STATE_COLORS["disabled"]),
            ("pressed", BUTTON_STATE_COLORS["pressed"]),
            ("active", BUTTON_STATE_COLORS["hover"]),
        ],
        foreground=[("disabled", COLORS["muted"])],
        bordercolor=[("focus", COLORS["accent"]), ("active", COLORS["button_pressed"])],
        lightcolor=[
            ("pressed", BUTTON_STATE_COLORS["pressed"]),
            ("active", BUTTON_STATE_COLORS["hover"]),
        ],
        darkcolor=[
            ("pressed", BUTTON_STATE_COLORS["pressed"]),
            ("active", BUTTON_STATE_COLORS["hover"]),
        ],
    )
    style.configure("Toolbar.TButton", background=COLORS["surface"], padding=(9, 6))
    style.map(
        "Toolbar.TButton",
        background=[
            ("disabled", COLORS["surface"]),
            ("pressed", COLORS["toolbar_pressed"]),
            ("active", COLORS["toolbar_hover"]),
        ],
    )
    style.configure("Primary.TButton", background=COLORS["accent"], foreground="#ffffff", padding=(14, 8))
    style.map(
        "Primary.TButton",
        background=[
            ("disabled", COLORS["border"]),
            ("pressed", COLORS["accent_pressed"]),
            ("active", COLORS["accent_hover"]),
        ],
        foreground=[("disabled", COLORS["muted"]), ("!disabled", "#ffffff")],
    )
    style.configure("Danger.TButton", background=COLORS["danger"], foreground="#ffffff", padding=(14, 8))
    style.map(
        "Danger.TButton",
        background=[
            ("disabled", COLORS["border"]),
            ("pressed", COLORS["danger_pressed"]),
            ("active", COLORS["danger_hover"]),
        ],
        foreground=[("disabled", COLORS["muted"]), ("!disabled", "#ffffff")],
    )
    style.configure("Icon.TButton", padding=(7, 5), width=3)
    style.configure("Disclosure.TButton", background=COLORS["surface"], foreground=COLORS["text"], anchor="w", padding=(4, 6))
    style.map(
        "Disclosure.TButton",
        background=[("pressed", COLORS["button_pressed"]), ("active", COLORS["button_hover"])],
    )

    style.configure("TEntry", fieldbackground=COLORS["surface"], padding=7)
    style.configure("TCombobox", fieldbackground=COLORS["surface"], padding=6)
    style.configure("TSpinbox", fieldbackground=COLORS["surface"], padding=6)
    style.configure("TCheckbutton", background=COLORS["surface"], padding=(3, 5))
    style.map("TCheckbutton", background=[("active", COLORS["accent_soft"])])
    style.configure("TNotebook", background=COLORS["app"], borderwidth=0, tabmargins=(12, 10, 12, 0))
    style.configure("TNotebook.Tab", padding=(20, 10), font=("Segoe UI Semibold", 10))
    style.map(
        "TNotebook.Tab",
        background=[("selected", COLORS["surface"]), ("active", COLORS["surface_alt"])],
        foreground=[("selected", COLORS["accent"]), ("active", COLORS["text"])],
    )
    style.configure("TPanedwindow", background=COLORS["border"], sashwidth=5)
    style.configure(
        "Vertical.TScrollbar",
        background=COLORS["button"],
        troughcolor=COLORS["surface_alt"],
        bordercolor=COLORS["surface"],
        arrowcolor=COLORS["muted"],
    )
    style.map(
        "Vertical.TScrollbar",
        background=[("pressed", COLORS["button_pressed"]), ("active", COLORS["button_hover"])],
    )
    style.configure(
        "Horizontal.TScale",
        background=COLORS["surface"],
        troughcolor=COLORS["surface_alt"],
        sliderrelief="flat",
    )

    style.configure(
        "Treeview",
        background=COLORS["surface"],
        fieldbackground=COLORS["surface"],
        foreground=COLORS["text"],
        bordercolor=COLORS["border"],
        rowheight=34,
    )
    style.map("Treeview", background=[("selected", COLORS["accent_soft"])], foreground=[("selected", COLORS["text"])])
    style.configure("Treeview.Heading", background=COLORS["surface_alt"], foreground=COLORS["muted"], relief="flat", padding=(9, 8), font=("Segoe UI Semibold", 9))
    style.map("Treeview.Heading", background=[("active", COLORS["border"])])

    style.configure("TLabelframe", background=COLORS["surface"], bordercolor=COLORS["border"], padding=10)
    style.configure("TLabelframe.Label", background=COLORS["surface"], foreground=COLORS["text"], font=("Segoe UI Semibold", 9))
    style.configure("Horizontal.TSeparator", background=COLORS["border"])

    style.configure("Watching.Status.TLabel", background=COLORS["surface"], foreground=COLORS["success"], font=("Segoe UI Semibold", 10))
    style.configure("WatchingPulse.Status.TLabel", background=COLORS["surface"], foreground=COLORS["success_bright"], font=("Segoe UI Semibold", 10))
    style.configure("Idle.Status.TLabel", background=COLORS["surface"], foreground=COLORS["muted"], font=("Segoe UI Semibold", 10))
    style.configure("Error.Status.TLabel", background=COLORS["surface"], foreground=COLORS["danger"], font=("Segoe UI Semibold", 10))

    root.bind_class(
        "TButton",
        "<Enter>",
        lambda event: event.widget.configure(cursor="hand2"),
        add="+",
    )
    root.bind_class(
        "TButton",
        "<Leave>",
        lambda event: event.widget.configure(cursor=""),
        add="+",
    )
    return style


class CollapsibleSection(ttk.Frame):
    def __init__(self, parent, title, expanded=False, style="Surface.TFrame"):
        super().__init__(parent, style=style)
        self.title = title
        self.expanded = bool(expanded)
        self.columnconfigure(0, weight=1)
        self.toggle_button = ttk.Button(
            self,
            style="Disclosure.TButton",
            command=self.toggle,
        )
        self.toggle_button.grid(row=0, column=0, sticky="ew")
        self.content = ttk.Frame(self, style=style, padding=(18, 2, 4, 8))
        self._render()

    def _render(self):
        marker = "\u25be" if self.expanded else "\u25b8"
        self.toggle_button.configure(text=f"{marker}  {self.title}")
        if self.expanded:
            self.content.grid(row=1, column=0, sticky="ew")
        else:
            self.content.grid_remove()

    def toggle(self):
        self.expanded = not self.expanded
        self._render()

    def set_expanded(self, expanded):
        self.expanded = bool(expanded)
        self._render()


class Tooltip:
    def __init__(self, widget, text, delay=500):
        self.widget = widget
        self.text = text
        self.delay = delay
        self._after_id = None
        self._window = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event=None):
        self._cancel()
        self._after_id = self.widget.after(self.delay, self._show)

    def _cancel(self):
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None

    def _show(self):
        if self._window is not None or not self.text:
            return
        try:
            x = self.widget.winfo_rootx() + 8
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 5
        except tk.TclError:
            return
        self._window = tk.Toplevel(self.widget)
        self._window.wm_overrideredirect(True)
        self._window.wm_geometry(f"+{x}+{y}")
        tk.Label(
            self._window,
            text=self.text,
            background=COLORS["surface_warm"],
            foreground=COLORS["text"],
            relief="solid",
            borderwidth=1,
            padx=7,
            pady=4,
            wraplength=320,
            justify="left",
            font=("Segoe UI", 9),
        ).pack()
        self._window.update_idletasks()
        width = self._window.winfo_reqwidth()
        height = self._window.winfo_reqheight()
        screen_width = self.widget.winfo_screenwidth()
        screen_height = self.widget.winfo_screenheight()
        x = min(max(4, x), max(4, screen_width - width - 4))
        y = min(max(4, y), max(4, screen_height - height - 4))
        self._window.wm_geometry(f"+{x}+{y}")

    def _hide(self, _event=None):
        self._cancel()
        if self._window is not None:
            try:
                self._window.destroy()
            except tk.TclError:
                pass
            self._window = None


def condition_choices(conditions, include_blank=False, blank_label="None"):
    choices = [f"{index}: {os.path.basename(condition.template_path) or 'Unnamed condition'}"
               for index, condition in enumerate(conditions or [])]
    return ([blank_label] + choices) if include_blank else choices


def condition_choice_for_index(conditions, index, blank_label="None"):
    if index is None:
        return blank_label
    choices = condition_choices(conditions)
    return choices[index] if 0 <= index < len(choices) else str(index)


def condition_index_from_choice(value, field_name, allow_blank=False):
    text = str(value or "").strip()
    if allow_blank and (not text or text.lower() in {"none", "automatic target"}):
        return None
    prefix = text.split(":", 1)[0].strip()
    try:
        return int(prefix)
    except ValueError as exc:
        raise ValueError(f"{field_name} must select a condition.") from exc


def condition_name(conditions, index, fallback="Automatic target"):
    if index is None:
        return fallback
    if 0 <= index < len(conditions or []):
        return os.path.basename(conditions[index].template_path) or f"Condition {index + 1}"
    return f"Missing condition {index + 1}"


def row_max_level_editor_state(action):
    if has_smart_rally_team_prefilter(action):
        return "disabled", "Controlled by Team 1 / Team 3"
    return "normal", "Max level"


def row_advanced_options_configured(action):
    return any(
        (
            has_smart_rally_team_prefilter(action),
            action.row_tolerance != 60,
            action.offset_x != 0,
            action.offset_y != 0,
            action.min_level is not None,
            action.max_level is not None,
            action.level_roi is not None,
            action.no_match_condition_index is not None,
            bool(action.no_match_disable_steps),
            action.pre_click_delay > 0.0,
        )
    )


def _team_limit_summary(max_level):
    return "unlimited" if max_level is None else f"max level {max_level}"


def action_display_summary(action, conditions):
    if action.type == "click":
        if action.x is not None and action.y is not None:
            target = f"point ({action.x}, {action.y})"
        else:
            target = condition_name(conditions, action.on_condition_index)
        return f"Click {target}"
    if action.type == "click_matching_row":
        reference = condition_name(conditions, action.match_condition_index, "Unselected row")
        target = condition_name(conditions, action.on_condition_index, "Unselected target")
        rows = "all rows" if action.row_mode == "all" else "first row"
        level = ""
        if action.min_level is not None or action.max_level is not None:
            low = "any" if action.min_level is None else action.min_level
            high = "any" if action.max_level is None else action.max_level
            level = f", levels {low}-{high}"
        delay = (
            f", wait {action.pre_click_delay:g}s after level check"
            if getattr(action, "pre_click_delay", 0.0)
            else ""
        )
        availability = (
            ", adapt to idle Team 1/3"
            if getattr(action, "team1_busy_template_path", "")
            and getattr(action, "team3_busy_template_path", "")
            else ""
        )
        return (
            f"Click {target} on {rows} matching {reference}"
            f"{level}{delay}{availability}"
        )
    if action.type == "select_rally_team":
        anchor = condition_name(
            conditions,
            action.on_condition_index,
            "Unselected anchor",
        )
        return (
            f"Select idle Team 3 ({_team_limit_summary(action.team3_max_level)}), "
            f"then Team 1 ({_team_limit_summary(action.team1_max_level)}), "
            f"anchored to {anchor}"
        )
    if action.type == "key":
        return f"Press {action.key or 'key'}"
    if action.type == "wait":
        return f"Wait {action.seconds:g}s"
    if action.type == "set_step":
        verb = "Enable" if action.set_enabled else "Disable"
        return f"{verb} {action.step_name or 'step'}"
    return action.summary()


def preserved_level_roi(original_roi, advanced_opened, values):
    if original_roi is None and not advanced_opened:
        return None
    return [int(value) for value in values]
