import os
import tkinter as tk
from tkinter import ttk

COLORS = {
    "app": "#f3f5f7",
    "surface": "#ffffff",
    "surface_alt": "#eef2f4",
    "border": "#d5dde2",
    "text": "#202a32",
    "muted": "#66737d",
    "accent": "#176b63",
    "accent_hover": "#12574f",
    "accent_soft": "#dcefeb",
    "danger": "#b42318",
    "danger_hover": "#8f1c13",
    "warning": "#a15c00",
    "success": "#25724a",
    "button": "#e4e9ec",
    "button_hover": "#cfd8dd",
    "button_pressed": "#b8c5cb",
    "button_disabled": "#edf0f2",
    "toolbar_hover": "#dbe3e7",
    "toolbar_pressed": "#c4d0d6",
}

BUTTON_STATE_COLORS = {
    "default": COLORS["button"],
    "hover": COLORS["button_hover"],
    "pressed": COLORS["button_pressed"],
    "disabled": COLORS["button_disabled"],
}


def configure_theme(root):
    root.configure(background=COLORS["app"])
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    style.configure(".", font=("Segoe UI", 9), background=COLORS["app"], foreground=COLORS["text"])
    style.configure("TFrame", background=COLORS["app"])
    style.configure("Surface.TFrame", background=COLORS["surface"])
    style.configure("Toolbar.TFrame", background=COLORS["surface"])
    style.configure("TLabel", background=COLORS["app"], foreground=COLORS["text"])
    style.configure("Surface.TLabel", background=COLORS["surface"], foreground=COLORS["text"])
    style.configure("Muted.TLabel", background=COLORS["surface"], foreground=COLORS["muted"])
    style.configure("Title.TLabel", background=COLORS["surface"], foreground=COLORS["text"], font=("Segoe UI Semibold", 14))
    style.configure("Section.TLabel", background=COLORS["surface"], foreground=COLORS["text"], font=("Segoe UI Semibold", 10))
    style.configure("Status.TLabel", background=COLORS["surface"], foreground=COLORS["muted"], font=("Segoe UI Semibold", 9))

    style.configure(
        "TButton",
        background=BUTTON_STATE_COLORS["default"],
        foreground=COLORS["text"],
        bordercolor=COLORS["border"],
        lightcolor=BUTTON_STATE_COLORS["default"],
        darkcolor=BUTTON_STATE_COLORS["default"],
        padding=(10, 6),
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
    style.configure("Toolbar.TButton", background=COLORS["surface"], padding=(8, 5))
    style.map(
        "Toolbar.TButton",
        background=[
            ("disabled", COLORS["surface"]),
            ("pressed", COLORS["toolbar_pressed"]),
            ("active", COLORS["toolbar_hover"]),
        ],
    )
    style.configure("Primary.TButton", background=COLORS["accent"], foreground="#ffffff", padding=(12, 7))
    style.map(
        "Primary.TButton",
        background=[
            ("disabled", COLORS["border"]),
            ("pressed", "#0c4842"),
            ("active", COLORS["accent_hover"]),
        ],
        foreground=[("disabled", COLORS["muted"]), ("!disabled", "#ffffff")],
    )
    style.configure("Danger.TButton", background=COLORS["danger"], foreground="#ffffff", padding=(12, 7))
    style.map(
        "Danger.TButton",
        background=[
            ("disabled", COLORS["border"]),
            ("pressed", "#72150f"),
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

    style.configure("TEntry", fieldbackground=COLORS["surface"], padding=5)
    style.configure("TCombobox", fieldbackground=COLORS["surface"], padding=4)
    style.configure("TCheckbutton", background=COLORS["surface"], padding=(2, 3))
    style.map("TCheckbutton", background=[("active", COLORS["accent_soft"])])
    style.configure("TNotebook", background=COLORS["app"], borderwidth=0, tabmargins=(8, 8, 8, 0))
    style.configure("TNotebook.Tab", padding=(16, 8), font=("Segoe UI Semibold", 9))
    style.map("TNotebook.Tab", background=[("selected", COLORS["surface"]), ("active", COLORS["surface_alt"])])
    style.configure("TPanedwindow", background=COLORS["border"], sashwidth=5)

    style.configure(
        "Treeview",
        background=COLORS["surface"],
        fieldbackground=COLORS["surface"],
        foreground=COLORS["text"],
        bordercolor=COLORS["border"],
        rowheight=30,
    )
    style.map("Treeview", background=[("selected", COLORS["accent_soft"])], foreground=[("selected", COLORS["text"])])
    style.configure("Treeview.Heading", background=COLORS["surface_alt"], foreground=COLORS["muted"], relief="flat", padding=(8, 6), font=("Segoe UI Semibold", 9))
    style.map("Treeview.Heading", background=[("active", COLORS["border"])])

    style.configure("TLabelframe", background=COLORS["surface"], bordercolor=COLORS["border"], padding=10)
    style.configure("TLabelframe.Label", background=COLORS["surface"], foreground=COLORS["text"], font=("Segoe UI Semibold", 9))
    style.configure("Horizontal.TSeparator", background=COLORS["border"])

    style.configure("Watching.Status.TLabel", background=COLORS["surface"], foreground=COLORS["success"], font=("Segoe UI Semibold", 9))
    style.configure("Idle.Status.TLabel", background=COLORS["surface"], foreground=COLORS["muted"], font=("Segoe UI Semibold", 9))
    style.configure("Error.Status.TLabel", background=COLORS["surface"], foreground=COLORS["danger"], font=("Segoe UI Semibold", 9))

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
            self.widget.after_cancel(self._after_id)
            self._after_id = None

    def _show(self):
        if self._window is not None or not self.text:
            return
        x = self.widget.winfo_rootx() + 8
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 5
        self._window = tk.Toplevel(self.widget)
        self._window.wm_overrideredirect(True)
        self._window.wm_geometry(f"+{x}+{y}")
        tk.Label(
            self._window,
            text=self.text,
            background="#fff8d8",
            foreground=COLORS["text"],
            relief="solid",
            borderwidth=1,
            padx=7,
            pady=4,
            font=("Segoe UI", 8),
        ).pack()

    def _hide(self, _event=None):
        self._cancel()
        if self._window is not None:
            self._window.destroy()
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
        return f"Click {target} on {rows} matching {reference}{level}{delay}"
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
