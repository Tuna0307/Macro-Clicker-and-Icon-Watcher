"""
PC Macro Builder -- main application.

A scenario is a list of Steps. Each Step has Conditions (images that
must/must-not be on screen) and Actions (click / key / wait / enable
or disable another step). Run it, watch the log, stop with the button
or your kill-switch key.
"""
import copy
import json
import math
import os
import queue
import tkinter as tk
import traceback
from datetime import datetime
from tkinter import ttk, filedialog, messagebox, simpledialog
from typing import Any, Optional
import keyboard
import mss
from PIL import ImageDraw, ImageTk

from detection_core import (
    LEGACY_MACRO_MATCH_MODE,
    MATCH_MODE_BY_LABEL,
    MATCH_MODE_LABELS,
    MATCH_MODE_LIST_TAGS,
    monitor_rect,
    physical_monitor_index,
)
from models import (
    Scenario, Step, ImageCondition, Action,
    list_scenarios, load_scenario, save_scenario, delete_scenario,
    portable_project_path, validate_scenario, validate_scenario_name,
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
    resolve_saved_capture_region,
    visible_window_titles,
)
from alert_watcher import AlertWatcherFrame, SingleInstanceLock
from runtime_paths import LOG_DIR, STARTUP_ERROR_LOG
from app_helpers import (
    duplicate_scenario,
    duplicate_step,
    duplicate_template_file,
    find_case_insensitive_name,
    remap_condition_references,
    rewrite_step_references,
)
from ui_components import (
    COLORS,
    CollapsibleSection,
    Tooltip,
    action_display_summary,
    condition_choice_for_index,
    condition_choices,
    condition_index_from_choice,
    configure_theme,
    preserved_level_roi,
)


START_MACRO_HOTKEY = "f8"


def _monitor_box(monitor_index=1):
    with mss.MSS() as sct:
        monitors = sct.monitors
        resolved_index = physical_monitor_index(
            monitors,
            monitor_index,
            use_fallback=False,
        )
        if isinstance(monitor_index, bool) or not isinstance(monitor_index, int):
            raise ValueError("Monitor must be a whole number.")
        if resolved_index is None:
            available = max(0, len(monitors) - 1)
            raise ValueError(
                f"Monitor {monitor_index} is unavailable. Choose 1 through {available}."
            )
        return monitor_rect(monitors[resolved_index])


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

    monitor_box = (
        _monitor_box(monitor_index)
        if window_rect is None or cond.region_mode == "monitor"
        else None
    )
    return resolve_saved_capture_region(
        cond.region,
        cond.region_mode,
        cond.region_ratio,
        cond.region_window_size,
        window_rect=window_rect,
        monitor_rect=monitor_box,
    )


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

def condition_dialog(parent, cond: Optional[ImageCondition] = None, monitor_index=1,
                     target_window_title=""):
    win = tk.Toplevel(parent)
    win.title("Edit Condition" if cond else "Add Condition")
    win.grab_set()
    win.resizable(False, False)
    win.configure(background=COLORS["surface"])
    result: dict[str, Optional[ImageCondition]] = {"value": None}

    template_var = tk.StringVar(value=cond.template_path if cond else "")
    confidence_var = tk.DoubleVar(value=cond.confidence if cond else 0.85)
    comparison_template_var = tk.StringVar(value=cond.comparison_template_path if cond else "")
    comparison_margin_var = tk.DoubleVar(value=cond.comparison_margin if cond else 0.03)
    comparison_reference = (
        cond.comparison_template_reference_size
        if cond and cond.comparison_template_reference_size
        else None
    )
    comparison_ref_width_var = tk.StringVar(
        value=str(comparison_reference[0]) if comparison_reference else ""
    )
    comparison_ref_height_var = tk.StringVar(
        value=str(comparison_reference[1]) if comparison_reference else ""
    )
    original_comparison_path = comparison_template_var.get().strip()

    def clear_stale_comparison_reference(*_args):
        if comparison_template_var.get().strip() != original_comparison_path:
            comparison_ref_width_var.set("")
            comparison_ref_height_var.set("")

    comparison_template_var.trace_add("write", clear_stale_comparison_reference)
    match_mode = cond.match_mode if cond else LEGACY_MACRO_MATCH_MODE
    match_mode_var = tk.StringVar(value=MATCH_MODE_LABELS[match_mode])
    grayscale_var = tk.BooleanVar(value=cond.use_grayscale if cond else False)
    template_reference_size_holder = {
        "size": (
            list(cond.template_reference_size)
            if cond and cond.template_reference_size
            else None
        )
    }
    original_template_path = template_var.get().strip()

    def clear_stale_template_reference(*_args):
        if template_var.get().strip() != original_template_path:
            template_reference_size_holder["size"] = None

    template_var.trace_add("write", clear_stale_template_reference)
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
        scope = {
            "window": "window-relative",
            "monitor": "monitor-relative",
        }.get(region_mode_holder["mode"], "screen")
        return f"{region_holder['region']} ({scope})"

    region_var = tk.StringVar(value=format_region_label())

    pad: dict[str, Any] = {"padx": 6, "pady": 4}

    ttk.Label(win, text="Template", style="Surface.TLabel").grid(row=0, column=0, sticky="w", **pad)
    template_entry = ttk.Entry(win, textvariable=template_var, width=42)
    template_entry.grid(row=0, column=1, columnspan=2, sticky="we", **pad)

    def browse():
        path = filedialog.askopenfilename(filetypes=[("PNG images", "*.png")], initialdir="templates", parent=win)
        if path:
            template_var.set(portable_project_path(path))
            template_reference_size_holder["size"] = None

    def capture():
        try:
            path = capture_template(win, monitor_index=monitor_index)
        except Exception as exc:
            messagebox.showerror("Capture failed", str(exc), parent=win)
            return
        if path:
            template_var.set(portable_project_path(path))
            title = target_window_title.strip()
            window_rect = find_window_rect(title) if title else None
            if window_rect:
                template_reference_size_holder["size"] = [
                    window_rect[2],
                    window_rect[3],
                ]
            else:
                monitor_box = _monitor_box(monitor_index)
                template_reference_size_holder["size"] = [
                    monitor_box[2],
                    monitor_box[3],
                ]

    template_browse_btn = ttk.Button(win, text="Browse", command=browse)
    template_browse_btn.grid(row=1, column=1, sticky="we", **pad)
    template_capture_btn = ttk.Button(win, text="Capture", command=capture)
    template_capture_btn.grid(row=1, column=2, sticky="we", **pad)

    ttk.Label(win, text="Confidence", style="Surface.TLabel").grid(row=2, column=0, sticky="w", **pad)
    ttk.Spinbox(
        win,
        textvariable=confidence_var,
        from_=0.5,
        to=1.0,
        increment=0.01,
        width=8,
    ).grid(row=2, column=1, sticky="w", **pad)

    def browse_comparison():
        path = filedialog.askopenfilename(
            filetypes=[("PNG images", "*.png")], initialdir="templates", parent=win
        )
        if path:
            comparison_template_var.set(portable_project_path(path))

    advanced_matching = CollapsibleSection(
        win,
        "Advanced matching",
        expanded=bool(
            comparison_template_var.get()
            or match_mode != LEGACY_MACRO_MATCH_MODE
            or grayscale_var.get()
        ),
    )
    advanced_matching.grid(row=3, column=0, columnspan=3, sticky="ew", padx=6, pady=(4, 2))
    advanced_matching.content.columnconfigure(1, weight=1)
    ttk.Label(
        advanced_matching.content,
        text="Detection type",
        style="Surface.TLabel",
    ).grid(row=0, column=0, sticky="w", padx=(0, 10), pady=4)
    ttk.Combobox(
        advanced_matching.content,
        textvariable=match_mode_var,
        values=list(MATCH_MODE_LABELS.values()),
        state="readonly",
        width=25,
    ).grid(row=0, column=1, columnspan=2, sticky="ew", pady=4)
    ttk.Checkbutton(
        advanced_matching.content,
        text="Grayscale pictures",
        variable=grayscale_var,
    ).grid(row=1, column=1, columnspan=2, sticky="w", pady=4)
    ttk.Label(
        advanced_matching.content,
        text="Compare against",
        style="Surface.TLabel",
    ).grid(row=2, column=0, sticky="w", padx=(0, 10), pady=4)
    ttk.Entry(
        advanced_matching.content,
        textvariable=comparison_template_var,
        width=34,
    ).grid(row=2, column=1, sticky="ew", pady=4)
    ttk.Button(
        advanced_matching.content,
        text="Browse",
        command=browse_comparison,
    ).grid(row=2, column=2, padx=(6, 0), pady=4)
    ttk.Label(
        advanced_matching.content,
        text="Required score lead",
        style="Surface.TLabel",
    ).grid(row=3, column=0, sticky="w", padx=(0, 10), pady=4)
    ttk.Spinbox(
        advanced_matching.content,
        textvariable=comparison_margin_var,
        from_=0.0,
        to=0.25,
        increment=0.01,
        width=8,
    ).grid(row=3, column=1, sticky="w", pady=4)
    ttk.Label(
        advanced_matching.content,
        text="Rival reference w / h",
        style="Surface.TLabel",
    ).grid(row=4, column=0, sticky="w", padx=(0, 10), pady=4)
    ttk.Entry(
        advanced_matching.content,
        textvariable=comparison_ref_width_var,
        width=8,
    ).grid(row=4, column=1, sticky="w", pady=4)
    ttk.Entry(
        advanced_matching.content,
        textvariable=comparison_ref_height_var,
        width=8,
    ).grid(row=4, column=2, sticky="w", padx=(6, 0), pady=4)

    ttk.Checkbutton(
        win,
        text="Require this template to be absent",
        variable=negate_var,
    ).grid(row=4, column=0, columnspan=3, sticky="w", **pad)

    ttk.Label(win, text="Search region", style="Surface.TLabel").grid(row=5, column=0, sticky="w", **pad)
    ttk.Label(win, textvariable=region_var, style="Muted.TLabel").grid(row=5, column=1, sticky="w", **pad)

    def pick_region():
        try:
            region, _ = select_region(win, monitor_index=monitor_index)
        except Exception as exc:
            messagebox.showerror("Region capture failed", str(exc), parent=win)
            return
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
                ratio = proportional_region_from_window(region, window_rect)
                if (
                    ratio[0] < 0.0
                    or ratio[1] < 0.0
                    or ratio[0] + ratio[2] > 1.001
                    or ratio[1] + ratio[3] > 1.001
                ):
                    messagebox.showerror(
                        "Region outside target window",
                        "Pick a region completely inside the selected target window.",
                        parent=win,
                    )
                    return
                region_ratio_holder["ratio"] = list(ratio)
                region_window_size_holder["size"] = [window_rect[2], window_rect[3]]
                region = relative_region_from_window(region, window_rect)
                region_mode_holder["mode"] = "window"
            else:
                monitor_box = _monitor_box(monitor_index)
                ratio = proportional_region_from_window(region, monitor_box)
                if (
                    ratio[0] < 0.0
                    or ratio[1] < 0.0
                    or ratio[0] + ratio[2] > 1.001
                    or ratio[1] + ratio[3] > 1.001
                ):
                    messagebox.showerror(
                        "Region outside monitor",
                        "Pick a region completely inside the selected monitor.",
                        parent=win,
                    )
                    return
                region_ratio_holder["ratio"] = list(ratio)
                region_window_size_holder["size"] = [
                    monitor_box[2],
                    monitor_box[3],
                ]
                region = relative_region_from_window(region, monitor_box)
                region_mode_holder["mode"] = "monitor"
            region_holder["region"] = list(region)
            region_var.set(format_region_label())

    def clear_region():
        region_holder["region"] = None
        region_mode_holder["mode"] = "screen"
        region_ratio_holder["ratio"] = None
        region_window_size_holder["size"] = None
        region_var.set(format_region_label())

    def current_condition():
        comparison_ref_width = _parse_optional_int(
            comparison_ref_width_var.get(),
            "Rival reference width",
        )
        comparison_ref_height = _parse_optional_int(
            comparison_ref_height_var.get(),
            "Rival reference height",
        )
        if (comparison_ref_width is None) != (comparison_ref_height is None):
            raise ValueError("Rival reference size requires both width and height.")
        comparison_reference_size = None
        if comparison_ref_width is not None and comparison_ref_height is not None:
            if comparison_ref_width <= 0 or comparison_ref_height <= 0:
                raise ValueError("Rival reference width and height must be positive.")
            comparison_reference_size = [
                comparison_ref_width,
                comparison_ref_height,
            ]
        candidate = ImageCondition(
            template_path=portable_project_path(template_var.get().strip()),
            confidence=round(confidence_var.get(), 2),
            comparison_template_path=portable_project_path(
                comparison_template_var.get().strip()
            ),
            comparison_margin=round(comparison_margin_var.get(), 2),
            comparison_template_reference_size=comparison_reference_size,
            match_mode=MATCH_MODE_BY_LABEL[match_mode_var.get()],
            use_grayscale=grayscale_var.get(),
            template_reference_size=template_reference_size_holder["size"],
            region=region_holder["region"],
            region_mode=region_mode_holder["mode"],
            region_ratio=region_ratio_holder["ratio"],
            region_window_size=region_window_size_holder["size"],
            negate=negate_var.get(),
        )
        validate_scenario(
            Scenario(
                name="Condition preview",
                target_window_title=target_window_title.strip(),
                steps=[Step(name="Condition", conditions=[candidate])],
            )
        )
        return candidate

    def show_region():
        try:
            temp_cond = current_condition()
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
        MultiRegionOverlay(win, [(box, label, "#ff9800" if negate_var.get() else "#ffcc00")])

    ttk.Button(win, text="Show", command=show_region).grid(row=6, column=0, sticky="we", **pad)
    ttk.Button(win, text="Pick region", command=pick_region).grid(row=6, column=1, sticky="we", **pad)
    ttk.Button(win, text="Clear", command=clear_region).grid(row=6, column=2, sticky="we", **pad)

    def on_ok():
        try:
            condition = current_condition()
        except (TypeError, ValueError, tk.TclError) as exc:
            messagebox.showerror("Invalid condition", str(exc), parent=win)
            return
        result["value"] = condition
        win.destroy()

    btns = ttk.Frame(win, style="Surface.TFrame")
    btns.grid(row=7, column=0, columnspan=3, sticky="e", padx=6, pady=12)
    ttk.Button(btns, text="Cancel", command=win.destroy).pack(side="left", padx=4)
    ttk.Button(btns, text="Save", style="Primary.TButton", command=on_ok).pack(side="left", padx=4)

    win.wait_window()
    return result["value"]


# ----------------------------------------------------------------------
# Action editor dialog
# ----------------------------------------------------------------------

def action_dialog(
    parent,
    action: Optional[Action] = None,
    step_names=None,
    num_conditions=0,
    conditions=None,
):
    win = tk.Toplevel(parent)
    win.title("Edit Action" if action else "Add Action")
    win.grab_set()
    win.resizable(False, False)
    win.configure(background=COLORS["surface"])
    result: dict[str, Optional[Action]] = {"value": None}
    a = action or Action(type="click")
    step_names = step_names or []
    conditions = list(conditions or [])
    condition_values = condition_choices(conditions)

    action_type_labels = {
        "click": "Click",
        "click_matching_row": "Click matching row",
        "key": "Press key",
        "wait": "Wait",
        "set_step": "Enable / disable step",
    }
    action_type_values = {label: value for value, label in action_type_labels.items()}

    ttk.Label(win, text="Action type", style="Surface.TLabel").grid(row=0, column=0, sticky="w", padx=10, pady=10)
    type_var = tk.StringVar(value=action_type_labels.get(a.type, "Click"))
    type_combo = ttk.Combobox(win, textvariable=type_var,
                               values=list(action_type_values.keys()),
                               state="readonly", width=22)
    type_combo.grid(row=0, column=1, sticky="w", padx=6, pady=6)

    body = ttk.Frame(win, style="Surface.TFrame")
    body.grid(row=1, column=0, columnspan=2, sticky="we", padx=6, pady=4)

    click_frame = ttk.LabelFrame(body, text="Click")
    row_click_frame = ttk.LabelFrame(body, text="Click matching row")
    key_frame = ttk.LabelFrame(body, text="Key press")
    wait_frame = ttk.LabelFrame(body, text="Wait")
    step_frame = ttk.LabelFrame(body, text="Enable / disable a step")
    frames = {
        "click": click_frame,
        "click_matching_row": row_click_frame,
        "key": key_frame,
        "wait": wait_frame,
        "set_step": step_frame,
    }

    # --- click fields ---
    cond_idx_var = tk.StringVar(
        value=condition_choice_for_index(conditions, a.on_condition_index, "Automatic target")
    )
    x_var = tk.StringVar(value=str(a.x) if a.x is not None else "")
    y_var = tk.StringVar(value=str(a.y) if a.y is not None else "")
    offx_var = tk.IntVar(value=a.offset_x)
    offy_var = tk.IntVar(value=a.offset_y)
    button_var = tk.StringVar(value=a.button)

    ttk.Label(click_frame, text="Click target", style="Surface.TLabel").grid(row=0, column=0, sticky="w", padx=4, pady=2)
    ttk.Combobox(
        click_frame,
        textvariable=cond_idx_var,
        values=["Automatic target"] + condition_values,
        state="readonly",
        width=30,
    ).grid(row=0, column=1, columnspan=3, sticky="w", padx=4)
    ttk.Label(click_frame, text="Fixed point x", style="Surface.TLabel").grid(row=2, column=0, sticky="w", padx=4, pady=(8, 2))
    ttk.Entry(click_frame, textvariable=x_var, width=7).grid(row=2, column=1, sticky="w")
    ttk.Label(click_frame, text="y", style="Surface.TLabel").grid(row=2, column=2, sticky="w")
    ttk.Entry(click_frame, textvariable=y_var, width=7).grid(row=2, column=3, sticky="w")
    ttk.Button(
        click_frame,
        text="Use mouse position (2s)",
        command=lambda: schedule_mouse_position_fill(win, x_var, y_var),
    ).grid(row=2, column=4, sticky="w", padx=(8, 4))
    ttk.Label(click_frame, text="Offset x", style="Surface.TLabel").grid(row=3, column=0, sticky="w", padx=4, pady=2)
    ttk.Entry(click_frame, textvariable=offx_var, width=7).grid(row=3, column=1, sticky="w")
    ttk.Label(click_frame, text="y", style="Surface.TLabel").grid(row=3, column=2, sticky="w")
    ttk.Entry(click_frame, textvariable=offy_var, width=7).grid(row=3, column=3, sticky="w")
    ttk.Label(click_frame, text="Button", style="Surface.TLabel").grid(row=4, column=0, sticky="w", padx=4, pady=2)
    ttk.Combobox(click_frame, textvariable=button_var, values=["left", "right", "middle"],
                 state="readonly", width=8).grid(row=4, column=1, sticky="w")

    # --- click matching row fields ---
    match_idx_var = tk.StringVar(
        value=condition_choice_for_index(conditions, a.match_condition_index, "Select condition")
    )
    row_cond_idx_var = tk.StringVar(
        value=condition_choice_for_index(conditions, a.on_condition_index, "Select condition")
    )
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
        value=condition_choice_for_index(
            conditions, getattr(a, "no_match_condition_index", None), "None"
        )
    )
    no_match_disable_steps_var = tk.StringVar(
        value=", ".join(getattr(a, "no_match_disable_steps", []) or [])
    )

    ttk.Label(row_click_frame, text="Row reference", style="Surface.TLabel").grid(row=0, column=0, sticky="w", padx=4, pady=2)
    ttk.Combobox(
        row_click_frame, textvariable=match_idx_var, values=condition_values,
        state="readonly", width=30,
    ).grid(row=0, column=1, columnspan=3, sticky="w")
    ttk.Label(row_click_frame, text="Click target", style="Surface.TLabel").grid(row=1, column=0, sticky="w", padx=4, pady=2)
    ttk.Combobox(
        row_click_frame, textvariable=row_cond_idx_var, values=condition_values,
        state="readonly", width=30,
    ).grid(row=1, column=1, columnspan=3, sticky="w")
    ttk.Label(row_click_frame, text="Row tolerance", style="Surface.TLabel").grid(row=2, column=0, sticky="w", padx=4, pady=2)
    ttk.Entry(row_click_frame, textvariable=row_tolerance_var, width=7).grid(row=2, column=1, sticky="w")
    ttk.Label(row_click_frame, text="Rows", style="Surface.TLabel").grid(row=3, column=0, sticky="w", padx=4, pady=2)
    ttk.Combobox(row_click_frame, textvariable=row_mode_var, values=["first", "all"],
                 state="readonly", width=8).grid(row=3, column=1, sticky="w")
    ttk.Label(row_click_frame, text="Target choice", style="Surface.TLabel").grid(row=4, column=0, sticky="w", padx=4, pady=2)
    ttk.Combobox(row_click_frame, textvariable=target_choice_var,
                 values=["leftmost", "rightmost", "nearest"],
                 state="readonly", width=10).grid(row=4, column=1, sticky="w")
    ttk.Label(row_click_frame, text="Offset x", style="Surface.TLabel").grid(row=5, column=0, sticky="w", padx=4, pady=2)
    ttk.Entry(row_click_frame, textvariable=row_offx_var, width=7).grid(row=5, column=1, sticky="w")
    ttk.Label(row_click_frame, text="y", style="Surface.TLabel").grid(row=5, column=2, sticky="w")
    ttk.Entry(row_click_frame, textvariable=row_offy_var, width=7).grid(row=5, column=3, sticky="w")
    ttk.Label(row_click_frame, text="Button", style="Surface.TLabel").grid(row=6, column=0, sticky="w", padx=4, pady=2)
    ttk.Combobox(row_click_frame, textvariable=row_button_var, values=["left", "right", "middle"],
                 state="readonly", width=8).grid(row=6, column=1, sticky="w")
    ttk.Label(row_click_frame, text="Min level", style="Surface.TLabel").grid(row=7, column=0, sticky="w", padx=4, pady=(8, 2))
    ttk.Entry(row_click_frame, textvariable=min_level_var, width=7).grid(row=7, column=1, sticky="w")
    ttk.Label(row_click_frame, text="Max level", style="Surface.TLabel").grid(row=7, column=2, sticky="w")
    ttk.Entry(row_click_frame, textvariable=max_level_var, width=7).grid(row=7, column=3, sticky="w")
    ttk.Label(row_click_frame, text="Min digits", style="Surface.TLabel").grid(row=8, column=0, sticky="w", padx=4, pady=2)
    ttk.Entry(row_click_frame, textvariable=level_min_digits_var, width=7).grid(row=8, column=1, sticky="w")
    ttk.Label(row_click_frame, text="Digit templates", style="Surface.TLabel").grid(row=9, column=0, sticky="w", padx=4, pady=2)
    ttk.Entry(row_click_frame, textvariable=level_digit_dir_var, width=28).grid(row=9, column=1, columnspan=3, sticky="we")

    def browse_level_digit_dir():
        path = filedialog.askdirectory(initialdir="templates", parent=win)
        if path:
            level_digit_dir_var.set(portable_project_path(path))

    ttk.Button(row_click_frame, text="Browse", command=browse_level_digit_dir).grid(row=9, column=4, sticky="w", padx=4)
    ttk.Label(row_click_frame, text="Level box x / y / w / h", style="Surface.TLabel").grid(row=10, column=0, sticky="w", padx=4, pady=2)
    ttk.Entry(row_click_frame, textvariable=level_roi_x_var, width=7).grid(row=10, column=1, sticky="w")
    ttk.Entry(row_click_frame, textvariable=level_roi_y_var, width=7).grid(row=10, column=2, sticky="w")
    ttk.Entry(row_click_frame, textvariable=level_roi_w_var, width=7).grid(row=10, column=3, sticky="w")
    ttk.Entry(row_click_frame, textvariable=level_roi_h_var, width=7).grid(row=10, column=4, sticky="w")
    ttk.Label(row_click_frame, text="No-row click", style="Surface.TLabel").grid(row=11, column=0, sticky="w", padx=4, pady=(8, 2))
    ttk.Combobox(
        row_click_frame,
        textvariable=no_match_cond_idx_var,
        values=["None"] + condition_values,
        state="readonly",
        width=30,
    ).grid(row=11, column=1, columnspan=3, sticky="w")
    ttk.Label(row_click_frame, text="Then disable", style="Surface.TLabel").grid(row=12, column=0, sticky="w", padx=4, pady=2)
    ttk.Entry(row_click_frame, textvariable=no_match_disable_steps_var, width=34).grid(row=12, column=1, columnspan=4, sticky="we")

    advanced_rows = (2, 5, 7, 8, 9, 10, 11, 12)
    advanced_widgets = [
        widget
        for row in advanced_rows
        for widget in row_click_frame.grid_slaves(row=row)
    ]
    advanced_configured = any(
        (
            a.row_tolerance != 60,
            a.offset_x != 0,
            a.offset_y != 0,
            a.min_level is not None,
            a.max_level is not None,
            a.level_roi is not None,
            getattr(a, "level_min_digits", 1) != 1,
            getattr(a, "no_match_condition_index", None) is not None,
            bool(getattr(a, "no_match_disable_steps", None)),
        )
    )
    row_advanced_state = {
        "expanded": advanced_configured,
        "opened": advanced_configured,
    }

    def render_row_advanced():
        for widget in advanced_widgets:
            if row_advanced_state["expanded"]:
                widget.grid()
            else:
                widget.grid_remove()
        row_advanced_btn.configure(
            text="Hide advanced options" if row_advanced_state["expanded"] else "Show advanced options"
        )

    def toggle_row_advanced():
        row_advanced_state["expanded"] = not row_advanced_state["expanded"]
        if row_advanced_state["expanded"]:
            row_advanced_state["opened"] = True
        render_row_advanced()

    row_advanced_btn = ttk.Button(
        row_click_frame,
        text="",
        style="Disclosure.TButton",
        command=toggle_row_advanced,
    )
    row_advanced_btn.grid(row=13, column=0, columnspan=5, sticky="ew", pady=(8, 0))
    render_row_advanced()

    # --- key fields ---
    key_var = tk.StringVar(value=a.key)
    hold_var = tk.DoubleVar(value=a.hold)
    ttk.Label(key_frame, text="Key", style="Surface.TLabel").grid(row=0, column=0, sticky="w", padx=4, pady=2)
    ttk.Entry(key_frame, textvariable=key_var, width=16).grid(row=0, column=1, sticky="w")
    ttk.Label(key_frame, text="Hold duration", style="Surface.TLabel").grid(row=1, column=0, sticky="w", padx=4, pady=2)
    ttk.Entry(key_frame, textvariable=hold_var, width=8).grid(row=1, column=1, sticky="w")

    # --- wait fields ---
    seconds_var = tk.DoubleVar(value=a.seconds)
    ttk.Label(wait_frame, text="Seconds", style="Surface.TLabel").grid(row=0, column=0, sticky="w", padx=4, pady=2)
    ttk.Entry(wait_frame, textvariable=seconds_var, width=8).grid(row=0, column=1, sticky="w")

    # --- set_step fields ---
    step_name_var = tk.StringVar(value=a.step_name)
    set_enabled_var = tk.BooleanVar(value=a.set_enabled)
    ttk.Label(step_frame, text="Step", style="Surface.TLabel").grid(row=0, column=0, sticky="w", padx=4, pady=2)
    ttk.Combobox(step_frame, textvariable=step_name_var, values=step_names, width=22).grid(row=0, column=1, sticky="w")
    ttk.Checkbutton(step_frame, text="Enable step",
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
                new_action.on_condition_index = condition_index_from_choice(
                    cond_idx_var.get(), "Click target", allow_blank=True
                )
                new_action.x = _parse_optional_int(x_var.get(), "Fixed x")
                new_action.y = _parse_optional_int(y_var.get(), "Fixed y")
                if (new_action.x is None) != (new_action.y is None):
                    raise ValueError("Fixed click coordinates require both x and y.")
                if (
                    new_action.on_condition_index is not None
                    and new_action.x is not None
                ):
                    raise ValueError(
                        "Choose either a condition target or a fixed point, not both."
                    )
                if (
                    new_action.on_condition_index is None
                    and new_action.x is None
                    and not conditions
                ):
                    raise ValueError(
                        "This step has no condition to use as an automatic click target; "
                        "enter a fixed point."
                    )
                new_action.offset_x = offx_var.get()
                new_action.offset_y = offy_var.get()
                new_action.button = button_var.get()
            elif t == "click_matching_row":
                mi = match_idx_var.get().strip()
                ci = row_cond_idx_var.get().strip()
                if mi in ("", "Select condition") or ci in ("", "Select condition"):
                    messagebox.showerror(
                        "Missing condition",
                        "Enter both the row reference condition and click condition.",
                        parent=win,
                    )
                    return
                new_action.match_condition_index = condition_index_from_choice(mi, "Row reference")
                new_action.on_condition_index = condition_index_from_choice(ci, "Click target")
                new_action.row_tolerance = row_tolerance_var.get()
                new_action.row_mode = row_mode_var.get()
                new_action.target_choice = target_choice_var.get()
                new_action.offset_x = row_offx_var.get()
                new_action.offset_y = row_offy_var.get()
                new_action.button = row_button_var.get()
                new_action.min_level = _parse_optional_int(min_level_var.get(), "Min level")
                new_action.max_level = _parse_optional_int(max_level_var.get(), "Max level")
                new_action.level_digit_template_dir = portable_project_path(
                    level_digit_dir_var.get().strip()
                )
                new_action.level_min_digits = max(
                    1,
                    _parse_required_int(level_min_digits_var.get(), "Min digits"),
                )
                new_action.level_roi = preserved_level_roi(
                    a.level_roi,
                    row_advanced_state["opened"],
                    (
                        level_roi_x_var.get(),
                        level_roi_y_var.get(),
                        level_roi_w_var.get(),
                        level_roi_h_var.get(),
                    ),
                )
                new_action.no_match_condition_index = condition_index_from_choice(
                    no_match_cond_idx_var.get(), "No-match target", allow_blank=True
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
            # Re-parse through the model to enforce finite/non-negative values
            # before the dialog closes instead of deferring errors until Run.
            new_action = Action.from_dict(new_action.to_dict())
        except (ValueError, tk.TclError) as exc:
            messagebox.showerror("Invalid number", str(exc), parent=win)
            return
        result["value"] = new_action
        win.destroy()

    btns = ttk.Frame(win, style="Surface.TFrame")
    btns.grid(row=2, column=0, columnspan=2, sticky="e", padx=10, pady=12)
    ttk.Button(btns, text="Cancel", command=win.destroy).pack(side="left", padx=4)
    ttk.Button(btns, text="Save", style="Primary.TButton", command=on_ok).pack(side="left", padx=4)

    win.wait_window()
    return result["value"]


# ----------------------------------------------------------------------
# Step editor dialog
# ----------------------------------------------------------------------

def step_dialog(parent, step: Optional[Step] = None, existing_names=None, all_step_names=None,
                monitor_index=1, target_window_title=""):
    win = tk.Toplevel(parent)
    win.title("Edit Step" if step else "Add Step")
    win.grab_set()
    win.resizable(False, False)
    win.configure(background=COLORS["surface"])
    result: dict[str, Optional[Step]] = {"value": None}

    s = step or Step(name="")
    # Work on independent copies so Cancel never leaks edits into the scenario.
    conditions = copy.deepcopy(s.conditions)
    actions = copy.deepcopy(s.actions)
    existing_names = existing_names or set()
    all_step_names = all_step_names or []

    pad: dict[str, Any] = {"padx": 6, "pady": 4}

    name_var = tk.StringVar(value=s.name)
    enabled_var = tk.BooleanVar(value=s.enabled)
    operator_var = tk.StringVar(value=s.condition_operator)
    cooldown_var = tk.DoubleVar(value=s.cooldown)
    repeatable_var = tk.BooleanVar(value=s.repeatable)

    ttk.Label(win, text="Step name", style="Surface.TLabel").grid(row=0, column=0, sticky="w", **pad)
    ttk.Entry(win, textvariable=name_var, width=28).grid(row=0, column=1, sticky="w", **pad)
    ttk.Checkbutton(win, text="Enabled at scenario start", variable=enabled_var).grid(
        row=0, column=2, columnspan=2, sticky="w", **pad)

    ttk.Label(win, text="Condition rule", style="Surface.TLabel").grid(row=1, column=0, sticky="w", **pad)
    ttk.Combobox(win, textvariable=operator_var, values=["AND", "OR"], state="readonly", width=6).grid(
        row=1, column=1, sticky="w", **pad)
    ttk.Label(win, text="Cooldown", style="Surface.TLabel").grid(row=1, column=2, sticky="w", **pad)
    ttk.Entry(win, textvariable=cooldown_var, width=8).grid(row=1, column=3, sticky="w", **pad)
    ttk.Checkbutton(win, text="Repeat while matched", variable=repeatable_var).grid(
        row=1, column=4, sticky="w", **pad)

    ttk.Label(win, text="Conditions", style="Section.TLabel").grid(row=2, column=0, sticky="w", **pad)
    cond_listbox = tk.Listbox(
        win,
        width=78,
        height=6,
        bg=COLORS["surface"],
        fg=COLORS["text"],
        selectbackground=COLORS["accent_soft"],
        selectforeground=COLORS["text"],
        highlightcolor=COLORS["border"],
        highlightbackground=COLORS["border"],
        relief="flat",
        borderwidth=0,
        font=("Segoe UI", 9),
    )
    cond_listbox.grid(row=3, column=0, columnspan=5, sticky="we", padx=6)

    def refresh_conditions():
        cond_listbox.delete(0, tk.END)
        for i, c in enumerate(conditions):
            tag = "NOT " if c.negate else ""
            subject = os.path.basename(c.template_path)
            if not c.region:
                region_txt = "target window" if target_window_title else "full screen"
            else:
                scope = {
                    "window": "window-relative",
                    "monitor": "monitor-relative",
                }.get(c.region_mode, "absolute screen")
                region_txt = f"{scope} region {tuple(c.region)}"
            comparison_txt = ""
            if c.comparison_template_path:
                rival = os.path.basename(c.comparison_template_path)
                comparison_txt = f", beats {rival} by {c.comparison_margin:.2f}"
            mode_txt = MATCH_MODE_LIST_TAGS.get(c.match_mode, "Static")
            cond_listbox.insert(
                tk.END,
                f"[{i}] {tag}{subject} [{mode_txt}]  "
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
            removed_index = sel[0]
            users = []
            for action_index, action in enumerate(actions):
                fields = [
                    field_name
                    for field_name in (
                        "on_condition_index",
                        "match_condition_index",
                        "no_match_condition_index",
                    )
                    if getattr(action, field_name, None) == removed_index
                ]
                if fields:
                    users.append(action_index + 1)
            if users:
                messagebox.showerror(
                    "Condition is in use",
                    "This condition is referenced by action(s) "
                    + ", ".join(str(index) for index in users)
                    + ". Edit or remove those actions before deleting the condition.",
                    parent=win,
                )
                return
            del conditions[removed_index]
            changes = remap_condition_references(actions, removed_index)
            refresh_conditions()
            refresh_actions()
            if changes["cleared"]:
                messagebox.showwarning(
                    "Action target removed",
                    f"Cleared {changes['cleared']} action reference(s) that targeted the "
                    "removed condition. Edit those actions before running this step.",
                    parent=win,
                )

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
        copied.template_path = portable_project_path(new_path)
        conditions.append(copied)
        refresh_conditions()

    cbtns = ttk.Frame(win, style="Surface.TFrame")
    cbtns.grid(row=4, column=0, columnspan=5, sticky="w", padx=6, pady=(0, 8))
    ttk.Button(cbtns, text="Add", command=add_condition).pack(side="left", padx=2)
    ttk.Button(cbtns, text="Edit", command=edit_condition).pack(side="left", padx=2)
    ttk.Button(cbtns, text="Duplicate template", command=duplicate_condition_template).pack(side="left", padx=2)
    ttk.Button(cbtns, text="Remove", command=remove_condition).pack(side="left", padx=2)

    ttk.Label(win, text="Actions", style="Section.TLabel").grid(
        row=5, column=0, columnspan=3, sticky="w", **pad)
    act_listbox = tk.Listbox(
        win,
        width=78,
        height=6,
        bg=COLORS["surface"],
        fg=COLORS["text"],
        selectbackground=COLORS["accent_soft"],
        selectforeground=COLORS["text"],
        highlightcolor=COLORS["border"],
        highlightbackground=COLORS["border"],
        relief="flat",
        borderwidth=0,
        font=("Segoe UI", 9),
    )
    act_listbox.grid(row=6, column=0, columnspan=5, sticky="we", padx=6)

    def refresh_actions():
        act_listbox.delete(0, tk.END)
        for i, act in enumerate(actions):
            act_listbox.insert(tk.END, f"{i + 1}. {action_display_summary(act, conditions)}")
    refresh_actions()

    def add_action():
        act = action_dialog(
            win,
            step_names=all_step_names,
            num_conditions=len(conditions),
            conditions=conditions,
        )
        if act:
            actions.append(act)
            refresh_actions()

    def edit_action():
        sel = act_listbox.curselection()
        if not sel:
            return
        act = action_dialog(
            win,
            action=actions[sel[0]],
            step_names=all_step_names,
            num_conditions=len(conditions),
            conditions=conditions,
        )
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

    abtns = ttk.Frame(win, style="Surface.TFrame")
    abtns.grid(row=7, column=0, columnspan=5, sticky="w", padx=6, pady=(0, 8))
    ttk.Button(abtns, text="Add", command=add_action).pack(side="left", padx=2)
    ttk.Button(abtns, text="Edit", command=edit_action).pack(side="left", padx=2)
    ttk.Button(abtns, text="Remove", command=remove_action).pack(side="left", padx=2)
    ttk.Button(abtns, text="Move up", command=lambda: move_action(-1)).pack(side="left", padx=2)
    ttk.Button(abtns, text="Move down", command=lambda: move_action(1)).pack(side="left", padx=2)

    def on_save():
        nm = name_var.get().strip()
        if not nm:
            messagebox.showerror("Missing name", "Enter a step name.", parent=win)
            return
        collision = find_case_insensitive_name(existing_names, nm, exclude_name=s.name)
        if collision is not None:
            messagebox.showerror(
                "Duplicate name",
                f"A step named '{collision}' already exists (names are case-insensitive).",
                parent=win,
            )
            return
        try:
            cooldown = float(cooldown_var.get())
        except (TypeError, ValueError, tk.TclError):
            messagebox.showerror(
                "Invalid cooldown", "Cooldown must be a number.", parent=win
            )
            return
        if not math.isfinite(cooldown) or cooldown < 0.0:
            messagebox.showerror(
                "Invalid cooldown",
                "Cooldown must be a non-negative finite number.",
                parent=win,
            )
            return
        result["value"] = Step(
            name=nm, conditions=conditions, actions=actions,
            condition_operator=operator_var.get(), enabled=enabled_var.get(),
            cooldown=cooldown, repeatable=repeatable_var.get(),
        )
        win.destroy()

    btns = ttk.Frame(win, style="Surface.TFrame")
    btns.grid(row=8, column=0, columnspan=5, sticky="e", padx=6, pady=12)
    ttk.Button(btns, text="Cancel", command=win.destroy).pack(side="left", padx=4)
    ttk.Button(btns, text="Save", style="Primary.TButton", command=on_save).pack(side="left", padx=4)

    win.wait_window()
    return result["value"]


class App:
    def __init__(self, root):
        self.root = root
        root.title("PC Macro Builder")
        root.geometry("1220x820")
        root.minsize(1024, 700)
        self._configure_style()

        self.scenario = Scenario(name="untitled")
        self.engine: Optional[MacroEngine] = None
        self._clean_scenario_snapshot = None
        self._loaded_scenario_name = None
        self._engine_ui_active = False
        self.log_queue = queue.Queue()
        self.control_queue = queue.Queue()
        self._start_hotkey_handle = None
        self.log_dir = LOG_DIR
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
        self._mark_scenario_clean()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._register_start_hotkey()
        self.root.after(150, self._poll_log_queue)
        self._write_log_file("---- app started ----")

    def _configure_style(self):
        style = configure_theme(self.root)
        style.configure(
            "Running.Status.TLabel",
            background=COLORS["surface"],
            foreground=COLORS["success"],
            font=("Segoe UI Semibold", 9),
        )
        style.configure(
            "Stopped.Status.TLabel",
            background=COLORS["surface"],
            foreground=COLORS["muted"],
            font=("Segoe UI Semibold", 9),
        )

    # ---- layout ----
    def _build_ui(self):
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True)

        self.macro_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.macro_tab, text="Macro Builder")
        self.macro_tab.columnconfigure(0, weight=1)
        self.macro_tab.rowconfigure(2, weight=1)

        self.alert_tab = AlertWatcherFrame(self.notebook, embedded=True)
        self.notebook.add(self.alert_tab, text="Icon Alerts")

        top = ttk.Frame(self.macro_tab, style="Toolbar.TFrame", padding=(14, 11))
        top.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 6))
        top.columnconfigure(1, weight=1)

        ttk.Label(top, text="Scenario", style="Surface.TLabel").grid(row=0, column=0, sticky="w")
        self.scenario_var = tk.StringVar()
        self.scenario_combo = ttk.Combobox(top, textvariable=self.scenario_var, state="readonly", width=28)
        self.scenario_combo.grid(row=0, column=1, sticky="w", padx=(8, 14))
        self.scenario_combo.bind("<<ComboboxSelected>>", self._on_scenario_selected)
        new_btn = ttk.Button(top, text="New", style="Toolbar.TButton", command=self._new_scenario)
        new_btn.grid(row=0, column=2, padx=2)
        save_btn = ttk.Button(top, text="Save", style="Toolbar.TButton", command=self._save_scenario)
        save_btn.grid(row=0, column=3, padx=2)
        ttk.Button(top, text="Save as", style="Toolbar.TButton", command=self._save_scenario_as).grid(row=0, column=4, padx=2)
        ttk.Button(top, text="Duplicate", style="Toolbar.TButton", command=self._duplicate_scenario).grid(row=0, column=5, padx=2)
        delete_btn = ttk.Button(top, text="Delete", style="Toolbar.TButton", command=self._delete_scenario)
        delete_btn.grid(row=0, column=6, padx=(2, 14))
        Tooltip(new_btn, "Create a scenario")
        Tooltip(save_btn, "Save the current scenario")
        Tooltip(delete_btn, "Delete the current scenario")

        self.status_label = ttk.Label(top, text="Stopped", style="Stopped.Status.TLabel")
        self.status_label.grid(row=0, column=7, padx=(8, 10))
        self.run_btn = ttk.Button(top, text="Run", style="Primary.TButton", command=self._start_engine)
        self.run_btn.grid(row=0, column=8, padx=3)
        self.stop_btn = ttk.Button(top, text="Stop", style="Danger.TButton", state="disabled", command=self._stop_engine)
        self.stop_btn.grid(row=0, column=9, padx=3)
        Tooltip(
            self.run_btn,
            f"Start the selected scenario ({START_MACRO_HOTKEY.upper()})",
        )
        Tooltip(self.stop_btn, "Stop the running scenario")

        config = ttk.Frame(self.macro_tab, style="Surface.TFrame", padding=(14, 9))
        config.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 6))
        config.columnconfigure(1, weight=1)
        ttk.Label(config, text="Target window", style="Surface.TLabel").grid(row=0, column=0, sticky="w")
        self.target_window_var = tk.StringVar(value=self.scenario.target_window_title)
        self.target_window_combo = ttk.Combobox(config, textvariable=self.target_window_var, width=48)
        self.target_window_combo.grid(row=0, column=1, sticky="ew", padx=(10, 6))
        refresh_btn = ttk.Button(config, text="Refresh", command=self._refresh_window_list)
        refresh_btn.grid(row=0, column=2, padx=3)
        settings_btn = ttk.Button(config, text="Scenario settings", command=self._open_scenario_settings)
        settings_btn.grid(row=0, column=3, padx=(8, 0))
        Tooltip(refresh_btn, "Refresh visible windows")

        self.poll_var = tk.DoubleVar(value=self.scenario.poll_interval)
        self.monitor_var = tk.IntVar(value=self.scenario.monitor_index)
        self.kill_var = tk.StringVar(value=self.scenario.kill_switch)
        self._refresh_window_list()

        workspace = ttk.PanedWindow(self.macro_tab, orient="horizontal")
        workspace.grid(row=2, column=0, sticky="nsew", padx=10, pady=(0, 6))

        navigator = ttk.Frame(workspace, style="Surface.TFrame", padding=12, width=330)
        inspector = ttk.Frame(workspace, style="Surface.TFrame", padding=14)
        workspace.add(navigator, weight=1)
        workspace.add(inspector, weight=3)

        navigator.columnconfigure(0, weight=1)
        navigator.rowconfigure(2, weight=1)
        ttk.Label(navigator, text="Steps", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        nav_tools = ttk.Frame(navigator, style="Surface.TFrame")
        nav_tools.grid(row=1, column=0, sticky="ew", pady=(8, 8))
        add_step_btn = ttk.Button(nav_tools, text="Add step", command=self._add_step)
        add_step_btn.pack(side="left")
        for text, command, tip in (
            ("\u270e", self._edit_step, "Edit selected step"),
            ("\u2398", self._duplicate_step, "Duplicate selected step"),
            ("\u2191", lambda: self._move_step(-1), "Move step up"),
            ("\u2193", lambda: self._move_step(1), "Move step down"),
            ("X", self._remove_step, "Remove selected step"),
        ):
            button = ttk.Button(nav_tools, text=text, style="Icon.TButton", command=command)
            button.pack(side="left", padx=(5, 0))
            Tooltip(button, tip)

        self.steps_tree = ttk.Treeview(
            navigator,
            columns=("state", "counts"),
            show="tree headings",
            selectmode="browse",
            height=12,
        )
        self.steps_tree.heading("#0", text="Step")
        self.steps_tree.heading("state", text="State")
        self.steps_tree.heading("counts", text="Cond / Act")
        self.steps_tree.column("#0", width=145, minwidth=105, stretch=True)
        self.steps_tree.column("state", width=58, anchor="center", stretch=False)
        self.steps_tree.column("counts", width=72, anchor="center", stretch=False)
        self.steps_tree.grid(row=2, column=0, sticky="nsew")
        self.steps_tree.bind("<<TreeviewSelect>>", self._on_step_selected)
        self.steps_tree.bind("<Double-1>", lambda _event: self._edit_step())
        self.steps_tree.tag_configure("disabled", foreground=COLORS["muted"])

        inspector.columnconfigure(0, weight=1)
        inspector.rowconfigure(3, weight=1)
        header = ttk.Frame(inspector, style="Surface.TFrame")
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        self.selected_step_name_var = tk.StringVar(value="Select a step")
        self.selected_step_meta_var = tk.StringVar(value="")
        ttk.Label(header, textvariable=self.selected_step_name_var, style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(header, textvariable=self.selected_step_meta_var, style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(2, 0))
        ttk.Button(header, text="Edit step", command=self._edit_step).grid(row=0, column=1, rowspan=2, padx=(8, 4))
        ttk.Button(header, text="Test", command=self._test_step).grid(row=0, column=2, rowspan=2, padx=4)
        ttk.Button(header, text="Show regions", command=self._show_step_regions).grid(row=0, column=3, rowspan=2, padx=(4, 0))

        ttk.Separator(inspector).grid(row=1, column=0, sticky="ew", pady=12)
        details = ttk.Frame(inspector, style="Surface.TFrame")
        details.grid(row=2, column=0, sticky="nsew")
        details.columnconfigure(0, weight=1)
        details.columnconfigure(1, weight=1)
        details.rowconfigure(1, weight=1)

        conditions_panel = ttk.Frame(details, style="Surface.TFrame")
        conditions_panel.grid(row=0, column=0, rowspan=2, sticky="nsew", padx=(0, 7))
        conditions_panel.columnconfigure(0, weight=1)
        conditions_panel.rowconfigure(1, weight=1)
        cond_header = ttk.Frame(conditions_panel, style="Surface.TFrame")
        cond_header.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        cond_header.columnconfigure(0, weight=1)
        ttk.Label(cond_header, text="Conditions", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Button(cond_header, text="Edit", command=self._edit_selected_condition).grid(row=0, column=1)
        self.condition_tree = ttk.Treeview(conditions_panel, columns=("rule", "scope"), show="tree headings", selectmode="browse")
        self.condition_tree.heading("#0", text="Template")
        self.condition_tree.heading("rule", text="Match")
        self.condition_tree.heading("scope", text="Scope")
        self.condition_tree.column("#0", width=135, minwidth=95)
        self.condition_tree.column("rule", width=85, anchor="center")
        self.condition_tree.column("scope", width=78)
        self.condition_tree.grid(row=1, column=0, sticky="nsew")
        self.condition_tree.bind("<Double-1>", lambda _event: self._edit_selected_condition())

        actions_panel = ttk.Frame(details, style="Surface.TFrame")
        actions_panel.grid(row=0, column=1, rowspan=2, sticky="nsew", padx=(7, 0))
        actions_panel.columnconfigure(0, weight=1)
        actions_panel.rowconfigure(1, weight=1)
        action_header = ttk.Frame(actions_panel, style="Surface.TFrame")
        action_header.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        action_header.columnconfigure(0, weight=1)
        ttk.Label(action_header, text="Actions", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Button(action_header, text="Edit", command=self._edit_selected_action).grid(row=0, column=1)
        self.action_tree = ttk.Treeview(actions_panel, columns=("order",), show="tree headings", selectmode="browse")
        self.action_tree.heading("#0", text="Action")
        self.action_tree.heading("order", text="#")
        self.action_tree.column("#0", width=300, minwidth=180)
        self.action_tree.column("order", width=38, anchor="center", stretch=False)
        self.action_tree.grid(row=1, column=0, sticky="nsew")
        self.action_tree.bind("<Double-1>", lambda _event: self._edit_selected_action())

        activity = ttk.Frame(self.macro_tab, style="Surface.TFrame", padding=(12, 8))
        activity.grid(row=3, column=0, sticky="ew", padx=10, pady=(0, 10))
        activity.columnconfigure(0, weight=1)
        activity_header = ttk.Frame(activity, style="Surface.TFrame")
        activity_header.grid(row=0, column=0, sticky="ew")
        activity_header.columnconfigure(0, weight=1)
        ttk.Label(activity_header, text="Activity", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        self.activity_toggle = ttk.Button(activity_header, text="Hide", command=self._toggle_activity)
        self.activity_toggle.grid(row=0, column=1)
        self.activity_body = ttk.Frame(activity, style="Surface.TFrame")
        self.activity_body.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        self.activity_body.columnconfigure(0, weight=1)
        self.log_text = tk.Text(
            self.activity_body,
            height=7,
            state="disabled",
            bg=COLORS["surface"],
            fg=COLORS["text"],
            insertbackground=COLORS["text"],
            selectbackground=COLORS["accent_soft"],
            relief="flat",
            borderwidth=0,
            font=("Cascadia Mono", 9),
            wrap="none",
        )
        log_scroll = ttk.Scrollbar(self.activity_body, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.grid(row=0, column=0, sticky="ew")
        log_scroll.grid(row=0, column=1, sticky="ns")
        self._activity_visible = True

    # ---- scenario management ----
    def _scenario_snapshot(self):
        self._sync_scenario_settings()
        return json.dumps(
            self.scenario.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )

    def _mark_scenario_clean(self, loaded_name=None):
        self._clean_scenario_snapshot = self._scenario_snapshot()
        if loaded_name is not None:
            self._loaded_scenario_name = loaded_name

    def _has_unsaved_changes(self):
        try:
            return self._scenario_snapshot() != getattr(self, "_clean_scenario_snapshot", None)
        except (TypeError, ValueError, tk.TclError):
            # Invalid text in a Tk variable is still an unsaved edit and must
            # not be discarded silently.
            return True

    def _confirm_save_before(self, action):
        if not self._has_unsaved_changes():
            return True
        choice = messagebox.askyesnocancel(
            "Unsaved changes",
            f"Save changes to '{self.scenario.name}' before {action}?",
            parent=self.root,
        )
        if choice is None:
            return False
        if choice:
            return self._save_scenario()
        return True

    def _require_stopped_for_scenario_change(self):
        engine = getattr(self, "engine", None)
        if engine is None or not engine.is_running:
            return True
        messagebox.showwarning(
            "Macro running",
            "Stop the macro before changing, loading, duplicating, or deleting a scenario.",
            parent=self.root,
        )
        return False

    def _apply_scenario_to_ui(self, scenario, loaded_name=None, clean=True):
        self.scenario = scenario
        self.scenario_var.set(scenario.name)
        self.poll_var.set(scenario.poll_interval)
        self.monitor_var.set(scenario.monitor_index)
        self.kill_var.set(scenario.kill_switch)
        self.target_window_var.set(scenario.target_window_title)
        self._loaded_scenario_name = loaded_name
        self._refresh_steps()
        if clean:
            self._mark_scenario_clean(loaded_name=loaded_name)
        else:
            # A newly-created scenario has never been saved, even when empty.
            self._clean_scenario_snapshot = None

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
        current_name = self.scenario.name
        if not name or name == current_name:
            return
        if not self._require_stopped_for_scenario_change():
            self.scenario_var.set(current_name)
            return
        if not self._confirm_save_before(f"switching to '{name}'"):
            self.scenario_var.set(current_name)
            return
        try:
            scenario = load_scenario(name)
        except Exception as e:
            messagebox.showerror("Load failed", str(e))
            self.scenario_var.set(current_name)
            return
        self._apply_scenario_to_ui(scenario, loaded_name=name, clean=True)

    def _sync_scenario_settings(self):
        try:
            poll_interval = float(self.poll_var.get())
            monitor_index = int(self.monitor_var.get())
        except (TypeError, ValueError, tk.TclError) as exc:
            raise ValueError("Poll interval and monitor must be valid numbers.") from exc
        if not math.isfinite(poll_interval) or poll_interval < 0.01:
            raise ValueError("Poll interval must be a finite number of at least 0.01 seconds.")
        if monitor_index < 1:
            raise ValueError("Monitor must be 1 or greater.")
        self.scenario.poll_interval = poll_interval
        self.scenario.monitor_index = monitor_index
        self.scenario.kill_switch = self.kill_var.get().strip() or "f12"
        self.scenario.target_window_title = self.target_window_var.get().strip()

    def _open_scenario_settings(self):
        win = tk.Toplevel(self.root)
        win.title("Scenario settings")
        win.transient(self.root)
        win.grab_set()
        win.resizable(False, False)

        body = ttk.Frame(win, style="Surface.TFrame", padding=18)
        body.grid(row=0, column=0, sticky="nsew")
        body.columnconfigure(1, weight=1)
        poll_var = tk.DoubleVar(value=self.poll_var.get())
        monitor_var = tk.IntVar(value=self.monitor_var.get())
        kill_var = tk.StringVar(value=self.kill_var.get())

        ttk.Label(body, text="Poll interval", style="Surface.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 18), pady=6)
        ttk.Entry(body, textvariable=poll_var, width=12).grid(row=0, column=1, sticky="ew", pady=6)
        ttk.Label(body, text="Monitor", style="Surface.TLabel").grid(row=1, column=0, sticky="w", padx=(0, 18), pady=6)
        ttk.Entry(body, textvariable=monitor_var, width=12).grid(row=1, column=1, sticky="ew", pady=6)
        ttk.Label(body, text="Stop key", style="Surface.TLabel").grid(row=2, column=0, sticky="w", padx=(0, 18), pady=6)
        ttk.Entry(body, textvariable=kill_var, width=12).grid(row=2, column=1, sticky="ew", pady=6)

        buttons = ttk.Frame(body, style="Surface.TFrame")
        buttons.grid(row=3, column=0, columnspan=2, sticky="e", pady=(16, 0))

        def save_settings():
            try:
                poll = float(poll_var.get())
                monitor = int(monitor_var.get())
            except (tk.TclError, ValueError):
                messagebox.showerror("Invalid settings", "Poll interval and monitor must be numbers.", parent=win)
                return
            if not math.isfinite(poll) or poll < 0.01:
                messagebox.showerror(
                    "Invalid settings",
                    "Poll interval must be a finite number of at least 0.01 seconds.",
                    parent=win,
                )
                return
            if monitor < 1:
                messagebox.showerror("Invalid settings", "Monitor must be 1 or greater.", parent=win)
                return
            try:
                _monitor_box(monitor)
            except (RuntimeError, ValueError, OSError) as exc:
                messagebox.showerror("Invalid settings", str(exc), parent=win)
                return
            self.poll_var.set(poll)
            self.monitor_var.set(monitor)
            self.kill_var.set(kill_var.get().strip() or "f12")
            win.destroy()

        ttk.Button(buttons, text="Cancel", command=win.destroy).pack(side="left", padx=4)
        ttk.Button(buttons, text="Save", style="Primary.TButton", command=save_settings).pack(side="left", padx=4)

    def _toggle_activity(self):
        self._activity_visible = not self._activity_visible
        if self._activity_visible:
            self.activity_body.grid()
            self.activity_toggle.configure(text="Hide")
        else:
            self.activity_body.grid_remove()
            self.activity_toggle.configure(text="Show")

    def _validate_scenario_name_for_ui(self, name):
        try:
            return validate_scenario_name(name)
        except ValueError as exc:
            messagebox.showerror("Invalid scenario name", str(exc))
            return None

    def _new_scenario(self):
        if not self._require_stopped_for_scenario_change():
            return
        name = simpledialog.askstring("New scenario", "Scenario name:", parent=self.root)
        if not name:
            return
        name = self._validate_scenario_name_for_ui(name)
        if name is None:
            return
        collision = find_case_insensitive_name(list_scenarios(), name)
        if collision is not None:
            messagebox.showerror(
                "Duplicate name",
                f"A scenario named '{collision}' already exists (names are case-insensitive).",
                parent=self.root,
            )
            return
        if not self._confirm_save_before("creating a new scenario"):
            return
        self._apply_scenario_to_ui(Scenario(name=name), loaded_name=None, clean=False)

    def _save_scenario(self):
        try:
            self._sync_scenario_settings()
            self.scenario.name = validate_scenario_name(self.scenario.name)
            validate_scenario(self.scenario)
        except (TypeError, ValueError, tk.TclError) as exc:
            messagebox.showerror("Save failed", str(exc), parent=self.root)
            return False

        loaded_name = getattr(self, "_loaded_scenario_name", None)
        collision = find_case_insensitive_name(
            list_scenarios(),
            self.scenario.name,
            exclude_name=loaded_name,
        )
        if collision is not None:
            messagebox.showerror(
                "Scenario already exists",
                f"Saving would overwrite '{collision}'. Use a different name.",
                parent=self.root,
            )
            return False
        try:
            path = save_scenario(self.scenario)
        except Exception as exc:
            messagebox.showerror("Save failed", str(exc), parent=self.root)
            return False
        self._loaded_scenario_name = self.scenario.name
        self._mark_scenario_clean(loaded_name=self.scenario.name)
        self._refresh_scenario_list()
        self._log(f"Saved to {path}")
        return True

    def _save_scenario_as(self):
        if not self._require_stopped_for_scenario_change():
            return
        name = simpledialog.askstring("Save as", "New scenario name:",
                                       initialvalue=self.scenario.name, parent=self.root)
        if not name:
            return
        name = self._validate_scenario_name_for_ui(name)
        if name is None:
            return
        if name == self.scenario.name:
            self._save_scenario()
            return
        if name.casefold() == self.scenario.name.casefold():
            messagebox.showerror(
                "Duplicate name",
                "The new name differs only by letter case. Choose a distinct name.",
                parent=self.root,
            )
            return
        collision = find_case_insensitive_name(list_scenarios(), name)
        if collision is not None:
            messagebox.showerror(
                "Duplicate name",
                f"A scenario named '{collision}' already exists (names are case-insensitive).",
                parent=self.root,
            )
            return
        old_name = self.scenario.name
        old_loaded_name = getattr(self, "_loaded_scenario_name", None)
        self.scenario.name = name
        self.scenario_var.set(name)
        self._loaded_scenario_name = None
        if not self._save_scenario():
            self.scenario.name = old_name
            self.scenario_var.set(old_name)
            self._loaded_scenario_name = old_loaded_name

    def _duplicate_scenario(self):
        if not self._require_stopped_for_scenario_change():
            return
        try:
            self._sync_scenario_settings()
            validate_scenario(self.scenario)
        except (TypeError, ValueError, tk.TclError) as exc:
            messagebox.showerror("Duplicate failed", str(exc), parent=self.root)
            return
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
        collision = find_case_insensitive_name(list_scenarios(), name)
        if collision is not None:
            messagebox.showerror(
                "Duplicate name",
                f"A scenario named '{collision}' already exists (names are case-insensitive).",
                parent=self.root,
            )
            return
        try:
            duplicated = duplicate_scenario(self.scenario, name)
            path = save_scenario(duplicated, overwrite=False)
        except Exception as e:
            messagebox.showerror("Duplicate failed", str(e))
            return
        self._apply_scenario_to_ui(duplicated, loaded_name=duplicated.name, clean=True)
        self._refresh_scenario_list()
        self._log(f"Duplicated scenario to {path}")

    def _delete_scenario(self):
        if not self._require_stopped_for_scenario_change():
            return
        name = self.scenario.name
        if not name:
            return
        warning = f"Permanently delete scenario '{name}'?"
        if self._has_unsaved_changes():
            warning += "\n\nIts unsaved changes will also be discarded."
        if messagebox.askyesno("Delete", warning, parent=getattr(self, "root", None)):
            try:
                delete_scenario(name)
            except Exception as exc:
                messagebox.showerror("Delete failed", str(exc), parent=getattr(self, "root", None))
                return
            self._apply_scenario_to_ui(Scenario(name="untitled"), loaded_name=None, clean=True)
            self._refresh_scenario_list()

    # ---- step management ----
    def _selected_step_index(self):
        selection = self.steps_tree.selection()
        if not selection:
            return None
        try:
            return int(selection[0])
        except (TypeError, ValueError):
            return None

    def _refresh_steps(self):
        previous = self._selected_step_index()
        for item in self.steps_tree.get_children():
            self.steps_tree.delete(item)
        for index, step in enumerate(self.scenario.steps):
            self.steps_tree.insert(
                "",
                "end",
                iid=str(index),
                text=step.name,
                values=(
                    "Enabled" if step.enabled else "Disabled",
                    f"{len(step.conditions)} / {len(step.actions)}",
                ),
                tags=() if step.enabled else ("disabled",),
            )
        if self.scenario.steps:
            selected = previous if previous is not None and previous < len(self.scenario.steps) else 0
            self.steps_tree.selection_set(str(selected))
            self.steps_tree.focus(str(selected))
            self.steps_tree.see(str(selected))
        self._refresh_step_details()

    def _on_step_selected(self, _event=None):
        self._refresh_step_details()

    def _refresh_step_details(self):
        for tree in (self.condition_tree, self.action_tree):
            for item in tree.get_children():
                tree.delete(item)
        index = self._selected_step_index()
        if index is None or index >= len(self.scenario.steps):
            self.selected_step_name_var.set("Select a step")
            self.selected_step_meta_var.set("")
            return

        step = self.scenario.steps[index]
        state = "Enabled" if step.enabled else "Disabled"
        repeat = "Repeats" if step.repeatable else "Runs once"
        self.selected_step_name_var.set(step.name)
        self.selected_step_meta_var.set(
            f"{state}  |  {step.condition_operator} conditions  |  {repeat}  |  {step.cooldown:g}s cooldown"
        )
        for condition_index, condition in enumerate(step.conditions):
            name = os.path.basename(condition.template_path) or "Unnamed condition"
            mode = MATCH_MODE_LIST_TAGS.get(condition.match_mode, "Static")
            name = f"{name} [{mode}]"
            if condition.negate:
                rule = "Absent"
            elif condition.comparison_template_path:
                rule = f"Best +{condition.comparison_margin:.2f}"
            else:
                rule = f"{condition.confidence:.2f}+"
            if condition.region:
                scope = {
                    "window": "Window-relative",
                    "monitor": "Monitor-relative",
                }.get(condition.region_mode, "Absolute screen")
            else:
                scope = "Target" if self.scenario.target_window_title else "Full screen"
            self.condition_tree.insert("", "end", iid=str(condition_index), text=name, values=(rule, scope))
        for action_index, action in enumerate(step.actions):
            self.action_tree.insert(
                "",
                "end",
                iid=str(action_index),
                text=action_display_summary(action, step.conditions),
                values=(action_index + 1,),
            )

    def _edit_selected_condition(self):
        step_index = self._selected_step_index()
        selection = self.condition_tree.selection()
        if step_index is None or not selection:
            return
        condition_index = int(selection[0])
        step = self.scenario.steps[step_index]
        edited = condition_dialog(
            self.root,
            cond=step.conditions[condition_index],
            monitor_index=self.monitor_var.get(),
            target_window_title=self.target_window_var.get().strip(),
        )
        if edited:
            step.conditions[condition_index] = edited
            self._refresh_steps()
            self.condition_tree.selection_set(str(condition_index))

    def _edit_selected_action(self):
        step_index = self._selected_step_index()
        selection = self.action_tree.selection()
        if step_index is None or not selection:
            return
        action_index = int(selection[0])
        step = self.scenario.steps[step_index]
        edited = action_dialog(
            self.root,
            action=step.actions[action_index],
            step_names=[item.name for item in self.scenario.steps],
            num_conditions=len(step.conditions),
            conditions=step.conditions,
        )
        if edited:
            step.actions[action_index] = edited
            self._refresh_steps()
            self.action_tree.selection_set(str(action_index))

    def _add_step(self):
        existing = {s.name for s in self.scenario.steps}
        all_names = [s.name for s in self.scenario.steps]
        s = step_dialog(self.root, existing_names=existing, all_step_names=all_names,
                         monitor_index=self.monitor_var.get(),
                         target_window_title=self.target_window_var.get().strip())
        if s:
            self.scenario.steps.append(s)
            self._refresh_steps()
            new_index = len(self.scenario.steps) - 1
            self.steps_tree.selection_set(str(new_index))
            self.steps_tree.focus(str(new_index))

    def _edit_step(self):
        idx = self._selected_step_index()
        if idx is None:
            return
        old_name = self.scenario.steps[idx].name
        existing = {s.name for s in self.scenario.steps}
        all_names = [s.name for s in self.scenario.steps]
        s = step_dialog(self.root, step=self.scenario.steps[idx], existing_names=existing,
                         all_step_names=all_names, monitor_index=self.monitor_var.get(),
                         target_window_title=self.target_window_var.get().strip())
        if s:
            self.scenario.steps[idx] = s
            if s.name != old_name:
                rewrite_step_references(self.scenario.steps, old_name, s.name)
            self._refresh_steps()

    def _remove_step(self):
        index = self._selected_step_index()
        if index is not None and messagebox.askyesno("Remove step", "Remove the selected step?"):
            removed_name = self.scenario.steps[index].name
            del self.scenario.steps[index]
            changes = rewrite_step_references(self.scenario.steps, removed_name, None)
            self._refresh_steps()
            removed_refs = changes["removed_actions"] + changes["removed_list_entries"]
            if removed_refs:
                self._log(
                    f"Removed {removed_refs} action reference(s) to deleted step "
                    f"'{removed_name}'."
                )

    def _duplicate_step(self):
        index = self._selected_step_index()
        if index is None:
            return
        existing = {s.name for s in self.scenario.steps}
        copied = duplicate_step(self.scenario.steps[index], existing)
        self.scenario.steps.insert(index + 1, copied)
        self._refresh_steps()
        self.steps_tree.selection_set(str(index + 1))

    def _test_step(self):
        if self.engine and self.engine.is_running:
            messagebox.showwarning("Macro running", "Stop the macro before testing a step.")
            return
        index = self._selected_step_index()
        if index is None:
            messagebox.showinfo("No step selected", "Select a step to test first.")
            return
        try:
            self._sync_scenario_settings()
            validate_scenario(self.scenario, require_files=True)
        except (TypeError, ValueError, tk.TclError) as exc:
            messagebox.showerror("Test failed", str(exc), parent=self.root)
            return
        step = self.scenario.steps[index]

        engine = None
        try:
            engine = MacroEngine(self.scenario, log=self._queue_log)
            preview = engine.preview_step(step)
        except Exception as e:
            messagebox.showerror("Test failed", str(e))
            return
        finally:
            if engine is not None:
                engine.stop()

        self._show_step_preview(step, preview)

    def _show_step_regions(self):
        index = self._selected_step_index()
        if index is None:
            messagebox.showinfo("No step selected", "Select a step first.")
            return
        try:
            self._sync_scenario_settings()
        except (TypeError, ValueError, tk.TclError) as exc:
            messagebox.showerror("Show regions failed", str(exc), parent=self.root)
            return
        step = self.scenario.steps[index]

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
        idx = self._selected_step_index()
        if idx is None:
            return
        new_idx = idx + delta
        if 0 <= new_idx < len(self.scenario.steps):
            steps = self.scenario.steps
            steps[idx], steps[new_idx] = steps[new_idx], steps[idx]
            self._refresh_steps()
            self.steps_tree.selection_set(str(new_idx))

    # ---- engine control ----
    def _set_engine_stopped_ui(self):
        self._engine_ui_active = False
        self.run_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.status_label.config(text="Stopped", style="Stopped.Status.TLabel")

    def _start_engine(self):
        if self.engine and self.engine.is_running:
            return
        if not self.scenario.steps:
            messagebox.showwarning("No steps", "Add at least one step before running.")
            return
        engine = None
        try:
            self._sync_scenario_settings()
            validate_scenario(self.scenario, require_files=True)
            engine = MacroEngine(self.scenario, log=self._queue_log)
            engine.start()
        except Exception as e:
            if engine is not None:
                try:
                    engine.stop()
                except Exception:
                    pass
            self.engine = None
            self._set_engine_stopped_ui()
            messagebox.showerror("Failed to start", str(e))
            return
        self.engine = engine
        self._engine_ui_active = True
        self.run_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.status_label.config(text="Running", style="Running.Status.TLabel")

    def _stop_engine(self):
        if self.engine:
            try:
                self.engine.stop()
            except Exception as exc:
                self._queue_log(f"[error] failed to stop engine cleanly: {exc}")
        if self.engine and self.engine.is_running:
            self._engine_ui_active = True
            self.run_btn.config(state="disabled")
            self.stop_btn.config(state="disabled")
            self.status_label.config(text="Stopping...", style="Running.Status.TLabel")
            return False
        self._set_engine_stopped_ui()
        return True

    def _register_start_hotkey(self):
        try:
            self._start_hotkey_handle = keyboard.add_hotkey(
                START_MACRO_HOTKEY, self._request_start_from_hotkey
            )
        except Exception as exc:
            self._queue_log(
                f"[warn] could not register start hotkey "
                f"{START_MACRO_HOTKEY.upper()}: {exc}"
            )

    def _request_start_from_hotkey(self):
        self.control_queue.put("start")

    def _start_engine_from_hotkey(self):
        if self.engine and self.engine.is_running:
            return
        root = getattr(self, "root", None)
        try:
            if root is not None and root.grab_current() is not None:
                self._queue_log(
                    f"[safety] {START_MACRO_HOTKEY.upper()} ignored while a dialog is open"
                )
                return
        except tk.TclError:
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
        if getattr(self, "_engine_ui_active", False) and (
            self.engine is None or not self.engine.is_running
        ):
            self._set_engine_stopped_ui()
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
        if not self._confirm_save_before("closing"):
            return
        self._remove_start_hotkey()
        try:
            if self.engine and self.engine.is_running:
                self.engine.stop()
            if hasattr(self, "alert_tab"):
                self.alert_tab.shutdown()
        finally:
            self._close_log_file()
            self.root.destroy()


def main():
    instance_lock = SingleInstanceLock()
    lock_error: Optional[Exception]
    try:
        acquired = instance_lock.acquire()
    except Exception as exc:
        acquired = False
        lock_error = exc
    else:
        lock_error = None

    if not acquired:
        notice = tk.Tk()
        notice.withdraw()
        detail = f"\n\nLock error: {lock_error}" if lock_error is not None else ""
        messagebox.showwarning(
            "PC Macro Builder already running",
            "Another copy of PC Macro Builder or Icon Alert Watcher is already running."
            + detail,
            parent=notice,
        )
        notice.destroy()
        return 1

    root = None
    try:
        root = tk.Tk()
        try:
            App(root)
        except Exception as exc:
            try:
                os.makedirs(os.path.dirname(STARTUP_ERROR_LOG), exist_ok=True)
                with open(STARTUP_ERROR_LOG, "a", encoding="utf-8") as handle:
                    handle.write(f"\n[{datetime.now().isoformat(timespec='seconds')}]\n")
                    handle.write(traceback.format_exc())
            except OSError:
                pass
            root.withdraw()
            messagebox.showerror(
                "PC Macro Builder could not start",
                f"{type(exc).__name__}: {exc}\n\nDetails were written to:\n{STARTUP_ERROR_LOG}",
                parent=root,
            )
            return 1
        root.mainloop()
        return 0
    finally:
        if root is not None:
            try:
                if root.winfo_exists():
                    root.destroy()
            except tk.TclError:
                pass
        instance_lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
