"""
PC Macro Builder -- main application.

A scenario is a list of Steps. Each Step has Conditions (images that
must/must-not be on screen) and Actions (click / key / wait / enable
or disable another step). Run it, watch the log, stop with the button
or your kill-switch key.
"""
import os
import queue
import tkinter as tk
from datetime import datetime
from tkinter import ttk, filedialog, messagebox, simpledialog
import keyboard
import mss
from PIL import ImageDraw, ImageTk

from models import (
    Scenario, Step, ImageCondition, Action,
    list_scenarios, load_scenario, save_scenario, delete_scenario,
    validate_scenario_name,
)
from capture_tool import capture_template, select_region
from engine import MacroEngine, _WINDOW_UNAVAILABLE
from log_maintenance import (
    DEFAULT_DEBUG_MAX_AGE_DAYS,
    DEFAULT_DEBUG_MAX_FILES,
    DEFAULT_LOG_BACKUPS,
    DEFAULT_MAX_LOG_BYTES,
    maintain_logs,
    rotate_log_file,
)
from window_locator import (
    find_window_rect,
    proportional_region_from_window,
    relative_region_from_window,
    resolve_window_region,
    visible_window_titles,
)
from alert_watcher import AlertWatcherFrame
from app_helpers import duplicate_scenario, duplicate_step, duplicate_template_file


def _monitor_box(monitor_index=1):
    with mss.MSS() as sct:
        monitors = sct.monitors
        index = monitor_index if 0 <= monitor_index < len(monitors) else 1
        mon = monitors[index]
        return (mon["left"], mon["top"], mon["width"], mon["height"])


def _parse_optional_int(value, field_name):
    text = str(value).strip()
    if text == "":
        return None
    try:
        return int(text)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a whole number.") from exc


def _parse_required_int(value, field_name):
    parsed = _parse_optional_int(value, field_name)
    if parsed is None:
        raise ValueError(f"{field_name} is required.")
    return parsed


def schedule_mouse_position_fill(win, x_var, y_var, delay_ms=2000):
    """Hide a dialog briefly, then fill vars with the current screen pointer."""
    win.withdraw()

    def capture_pointer():
        try:
            x_var.set(str(win.winfo_pointerx()))
            y_var.set(str(win.winfo_pointery()))
        finally:
            win.deiconify()
            win.lift()
            win.grab_set()

    win.after(delay_ms, capture_pointer)


def resolve_condition_preview_box(cond: ImageCondition, target_window_title="", monitor_index=1,
                                  window_rect_provider=find_window_rect):
    title = (target_window_title or "").strip()
    window_rect = None

    if title or cond.region_mode == "window":
        if not title:
            raise RuntimeError("Window-relative regions need a target window title.")
        window_rect = window_rect_provider(title)
        if not window_rect:
            return _WINDOW_UNAVAILABLE

    if cond.region:
        if cond.region_mode == "window":
            return resolve_window_region(
                cond.region,
                window_rect,
                cond.region_ratio,
                cond.region_window_size,
            )
        return tuple(cond.region)

    if title:
        return window_rect
    return _monitor_box(monitor_index)


class MultiRegionOverlay(tk.Toplevel):
    def __init__(self, master, boxes, duration_ms=4500):
        super().__init__(master)
        self.title("Search Region Preview")

        with mss.MSS() as sct:
            virtual = sct.monitors[0]

        origin_x, origin_y = virtual["left"], virtual["top"]
        width, height = virtual["width"], virtual["height"]
        self.geometry(f"{width}x{height}+{origin_x}+{origin_y}")
        self.overrideredirect(True)
        self.attributes("-topmost", True)

        transparent = "#123456"
        self.configure(bg=transparent)
        try:
            self.attributes("-transparentcolor", transparent)
        except tk.TclError:
            self.attributes("-alpha", 0.35)

        self.canvas = tk.Canvas(self, bg=transparent, highlightthickness=0, cursor="hand2")
        self.canvas.pack(fill="both", expand=True)

        for box, label, color in boxes:
            x, y, w, h = box
            x0, y0 = x - origin_x, y - origin_y
            x1, y1 = x0 + w, y0 + h
            x0, y0 = max(0, x0), max(0, y0)
            x1, y1 = min(width, x1), min(height, y1)
            if x1 <= x0 or y1 <= y0:
                continue

            self.canvas.create_rectangle(x0, y0, x1, y1, outline="#000000", width=7)
            self.canvas.create_rectangle(x0, y0, x1, y1, outline=color, width=4)
            text_y = y0 - 16 if y0 >= 28 else y1 + 16
            text = f"{label}: {w}x{h} at {x},{y}"
            self.canvas.create_text(
                x0 + 2, text_y, text=text, fill="#000000",
                anchor="w", font=("Segoe UI", 12, "bold"),
            )
            self.canvas.create_text(
                x0, text_y - 2, text=text, fill=color,
                anchor="w", font=("Segoe UI", 12, "bold"),
            )

        self.bind("<Escape>", lambda _event: self.destroy())
        self.canvas.bind("<Button-1>", lambda _event: self.destroy())
        self.after(duration_ms, self._safe_destroy)

    def _safe_destroy(self):
        try:
            self.destroy()
        except tk.TclError:
            pass


# ----------------------------------------------------------------------
# Condition editor dialog
# ----------------------------------------------------------------------

def condition_dialog(parent, cond: ImageCondition = None, monitor_index=1,
                     target_window_title=""):
    win = tk.Toplevel(parent)
    win.title("Edit Condition" if cond else "Add Condition")
    win.grab_set()
    win.resizable(False, False)
    result = {"value": None}

    template_var = tk.StringVar(value=cond.template_path if cond else "")
    confidence_var = tk.DoubleVar(value=cond.confidence if cond else 0.85)
    comparison_template_var = tk.StringVar(value=cond.comparison_template_path if cond else "")
    comparison_margin_var = tk.DoubleVar(value=cond.comparison_margin if cond else 0.03)
    negate_var = tk.BooleanVar(value=cond.negate if cond else False)
    region_holder = {"region": list(cond.region) if (cond and cond.region) else None}
    region_mode_holder = {"mode": cond.region_mode if cond else "screen"}
    region_ratio_holder = {"ratio": list(cond.region_ratio) if (cond and cond.region_ratio) else None}
    region_window_size_holder = {
        "size": list(cond.region_window_size) if (cond and cond.region_window_size) else None
    }

    def format_region_label():
        if not region_holder["region"]:
            return "Target window" if target_window_title else "Full screen"
        scope = "window-relative" if region_mode_holder["mode"] == "window" else "screen"
        return f"{region_holder['region']} ({scope})"

    region_var = tk.StringVar(value=format_region_label())

    pad = {"padx": 6, "pady": 4}

    tk.Label(win, text="Template image:").grid(row=0, column=0, sticky="w", **pad)
    template_entry = tk.Entry(win, textvariable=template_var, width=32)
    template_entry.grid(row=0, column=1, columnspan=2, sticky="we", **pad)

    def browse():
        path = filedialog.askopenfilename(filetypes=[("PNG images", "*.png")], initialdir="templates", parent=win)
        if path:
            template_var.set(path)

    def capture():
        path = capture_template(parent, monitor_index=monitor_index)
        if path:
            template_var.set(path)

    template_browse_btn = tk.Button(win, text="Browse...", command=browse)
    template_browse_btn.grid(row=1, column=1, sticky="we", **pad)
    template_capture_btn = tk.Button(win, text="Capture from screen...", command=capture)
    template_capture_btn.grid(row=1, column=2, sticky="we", **pad)

    tk.Label(win, text="Confidence:").grid(row=2, column=0, sticky="w", **pad)
    tk.Scale(win, variable=confidence_var, from_=0.5, to=1.0, resolution=0.01,
             orient="horizontal", length=220).grid(row=2, column=1, columnspan=2, sticky="w", **pad)

    tk.Label(win, text="Compare against:").grid(row=3, column=0, sticky="w", **pad)
    tk.Entry(win, textvariable=comparison_template_var, width=32).grid(
        row=3, column=1, sticky="we", **pad
    )

    def browse_comparison():
        path = filedialog.askopenfilename(
            filetypes=[("PNG images", "*.png")], initialdir="templates", parent=win
        )
        if path:
            comparison_template_var.set(path)

    tk.Button(win, text="Browse...", command=browse_comparison).grid(
        row=3, column=2, sticky="we", **pad
    )
    tk.Label(win, text="Required score lead:").grid(row=4, column=0, sticky="w", **pad)
    tk.Scale(
        win, variable=comparison_margin_var, from_=0.0, to=0.25, resolution=0.01,
        orient="horizontal", length=220,
    ).grid(row=4, column=1, columnspan=2, sticky="w", **pad)

    tk.Checkbutton(win, text="Negate (succeeds when this image is ABSENT)",
                   variable=negate_var).grid(row=5, column=0, columnspan=3, sticky="w", **pad)

    tk.Label(win, text="Search region:").grid(row=6, column=0, sticky="w", **pad)
    tk.Label(win, textvariable=region_var, fg="#555").grid(row=6, column=1, sticky="w", **pad)

    def pick_region():
        region, _ = select_region(parent, monitor_index=monitor_index)
        if region:
            title = target_window_title.strip()
            if title:
                try:
                    window_rect = find_window_rect(title)
                except Exception as e:
                    messagebox.showerror("Window lookup failed", str(e), parent=win)
                    return
                if not window_rect:
                    messagebox.showerror(
                        "Target window not found",
                        f"No visible window title contains: {title}",
                        parent=win,
                    )
                    return
                region_ratio_holder["ratio"] = list(proportional_region_from_window(region, window_rect))
                region_window_size_holder["size"] = [window_rect[2], window_rect[3]]
                region = relative_region_from_window(region, window_rect)
                region_mode_holder["mode"] = "window"
            else:
                region_mode_holder["mode"] = "screen"
                region_ratio_holder["ratio"] = None
                region_window_size_holder["size"] = None
            region_holder["region"] = list(region)
            region_var.set(format_region_label())

    def clear_region():
        region_holder["region"] = None
        region_mode_holder["mode"] = "screen"
        region_ratio_holder["ratio"] = None
        region_window_size_holder["size"] = None
        region_var.set(format_region_label())

    def show_region():
        temp_cond = ImageCondition(
            template_path=template_var.get(),
            confidence=round(confidence_var.get(), 2),
            comparison_template_path=comparison_template_var.get(),
            comparison_margin=round(comparison_margin_var.get(), 2),
            region=region_holder["region"],
            region_mode=region_mode_holder["mode"],
            region_ratio=region_ratio_holder["ratio"],
            region_window_size=region_window_size_holder["size"],
            negate=negate_var.get(),
        )
        try:
            box = resolve_condition_preview_box(temp_cond, target_window_title, monitor_index)
        except Exception as e:
            messagebox.showerror("Show region failed", str(e), parent=win)
            return
        if box is _WINDOW_UNAVAILABLE:
            messagebox.showerror(
                "Target window not found",
                f"No visible window title contains: {target_window_title.strip()}",
                parent=win,
            )
            return
        label = os.path.basename(template_var.get()) if template_var.get() else "Search region"
        if negate_var.get():
            label = f"NOT {label}"
        MultiRegionOverlay(parent, [(box, label, "#ff9800" if negate_var.get() else "#ffcc00")])

    tk.Button(win, text="Show region", command=show_region).grid(row=7, column=0, sticky="we", **pad)
    tk.Button(win, text="Pick region...", command=pick_region).grid(row=7, column=1, sticky="we", **pad)
    tk.Button(win, text="Clear (full screen)", command=clear_region).grid(row=7, column=2, sticky="we", **pad)

    def on_ok():
        if not template_var.get():
            messagebox.showerror("Missing template", "Choose or capture a template image first.", parent=win)
            return
        result["value"] = ImageCondition(
            template_path=template_var.get(),
            confidence=round(confidence_var.get(), 2),
            comparison_template_path=comparison_template_var.get(),
            comparison_margin=round(comparison_margin_var.get(), 2),
            region=region_holder["region"],
            region_mode=region_mode_holder["mode"],
            region_ratio=region_ratio_holder["ratio"],
            region_window_size=region_window_size_holder["size"],
            negate=negate_var.get(),
        )
        win.destroy()

    btns = tk.Frame(win)
    btns.grid(row=8, column=0, columnspan=3, pady=10)
    tk.Button(btns, text="OK", width=10, command=on_ok).pack(side="left", padx=4)
    tk.Button(btns, text="Cancel", width=10, command=win.destroy).pack(side="left", padx=4)

    win.wait_window()
    return result["value"]


# ----------------------------------------------------------------------
# Action editor dialog
# ----------------------------------------------------------------------

def action_dialog(parent, action: Action = None, step_names=None, num_conditions=0):
    win = tk.Toplevel(parent)
    win.title("Edit Action" if action else "Add Action")
    win.grab_set()
    win.resizable(False, False)
    result = {"value": None}
    a = action or Action(type="click")
    step_names = step_names or []

    action_type_labels = {
        "click": "Click",
        "click_matching_row": "Click matching row",
        "key": "Press key",
        "wait": "Wait",
        "set_step": "Enable / disable step",
    }
    action_type_values = {label: value for value, label in action_type_labels.items()}

    tk.Label(win, text="Action type:").grid(row=0, column=0, sticky="w", padx=6, pady=6)
    type_var = tk.StringVar(value=action_type_labels.get(a.type, "Click"))
    type_combo = ttk.Combobox(win, textvariable=type_var,
                               values=list(action_type_values.keys()),
                               state="readonly", width=22)
    type_combo.grid(row=0, column=1, sticky="w", padx=6, pady=6)

    body = tk.Frame(win)
    body.grid(row=1, column=0, columnspan=2, sticky="we", padx=6, pady=4)

    click_frame = tk.LabelFrame(body, text="Click")
    row_click_frame = tk.LabelFrame(body, text="Click matching row")
    key_frame = tk.LabelFrame(body, text="Key press")
    wait_frame = tk.LabelFrame(body, text="Wait")
    step_frame = tk.LabelFrame(body, text="Enable / disable a step")
    frames = {
        "click": click_frame,
        "click_matching_row": row_click_frame,
        "key": key_frame,
        "wait": wait_frame,
        "set_step": step_frame,
    }

    # --- click fields ---
    cond_idx_var = tk.StringVar(value=str(a.on_condition_index) if a.on_condition_index is not None else "")
    x_var = tk.StringVar(value=str(a.x) if a.x is not None else "")
    y_var = tk.StringVar(value=str(a.y) if a.y is not None else "")
    offx_var = tk.IntVar(value=a.offset_x)
    offy_var = tk.IntVar(value=a.offset_y)
    button_var = tk.StringVar(value=a.button)

    tk.Label(click_frame, text=f"Click on condition # (0-{max(num_conditions - 1, 0)}, blank = first match):"
             ).grid(row=0, column=0, columnspan=2, sticky="w", padx=4, pady=2)
    tk.Entry(click_frame, textvariable=cond_idx_var, width=6).grid(row=1, column=0, sticky="w", padx=4)
    tk.Label(click_frame, text="...OR a fixed point  x:").grid(row=2, column=0, sticky="w", padx=4, pady=(8, 2))
    tk.Entry(click_frame, textvariable=x_var, width=6).grid(row=2, column=1, sticky="w")
    tk.Label(click_frame, text="y:").grid(row=2, column=2, sticky="w")
    tk.Entry(click_frame, textvariable=y_var, width=6).grid(row=2, column=3, sticky="w")
    tk.Button(
        click_frame,
        text="Use mouse position (2s)",
        command=lambda: schedule_mouse_position_fill(win, x_var, y_var),
    ).grid(row=2, column=4, sticky="w", padx=(8, 4))
    tk.Label(click_frame, text="Offset  x:").grid(row=3, column=0, sticky="w", padx=4, pady=2)
    tk.Entry(click_frame, textvariable=offx_var, width=6).grid(row=3, column=1, sticky="w")
    tk.Label(click_frame, text="y:").grid(row=3, column=2, sticky="w")
    tk.Entry(click_frame, textvariable=offy_var, width=6).grid(row=3, column=3, sticky="w")
    tk.Label(click_frame, text="Button:").grid(row=4, column=0, sticky="w", padx=4, pady=2)
    ttk.Combobox(click_frame, textvariable=button_var, values=["left", "right", "middle"],
                 state="readonly", width=8).grid(row=4, column=1, sticky="w")

    # --- click matching row fields ---
    match_idx_var = tk.StringVar(value=str(a.match_condition_index) if a.match_condition_index is not None else "")
    row_cond_idx_var = tk.StringVar(value=str(a.on_condition_index) if a.on_condition_index is not None else "")
    row_tolerance_var = tk.IntVar(value=a.row_tolerance)
    row_mode_var = tk.StringVar(value=a.row_mode)
    target_choice_var = tk.StringVar(value=a.target_choice)
    row_offx_var = tk.IntVar(value=a.offset_x)
    row_offy_var = tk.IntVar(value=a.offset_y)
    row_button_var = tk.StringVar(value=a.button)
    min_level_var = tk.StringVar(value=str(a.min_level) if a.min_level is not None else "")
    max_level_var = tk.StringVar(value=str(a.max_level) if a.max_level is not None else "")
    level_min_digits_var = tk.IntVar(value=max(1, getattr(a, "level_min_digits", 1)))
    level_digit_dir_var = tk.StringVar(value=a.level_digit_template_dir)
    default_level_roi = a.level_roi or [-90, -45, 220, 100]
    level_roi_x_var = tk.IntVar(value=default_level_roi[0])
    level_roi_y_var = tk.IntVar(value=default_level_roi[1])
    level_roi_w_var = tk.IntVar(value=default_level_roi[2])
    level_roi_h_var = tk.IntVar(value=default_level_roi[3])
    no_match_cond_idx_var = tk.StringVar(
        value=str(getattr(a, "no_match_condition_index", ""))
        if getattr(a, "no_match_condition_index", None) is not None else ""
    )
    no_match_disable_steps_var = tk.StringVar(
        value=", ".join(getattr(a, "no_match_disable_steps", []) or [])
    )

    tk.Label(row_click_frame, text=f"Row reference condition # (0-{max(num_conditions - 1, 0)}):"
             ).grid(row=0, column=0, sticky="w", padx=4, pady=2)
    tk.Entry(row_click_frame, textvariable=match_idx_var, width=6).grid(row=0, column=1, sticky="w")
    tk.Label(row_click_frame, text=f"Click condition # (0-{max(num_conditions - 1, 0)}):"
             ).grid(row=1, column=0, sticky="w", padx=4, pady=2)
    tk.Entry(row_click_frame, textvariable=row_cond_idx_var, width=6).grid(row=1, column=1, sticky="w")
    tk.Label(row_click_frame, text="Row tolerance px:").grid(row=2, column=0, sticky="w", padx=4, pady=2)
    tk.Entry(row_click_frame, textvariable=row_tolerance_var, width=6).grid(row=2, column=1, sticky="w")
    tk.Label(row_click_frame, text="Rows:").grid(row=3, column=0, sticky="w", padx=4, pady=2)
    ttk.Combobox(row_click_frame, textvariable=row_mode_var, values=["first", "all"],
                 state="readonly", width=8).grid(row=3, column=1, sticky="w")
    tk.Label(row_click_frame, text="Target choice:").grid(row=4, column=0, sticky="w", padx=4, pady=2)
    ttk.Combobox(row_click_frame, textvariable=target_choice_var,
                 values=["leftmost", "rightmost", "nearest"],
                 state="readonly", width=10).grid(row=4, column=1, sticky="w")
    tk.Label(row_click_frame, text="Offset  x:").grid(row=5, column=0, sticky="w", padx=4, pady=2)
    tk.Entry(row_click_frame, textvariable=row_offx_var, width=6).grid(row=5, column=1, sticky="w")
    tk.Label(row_click_frame, text="y:").grid(row=5, column=2, sticky="w")
    tk.Entry(row_click_frame, textvariable=row_offy_var, width=6).grid(row=5, column=3, sticky="w")
    tk.Label(row_click_frame, text="Button:").grid(row=6, column=0, sticky="w", padx=4, pady=2)
    ttk.Combobox(row_click_frame, textvariable=row_button_var, values=["left", "right", "middle"],
                 state="readonly", width=8).grid(row=6, column=1, sticky="w")
    tk.Label(row_click_frame, text="Min level (blank = any):").grid(row=7, column=0, sticky="w", padx=4, pady=(8, 2))
    tk.Entry(row_click_frame, textvariable=min_level_var, width=6).grid(row=7, column=1, sticky="w")
    tk.Label(row_click_frame, text="Max level:").grid(row=7, column=2, sticky="w")
    tk.Entry(row_click_frame, textvariable=max_level_var, width=6).grid(row=7, column=3, sticky="w")
    tk.Label(row_click_frame, text="Min digits:").grid(row=8, column=0, sticky="w", padx=4, pady=2)
    tk.Entry(row_click_frame, textvariable=level_min_digits_var, width=6).grid(row=8, column=1, sticky="w")
    tk.Label(row_click_frame, text="Digit templates:").grid(row=9, column=0, sticky="w", padx=4, pady=2)
    tk.Entry(row_click_frame, textvariable=level_digit_dir_var, width=28).grid(row=9, column=1, columnspan=3, sticky="we")

    def browse_level_digit_dir():
        path = filedialog.askdirectory(initialdir="templates", parent=win)
        if path:
            level_digit_dir_var.set(path)

    tk.Button(row_click_frame, text="Browse...", command=browse_level_digit_dir).grid(row=9, column=4, sticky="w", padx=4)
    tk.Label(row_click_frame, text="Level box rel to row x/y/w/h:").grid(row=10, column=0, sticky="w", padx=4, pady=2)
    tk.Entry(row_click_frame, textvariable=level_roi_x_var, width=6).grid(row=10, column=1, sticky="w")
    tk.Entry(row_click_frame, textvariable=level_roi_y_var, width=6).grid(row=10, column=2, sticky="w")
    tk.Entry(row_click_frame, textvariable=level_roi_w_var, width=6).grid(row=10, column=3, sticky="w")
    tk.Entry(row_click_frame, textvariable=level_roi_h_var, width=6).grid(row=10, column=4, sticky="w")
    tk.Label(row_click_frame, text="If no valid row, click condition #:").grid(row=11, column=0, sticky="w", padx=4, pady=(8, 2))
    tk.Entry(row_click_frame, textvariable=no_match_cond_idx_var, width=6).grid(row=11, column=1, sticky="w")
    tk.Label(row_click_frame, text="Then disable steps:").grid(row=12, column=0, sticky="w", padx=4, pady=2)
    tk.Entry(row_click_frame, textvariable=no_match_disable_steps_var, width=34).grid(row=12, column=1, columnspan=4, sticky="we")

    # --- key fields ---
    key_var = tk.StringVar(value=a.key)
    hold_var = tk.DoubleVar(value=a.hold)
    tk.Label(key_frame, text="Key name (e.g. space, f, enter, esc):").grid(row=0, column=0, sticky="w", padx=4, pady=2)
    tk.Entry(key_frame, textvariable=key_var, width=14).grid(row=0, column=1, sticky="w")
    tk.Label(key_frame, text="Hold (s), 0 = quick tap:").grid(row=1, column=0, sticky="w", padx=4, pady=2)
    tk.Entry(key_frame, textvariable=hold_var, width=6).grid(row=1, column=1, sticky="w")

    # --- wait fields ---
    seconds_var = tk.DoubleVar(value=a.seconds)
    tk.Label(wait_frame, text="Seconds:").grid(row=0, column=0, sticky="w", padx=4, pady=2)
    tk.Entry(wait_frame, textvariable=seconds_var, width=6).grid(row=0, column=1, sticky="w")

    # --- set_step fields ---
    step_name_var = tk.StringVar(value=a.step_name)
    set_enabled_var = tk.BooleanVar(value=a.set_enabled)
    tk.Label(step_frame, text="Step:").grid(row=0, column=0, sticky="w", padx=4, pady=2)
    ttk.Combobox(step_frame, textvariable=step_name_var, values=step_names, width=22).grid(row=0, column=1, sticky="w")
    tk.Checkbutton(step_frame, text="Enable (unchecked = disable)",
                   variable=set_enabled_var).grid(row=1, column=0, columnspan=2, sticky="w", padx=4)

    def show_frame(*_):
        for f in frames.values():
            f.grid_forget()
        action_type = action_type_values.get(type_var.get(), "click")
        frames[action_type].grid(row=0, column=0, sticky="we")

    type_combo.bind("<<ComboboxSelected>>", show_frame)
    show_frame()

    def on_ok():
        t = action_type_values.get(type_var.get(), "click")
        new_action = Action(type=t)
        try:
            if t == "click":
                new_action.on_condition_index = _parse_optional_int(
                    cond_idx_var.get(),
                    "Click condition",
                )
                new_action.x = _parse_optional_int(x_var.get(), "Fixed x")
                new_action.y = _parse_optional_int(y_var.get(), "Fixed y")
                new_action.offset_x = offx_var.get()
                new_action.offset_y = offy_var.get()
                new_action.button = button_var.get()
            elif t == "click_matching_row":
                mi = match_idx_var.get().strip()
                ci = row_cond_idx_var.get().strip()
                if mi == "" or ci == "":
                    messagebox.showerror(
                        "Missing condition",
                        "Enter both the row reference condition and click condition.",
                        parent=win,
                    )
                    return
                new_action.match_condition_index = _parse_required_int(mi, "Row reference condition")
                new_action.on_condition_index = _parse_required_int(ci, "Click condition")
                new_action.row_tolerance = row_tolerance_var.get()
                new_action.row_mode = row_mode_var.get()
                new_action.target_choice = target_choice_var.get()
                new_action.offset_x = row_offx_var.get()
                new_action.offset_y = row_offy_var.get()
                new_action.button = row_button_var.get()
                new_action.min_level = _parse_optional_int(min_level_var.get(), "Min level")
                new_action.max_level = _parse_optional_int(max_level_var.get(), "Max level")
                new_action.level_digit_template_dir = level_digit_dir_var.get().strip()
                new_action.level_min_digits = max(
                    1,
                    _parse_required_int(level_min_digits_var.get(), "Min digits"),
                )
                new_action.level_roi = [
                    level_roi_x_var.get(),
                    level_roi_y_var.get(),
                    level_roi_w_var.get(),
                    level_roi_h_var.get(),
                ]
                new_action.no_match_condition_index = _parse_optional_int(
                    no_match_cond_idx_var.get(),
                    "No-match condition",
                )
                new_action.no_match_disable_steps = [
                    name.strip()
                    for name in no_match_disable_steps_var.get().split(",")
                    if name.strip()
                ]
            elif t == "key":
                if not key_var.get().strip():
                    messagebox.showerror("Missing key", "Enter a key name.", parent=win)
                    return
                new_action.key = key_var.get().strip()
                new_action.hold = hold_var.get()
            elif t == "wait":
                new_action.seconds = seconds_var.get()
            elif t == "set_step":
                if not step_name_var.get().strip():
                    messagebox.showerror("Missing step", "Choose a step name.", parent=win)
                    return
                new_action.step_name = step_name_var.get().strip()
                new_action.set_enabled = set_enabled_var.get()
        except ValueError as exc:
            messagebox.showerror("Invalid number", str(exc), parent=win)
            return
        result["value"] = new_action
        win.destroy()

    btns = tk.Frame(win)
    btns.grid(row=2, column=0, columnspan=2, pady=10)
    tk.Button(btns, text="OK", width=10, command=on_ok).pack(side="left", padx=4)
    tk.Button(btns, text="Cancel", width=10, command=win.destroy).pack(side="left", padx=4)

    win.wait_window()
    return result["value"]


# ----------------------------------------------------------------------
# Step editor dialog
# ----------------------------------------------------------------------

def step_dialog(parent, step: Step = None, existing_names=None, all_step_names=None,
                monitor_index=1, target_window_title=""):
    win = tk.Toplevel(parent)
    win.title("Edit Step" if step else "Add Step")
    win.grab_set()
    win.resizable(False, False)
    result = {"value": None}

    s = step or Step(name="")
    conditions = list(s.conditions)
    actions = list(s.actions)
    existing_names = existing_names or set()
    all_step_names = all_step_names or []

    pad = {"padx": 6, "pady": 4}

    name_var = tk.StringVar(value=s.name)
    enabled_var = tk.BooleanVar(value=s.enabled)
    operator_var = tk.StringVar(value=s.condition_operator)
    cooldown_var = tk.DoubleVar(value=s.cooldown)
    repeatable_var = tk.BooleanVar(value=s.repeatable)

    tk.Label(win, text="Step name:").grid(row=0, column=0, sticky="w", **pad)
    tk.Entry(win, textvariable=name_var, width=24).grid(row=0, column=1, sticky="w", **pad)
    tk.Checkbutton(win, text="Enabled at scenario start", variable=enabled_var).grid(
        row=0, column=2, columnspan=2, sticky="w", **pad)

    tk.Label(win, text="Conditions must match:").grid(row=1, column=0, sticky="w", **pad)
    ttk.Combobox(win, textvariable=operator_var, values=["AND", "OR"], state="readonly", width=6).grid(
        row=1, column=1, sticky="w", **pad)
    tk.Label(win, text="Cooldown (s):").grid(row=1, column=2, sticky="w", **pad)
    tk.Entry(win, textvariable=cooldown_var, width=6).grid(row=1, column=3, sticky="w", **pad)
    tk.Checkbutton(win, text="Repeatable (keep firing)", variable=repeatable_var).grid(
        row=1, column=4, sticky="w", **pad)

    tk.Label(win, text="Conditions:").grid(row=2, column=0, sticky="w", **pad)
    cond_listbox = tk.Listbox(win, width=70, height=5)
    cond_listbox.grid(row=3, column=0, columnspan=5, sticky="we", padx=6)

    def refresh_conditions():
        cond_listbox.delete(0, tk.END)
        for i, c in enumerate(conditions):
            tag = "NOT " if c.negate else ""
            subject = os.path.basename(c.template_path)
            if not c.region:
                region_txt = "target window" if target_window_title else "full screen"
            else:
                scope = "window" if c.region_mode == "window" else "screen"
                region_txt = f"{scope} region {tuple(c.region)}"
            comparison_txt = ""
            if c.comparison_template_path:
                rival = os.path.basename(c.comparison_template_path)
                comparison_txt = f", beats {rival} by {c.comparison_margin:.2f}"
            cond_listbox.insert(
                tk.END,
                f"[{i}] {tag}{subject}  "
                f"(conf {c.confidence}{comparison_txt}, {region_txt})",
            )
    refresh_conditions()

    def add_condition():
        c = condition_dialog(
            win,
            monitor_index=monitor_index,
            target_window_title=target_window_title,
        )
        if c:
            conditions.append(c)
            refresh_conditions()

    def edit_condition():
        sel = cond_listbox.curselection()
        if not sel:
            return
        c = condition_dialog(
            win,
            cond=conditions[sel[0]],
            monitor_index=monitor_index,
            target_window_title=target_window_title,
        )
        if c:
            conditions[sel[0]] = c
            refresh_conditions()

    def remove_condition():
        sel = cond_listbox.curselection()
        if sel:
            del conditions[sel[0]]
            refresh_conditions()

    def duplicate_condition_template():
        sel = cond_listbox.curselection()
        if not sel:
            return
        original = conditions[sel[0]]
        if original.condition_type != "template" or not original.template_path:
            messagebox.showinfo("Not a template", "Select a template condition to duplicate its image.", parent=win)
            return
        name = simpledialog.askstring(
            "Duplicate template",
            "New template image name:",
            initialvalue=os.path.splitext(os.path.basename(original.template_path))[0] + "_copy",
            parent=win,
        )
        if not name:
            return
        try:
            new_path = duplicate_template_file(original.template_path, name)
        except Exception as e:
            messagebox.showerror("Duplicate failed", str(e), parent=win)
            return
        copied = ImageCondition.from_dict(original.to_dict())
        copied.template_path = new_path
        conditions.append(copied)
        refresh_conditions()

    cbtns = tk.Frame(win)
    cbtns.grid(row=4, column=0, columnspan=5, sticky="w", padx=6, pady=(0, 8))
    tk.Button(cbtns, text="Add...", command=add_condition).pack(side="left", padx=2)
    tk.Button(cbtns, text="Edit...", command=edit_condition).pack(side="left", padx=2)
    tk.Button(cbtns, text="Duplicate Template...", command=duplicate_condition_template).pack(side="left", padx=2)
    tk.Button(cbtns, text="Remove", command=remove_condition).pack(side="left", padx=2)

    tk.Label(win, text="Actions (run top to bottom when conditions are met):").grid(
        row=5, column=0, columnspan=3, sticky="w", **pad)
    act_listbox = tk.Listbox(win, width=70, height=5)
    act_listbox.grid(row=6, column=0, columnspan=5, sticky="we", padx=6)

    def refresh_actions():
        act_listbox.delete(0, tk.END)
        for i, act in enumerate(actions):
            act_listbox.insert(tk.END, f"[{i}] {act.summary()}")
    refresh_actions()

    def add_action():
        act = action_dialog(win, step_names=all_step_names, num_conditions=len(conditions))
        if act:
            actions.append(act)
            refresh_actions()

    def edit_action():
        sel = act_listbox.curselection()
        if not sel:
            return
        act = action_dialog(win, action=actions[sel[0]], step_names=all_step_names, num_conditions=len(conditions))
        if act:
            actions[sel[0]] = act
            refresh_actions()

    def remove_action():
        sel = act_listbox.curselection()
        if sel:
            del actions[sel[0]]
            refresh_actions()

    def move_action(delta):
        sel = act_listbox.curselection()
        if not sel:
            return
        idx, new_idx = sel[0], sel[0] + delta
        if 0 <= new_idx < len(actions):
            actions[idx], actions[new_idx] = actions[new_idx], actions[idx]
            refresh_actions()
            act_listbox.selection_set(new_idx)

    abtns = tk.Frame(win)
    abtns.grid(row=7, column=0, columnspan=5, sticky="w", padx=6, pady=(0, 8))
    tk.Button(abtns, text="Add...", command=add_action).pack(side="left", padx=2)
    tk.Button(abtns, text="Edit...", command=edit_action).pack(side="left", padx=2)
    tk.Button(abtns, text="Remove", command=remove_action).pack(side="left", padx=2)
    tk.Button(abtns, text="Move Up", command=lambda: move_action(-1)).pack(side="left", padx=2)
    tk.Button(abtns, text="Move Down", command=lambda: move_action(1)).pack(side="left", padx=2)

    def on_save():
        nm = name_var.get().strip()
        if not nm:
            messagebox.showerror("Missing name", "Enter a step name.", parent=win)
            return
        if nm in existing_names and nm != s.name:
            messagebox.showerror("Duplicate name", "A step with this name already exists.", parent=win)
            return
        result["value"] = Step(
            name=nm, conditions=conditions, actions=actions,
            condition_operator=operator_var.get(), enabled=enabled_var.get(),
            cooldown=cooldown_var.get(), repeatable=repeatable_var.get(),
        )
        win.destroy()

    btns = tk.Frame(win)
    btns.grid(row=8, column=0, columnspan=5, pady=10)
    tk.Button(btns, text="Save", width=10, command=on_save).pack(side="left", padx=4)
    tk.Button(btns, text="Cancel", width=10, command=win.destroy).pack(side="left", padx=4)

    win.wait_window()
    return result["value"]


class App:
    def __init__(self, root):
        self.root = root
        root.title("PC Macro Builder")
        root.geometry("1040x760")
        root.minsize(900, 650)
        self._configure_style()

        self.scenario = Scenario(name="untitled")
        self.engine = None
        self.log_queue = queue.Queue()
        self.control_queue = queue.Queue()
        self._start_hotkey_handle = None
        app_dir = os.path.dirname(os.path.abspath(__file__))
        self.log_dir = os.path.join(app_dir, "logs")
        self.log_file_path = os.path.join(self.log_dir, "pc_macro_builder.log")
        self.log_max_bytes = DEFAULT_MAX_LOG_BYTES
        self.log_backups = DEFAULT_LOG_BACKUPS
        self.debug_max_files = DEFAULT_DEBUG_MAX_FILES
        self.debug_max_age_days = DEFAULT_DEBUG_MAX_AGE_DAYS
        self.log_text_max_lines = 1000
        self._log_line_count = 0
        self._log_file_handle = None
        self._log_write_count = 0
        os.makedirs(self.log_dir, exist_ok=True)
        maintain_logs(
            self.log_dir,
            self.log_file_path,
            self.log_max_bytes,
            self.log_backups,
            self.debug_max_files,
            self.debug_max_age_days,
        )

        self._build_ui()
        self._refresh_scenario_list()
        self._refresh_steps()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._register_start_hotkey()
        self.root.after(150, self._poll_log_queue)
        self._write_log_file("---- app started ----")

    def _configure_style(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TNotebook", padding=4)
        style.configure("TNotebook.Tab", padding=(14, 6))
        style.configure("TLabelframe", padding=6)
        style.configure("TLabelframe.Label", font=("Segoe UI", 9, "bold"))

    # ---- layout ----
    def _build_ui(self):
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True)

        self.macro_tab = tk.Frame(self.notebook)
        self.notebook.add(self.macro_tab, text="Macro Builder")

        self.alert_tab = AlertWatcherFrame(self.notebook, embedded=True)
        self.notebook.add(self.alert_tab, text="Icon Alerts")

        top = ttk.Frame(self.macro_tab, padding=(10, 10, 10, 6))
        top.pack(fill="x", padx=8, pady=6)
        ttk.Label(top, text="Scenario:").pack(side="left")
        self.scenario_var = tk.StringVar()
        self.scenario_combo = ttk.Combobox(top, textvariable=self.scenario_var, state="readonly", width=24)
        self.scenario_combo.pack(side="left", padx=4)
        self.scenario_combo.bind("<<ComboboxSelected>>", self._on_scenario_selected)
        ttk.Button(top, text="New", command=self._new_scenario).pack(side="left", padx=2)
        ttk.Button(top, text="Save", command=self._save_scenario).pack(side="left", padx=2)
        ttk.Button(top, text="Save As...", command=self._save_scenario_as).pack(side="left", padx=2)
        ttk.Button(top, text="Duplicate", command=self._duplicate_scenario).pack(side="left", padx=2)
        ttk.Button(top, text="Delete", command=self._delete_scenario).pack(side="left", padx=2)

        settings = ttk.LabelFrame(self.macro_tab, text="Scenario Settings", padding=8)
        settings.pack(fill="x", padx=12, pady=(0, 6))
        ttk.Label(settings, text="Poll interval (s):").pack(side="left")
        self.poll_var = tk.DoubleVar(value=self.scenario.poll_interval)
        ttk.Entry(settings, textvariable=self.poll_var, width=6).pack(side="left", padx=4)
        ttk.Label(settings, text="Monitor #:").pack(side="left", padx=(10, 0))
        self.monitor_var = tk.IntVar(value=self.scenario.monitor_index)
        ttk.Entry(settings, textvariable=self.monitor_var, width=4).pack(side="left", padx=4)
        ttk.Label(settings, text="Kill switch key:").pack(side="left", padx=(10, 0))
        self.kill_var = tk.StringVar(value=self.scenario.kill_switch)
        ttk.Entry(settings, textvariable=self.kill_var, width=8).pack(side="left", padx=4)

        target = ttk.LabelFrame(self.macro_tab, text="Target Window", padding=8)
        target.pack(fill="x", padx=12, pady=(0, 6))
        ttk.Label(target, text="Target window title contains:").pack(side="left")
        self.target_window_var = tk.StringVar(value=self.scenario.target_window_title)
        self.target_window_combo = ttk.Combobox(target, textvariable=self.target_window_var, width=42)
        self.target_window_combo.pack(side="left", padx=4)
        ttk.Button(target, text="Refresh", command=self._refresh_window_list).pack(side="left", padx=2)
        ttk.Label(target, text="blank = full screen", foreground="#555").pack(side="left")
        self._refresh_window_list()

        mid = ttk.LabelFrame(self.macro_tab, text="Steps", padding=8)
        mid.pack(fill="both", expand=True, padx=12, pady=(0, 6))
        ttk.Label(mid, text="Checked top to bottom every cycle").pack(anchor="w")
        body = ttk.Frame(mid)
        body.pack(fill="both", expand=True)
        self.steps_listbox = tk.Listbox(body, height=10)
        self.steps_listbox.pack(fill="both", expand=True, side="left")
        step_btns = ttk.Frame(body)
        step_btns.pack(side="left", fill="y", padx=6)
        ttk.Button(step_btns, text="Add Step...", width=14, command=self._add_step).pack(pady=2)
        ttk.Button(step_btns, text="Edit Step...", width=14, command=self._edit_step).pack(pady=2)
        ttk.Button(step_btns, text="Duplicate Step", width=14, command=self._duplicate_step).pack(pady=2)
        ttk.Button(step_btns, text="Test Step", width=14, command=self._test_step).pack(pady=2)
        ttk.Button(step_btns, text="Show Regions", width=14, command=self._show_step_regions).pack(pady=2)
        ttk.Button(step_btns, text="Remove Step", width=14, command=self._remove_step).pack(pady=2)
        ttk.Button(step_btns, text="Move Up", width=14, command=lambda: self._move_step(-1)).pack(pady=2)
        ttk.Button(step_btns, text="Move Down", width=14, command=lambda: self._move_step(1)).pack(pady=2)

        run_frame = ttk.Frame(self.macro_tab, padding=(12, 0, 12, 6))
        run_frame.pack(fill="x", padx=0, pady=0)
        self.run_btn = tk.Button(run_frame, text="\u25b6 Run", width=12, bg="#2e7d32", fg="white",
                                  command=self._start_engine)
        self.run_btn.pack(side="left", padx=2)
        self.stop_btn = tk.Button(run_frame, text="\u25a0 Stop", width=12, bg="#c62828", fg="white",
                                   state="disabled", command=self._stop_engine)
        self.stop_btn.pack(side="left", padx=2)
        self.status_label = tk.Label(run_frame, text="Stopped", fg="#c62828")
        self.status_label.pack(side="left", padx=12)

        log_frame = ttk.LabelFrame(self.macro_tab, text="Log", padding=8)
        log_frame.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.log_text = tk.Text(log_frame, height=8, state="disabled", bg="#111111", fg="#33ff33")
        self.log_text.pack(fill="both", expand=True)

    # ---- scenario management ----
    def _refresh_scenario_list(self):
        names = list_scenarios()
        self.scenario_combo["values"] = names
        if self.scenario.name in names:
            self.scenario_var.set(self.scenario.name)

    def _refresh_window_list(self):
        try:
            self.target_window_combo["values"] = visible_window_titles()
        except Exception as e:
            self._log(f"[warn] could not list windows: {e}")

    def _on_scenario_selected(self, event=None):
        name = self.scenario_var.get()
        try:
            self.scenario = load_scenario(name)
        except Exception as e:
            messagebox.showerror("Load failed", str(e))
            return
        self.poll_var.set(self.scenario.poll_interval)
        self.monitor_var.set(self.scenario.monitor_index)
        self.kill_var.set(self.scenario.kill_switch)
        self.target_window_var.set(self.scenario.target_window_title)
        self._refresh_steps()

    def _sync_scenario_settings(self):
        self.scenario.poll_interval = self.poll_var.get()
        self.scenario.monitor_index = self.monitor_var.get()
        self.scenario.kill_switch = self.kill_var.get().strip() or "f12"
        self.scenario.target_window_title = self.target_window_var.get().strip()

    def _validate_scenario_name_for_ui(self, name):
        try:
            return validate_scenario_name(name)
        except ValueError as exc:
            messagebox.showerror("Invalid scenario name", str(exc))
            return None

    def _new_scenario(self):
        name = simpledialog.askstring("New scenario", "Scenario name:", parent=self.root)
        if not name:
            return
        name = self._validate_scenario_name_for_ui(name)
        if name is None:
            return
        self.scenario = Scenario(name=name)
        self.scenario_var.set(name)
        self.poll_var.set(self.scenario.poll_interval)
        self.monitor_var.set(self.scenario.monitor_index)
        self.kill_var.set(self.scenario.kill_switch)
        self.target_window_var.set(self.scenario.target_window_title)
        self._refresh_steps()

    def _save_scenario(self):
        self._sync_scenario_settings()
        path = save_scenario(self.scenario)
        self._refresh_scenario_list()
        self._log(f"Saved to {path}")

    def _save_scenario_as(self):
        name = simpledialog.askstring("Save as", "New scenario name:",
                                       initialvalue=self.scenario.name, parent=self.root)
        if not name:
            return
        name = self._validate_scenario_name_for_ui(name)
        if name is None:
            return
        if name != self.scenario.name and name in list_scenarios():
            messagebox.showerror("Duplicate name", "A scenario with that name already exists.")
            return
        self.scenario.name = name
        self.scenario_var.set(name)
        self._save_scenario()

    def _duplicate_scenario(self):
        self._sync_scenario_settings()
        name = simpledialog.askstring(
            "Duplicate scenario",
            "Name for duplicated scenario:",
            initialvalue=f"{self.scenario.name}_copy",
            parent=self.root,
        )
        if not name:
            return
        name = self._validate_scenario_name_for_ui(name)
        if name is None:
            return
        if name in list_scenarios():
            messagebox.showerror("Duplicate name", "A scenario with that name already exists.")
            return
        try:
            self.scenario = duplicate_scenario(self.scenario, name)
            path = save_scenario(self.scenario)
        except Exception as e:
            messagebox.showerror("Duplicate failed", str(e))
            return
        self.scenario_var.set(self.scenario.name)
        self._refresh_scenario_list()
        self._refresh_steps()
        self._log(f"Duplicated scenario to {path}")

    def _delete_scenario(self):
        name = self.scenario_var.get()
        if name and messagebox.askyesno("Delete", f"Delete scenario '{name}'?"):
            delete_scenario(name)
            self.scenario = Scenario(name="untitled")
            self.scenario_var.set(self.scenario.name)
            self.poll_var.set(self.scenario.poll_interval)
            self.monitor_var.set(self.scenario.monitor_index)
            self.kill_var.set(self.scenario.kill_switch)
            self.target_window_var.set(self.scenario.target_window_title)
            self._refresh_scenario_list()
            self._refresh_steps()

    # ---- step management ----
    def _refresh_steps(self):
        self.steps_listbox.delete(0, tk.END)
        for s in self.scenario.steps:
            state = "ON " if s.enabled else "off"
            self.steps_listbox.insert(tk.END, f"[{state}] {s.name}  ({len(s.conditions)} cond, {len(s.actions)} act)")

    def _add_step(self):
        existing = {s.name for s in self.scenario.steps}
        all_names = [s.name for s in self.scenario.steps]
        s = step_dialog(self.root, existing_names=existing, all_step_names=all_names,
                         monitor_index=self.monitor_var.get(),
                         target_window_title=self.target_window_var.get().strip())
        if s:
            self.scenario.steps.append(s)
            self._refresh_steps()

    def _edit_step(self):
        sel = self.steps_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        existing = {s.name for s in self.scenario.steps}
        all_names = [s.name for s in self.scenario.steps]
        s = step_dialog(self.root, step=self.scenario.steps[idx], existing_names=existing,
                         all_step_names=all_names, monitor_index=self.monitor_var.get(),
                         target_window_title=self.target_window_var.get().strip())
        if s:
            self.scenario.steps[idx] = s
            self._refresh_steps()

    def _remove_step(self):
        sel = self.steps_listbox.curselection()
        if sel and messagebox.askyesno("Remove step", "Remove the selected step?"):
            del self.scenario.steps[sel[0]]
            self._refresh_steps()

    def _duplicate_step(self):
        sel = self.steps_listbox.curselection()
        if not sel:
            return
        existing = {s.name for s in self.scenario.steps}
        copied = duplicate_step(self.scenario.steps[sel[0]], existing)
        self.scenario.steps.insert(sel[0] + 1, copied)
        self._refresh_steps()
        self.steps_listbox.selection_set(sel[0] + 1)

    def _test_step(self):
        if self.engine and self.engine.is_running:
            messagebox.showwarning("Macro running", "Stop the macro before testing a step.")
            return
        sel = self.steps_listbox.curselection()
        if not sel:
            messagebox.showinfo("No step selected", "Select a step to test first.")
            return
        self._sync_scenario_settings()
        step = self.scenario.steps[sel[0]]

        engine = MacroEngine(self.scenario, log=self._queue_log)
        try:
            preview = engine.preview_step(step)
        except Exception as e:
            messagebox.showerror("Test failed", str(e))
            return
        finally:
            engine.stop()

        self._show_step_preview(step, preview)

    def _show_step_regions(self):
        sel = self.steps_listbox.curselection()
        if not sel:
            messagebox.showinfo("No step selected", "Select a step first.")
            return
        self._sync_scenario_settings()
        step = self.scenario.steps[sel[0]]

        boxes = []
        missing_window = False
        for i, cond in enumerate(step.conditions):
            try:
                box = resolve_condition_preview_box(
                    cond,
                    target_window_title=self.scenario.target_window_title,
                    monitor_index=self.scenario.monitor_index,
                )
            except Exception as e:
                messagebox.showerror("Show regions failed", str(e))
                return
            if box is _WINDOW_UNAVAILABLE:
                missing_window = True
                continue
            label = f"#{i} {os.path.basename(cond.template_path)}"
            if cond.negate:
                label = f"#{i} NOT {os.path.basename(cond.template_path)}"
            color = "#ff9800" if cond.negate else "#ffcc00"
            boxes.append((box, label, color))

        if missing_window:
            messagebox.showerror(
                "Target window not found",
                f"No visible window title contains: {self.scenario.target_window_title.strip()}",
            )
            return
        if not boxes:
            messagebox.showinfo("No regions", "This step has no conditions to show.")
            return

        MultiRegionOverlay(self.root, boxes)
        self._log(f"Showing {len(boxes)} search region(s) for step '{step.name}'")

    def _show_step_preview(self, step: Step, preview):
        win = tk.Toplevel(self.root)
        win.title(f"Test Step - {step.name}")
        win.geometry("900x700")

        summary = "MATCH" if preview["met"] else "NO MATCH"
        tk.Label(
            win,
            text=f"{summary}: {len(preview['matches'])} detection(s)",
            font=("Segoe UI", 11, "bold"),
            fg="#2e7d32" if preview["met"] else "#c62828",
        ).pack(anchor="w", padx=8, pady=6)

        previews = preview.get("condition_previews") or []
        if not previews:
            tk.Label(win, text="No screenshot available.").pack(anchor="w", padx=8, pady=4)
        else:
            images_frame = tk.Frame(win)
            images_frame.pack(fill="x", padx=8, pady=4)
            image_refs = []

            for condition_preview in previews:
                status = "OK" if condition_preview["ok"] else "MISS"
                template_name = os.path.basename(condition_preview["template_path"])
                if condition_preview["negate"]:
                    template_name = f"NOT {template_name}"
                capture_box = condition_preview.get("capture_box")
                region_text = "" if capture_box is None else f" region={capture_box}"
                tk.Label(
                    images_frame,
                    text=f"condition #{condition_preview['condition_index']} {status}: {template_name}{region_text}",
                    fg="#2e7d32" if condition_preview["ok"] else "#c62828",
                    font=("Segoe UI", 9, "bold"),
                ).pack(anchor="w", pady=(6, 0))

                image = condition_preview.get("image")
                if image is None:
                    tk.Label(images_frame, text="No screenshot available for this condition.").pack(anchor="w")
                    continue

                display_image = image.copy()
                draw = ImageDraw.Draw(display_image)
                for match in condition_preview["matches"]:
                    box = match.get("image_box", match["box"])
                    draw.rectangle(box, outline="lime", width=4)
                    text_pos = (box[0], max(0, box[1] - 18))
                    draw.text(text_pos, match["label"], fill="lime")

                max_w, max_h = 860, 180
                scale = min(max_w / display_image.width, max_h / display_image.height, 1.0)
                if scale < 1.0:
                    display_image = display_image.resize(
                        (int(display_image.width * scale), int(display_image.height * scale))
                    )

                photo = ImageTk.PhotoImage(display_image)
                image_refs.append(photo)
                img_label = tk.Label(images_frame, image=photo)
                img_label.pack(anchor="w", pady=2)
            win._preview_image_refs = image_refs

        details = tk.Text(win, height=6, state="normal")
        details.pack(fill="both", expand=True, padx=8, pady=8)
        if preview["matches"]:
            for match in preview["matches"]:
                details.insert(
                    tk.END,
                    f"condition #{match['condition_index']} {match['type']} "
                    f"{match['label']} box={match['box']} center={match['center']}\n",
                )
        else:
            details.insert(tk.END, "No detections met the selected conditions.\n")
        details.config(state="disabled")

    def _move_step(self, delta):
        sel = self.steps_listbox.curselection()
        if not sel:
            return
        idx, new_idx = sel[0], sel[0] + delta
        if 0 <= new_idx < len(self.scenario.steps):
            steps = self.scenario.steps
            steps[idx], steps[new_idx] = steps[new_idx], steps[idx]
            self._refresh_steps()
            self.steps_listbox.selection_set(new_idx)

    # ---- engine control ----
    def _start_engine(self):
        if self.engine and self.engine.is_running:
            return
        if not self.scenario.steps:
            messagebox.showwarning("No steps", "Add at least one step before running.")
            return
        self._sync_scenario_settings()
        self.engine = MacroEngine(self.scenario, log=self._queue_log)
        try:
            self.engine.start()
        except Exception as e:
            messagebox.showerror("Failed to start", str(e))
            return
        self.run_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.status_label.config(text="Running", fg="#2e7d32")

    def _stop_engine(self):
        if self.engine:
            self.engine.stop()
        self.run_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.status_label.config(text="Stopped", fg="#c62828")

    def _register_start_hotkey(self):
        try:
            self._start_hotkey_handle = keyboard.add_hotkey(
                "f11", self._request_start_from_hotkey
            )
        except Exception as exc:
            self._queue_log(f"[warn] could not register start hotkey F11: {exc}")

    def _request_start_from_hotkey(self):
        self.control_queue.put("start")

    def _start_engine_from_hotkey(self):
        if self.engine and self.engine.is_running:
            return
        self._start_engine()

    def _remove_start_hotkey(self):
        handle = getattr(self, "_start_hotkey_handle", None)
        if handle is None:
            return
        try:
            keyboard.remove_hotkey(handle)
        except Exception:
            pass
        self._start_hotkey_handle = None

    # ---- logging ----
    def _queue_log(self, msg):
        self.log_queue.put(msg)

    def _poll_log_queue(self):
        try:
            while True:
                command = self.control_queue.get_nowait()
                if command == "start":
                    self._start_engine_from_hotkey()
        except queue.Empty:
            pass
        try:
            while True:
                self._log(self.log_queue.get_nowait())
        except queue.Empty:
            pass
        if self.engine and not self.engine.is_running and self.stop_btn["state"] == "normal":
            self._stop_engine()
        self.root.after(150, self._poll_log_queue)

    def _log(self, msg):
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self.log_text.config(state="normal")
        line = f"{timestamp} {msg}"
        self.log_text.insert(tk.END, line + "\n")
        self._log_line_count += 1
        self._trim_log_text()
        self.log_text.see(tk.END)
        self.log_text.config(state="disabled")
        self._write_log_file(line)

    def _trim_log_text(self):
        extra_lines = self._log_line_count - self.log_text_max_lines
        if extra_lines > 0:
            self.log_text.delete("1.0", f"{extra_lines + 1}.0")
            self._log_line_count -= extra_lines

    def _write_log_file(self, line):
        try:
            if self._log_file_handle is None:
                rotate_log_file(self.log_file_path, self.log_max_bytes, self.log_backups)
                self._log_file_handle = open(self.log_file_path, "a", encoding="utf-8")
            self._log_file_handle.write(line + "\n")
            self._log_write_count += 1
            if self._log_write_count % 20 == 0:
                self._log_file_handle.flush()
            if self._log_write_count % 100 == 0 and os.path.exists(self.log_file_path):
                if os.path.getsize(self.log_file_path) >= self.log_max_bytes:
                    self._close_log_file()
                    rotate_log_file(self.log_file_path, self.log_max_bytes, self.log_backups)
        except Exception:
            pass

    def _flush_log_file(self):
        handle = getattr(self, "_log_file_handle", None)
        if handle is None:
            return
        try:
            handle.flush()
        except Exception:
            pass

    def _close_log_file(self):
        handle = getattr(self, "_log_file_handle", None)
        if handle is None:
            return
        try:
            handle.flush()
            handle.close()
        except Exception:
            pass
        self._log_file_handle = None

    def _on_close(self):
        self._remove_start_hotkey()
        if self.engine and self.engine.is_running:
            self.engine.stop()
        if hasattr(self, "alert_tab"):
            self.alert_tab.shutdown()
        self._close_log_file()
        self.root.destroy()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
