"""Modal editors for macro conditions, actions, and steps."""

import copy
import math
import os
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any, Optional

import mss

from .app_helpers import (
    duplicate_template_file,
    find_case_insensitive_name,
    remap_condition_references,
)
from .capture_tool import capture_template, select_region
from .detection_core import (
    LEGACY_MACRO_MATCH_MODE,
    MATCH_MODE_BY_LABEL,
    MATCH_MODE_LABELS,
    MATCH_MODE_LIST_TAGS,
    monitor_rect,
    physical_monitor_index,
)
from .engine import _WINDOW_UNAVAILABLE
from .models import (
    Action,
    ImageCondition,
    Scenario,
    Step,
    has_smart_rally_team_prefilter,
    portable_project_path,
    project_path,
    validate_scenario,
)
from .rally_team_policy import (
    LEGACY_RALLY_TEAM_PRIORITY,
    THREE_TEAM_RALLY_PRIORITY,
    validate_rally_team_priority,
)
from .ui_components import (
    COLORS,
    CollapsibleSection,
    action_display_summary,
    center_window,
    condition_choice_for_index,
    condition_choices,
    condition_index_from_choice,
    preserved_level_roi,
    row_advanced_options_configured,
    row_max_level_editor_state,
)
from .window_locator import (
    find_window_rect,
    proportional_region_from_window,
    relative_region_from_window,
    resolve_saved_capture_region,
)

RALLY_TEAM_MODE_LEGACY = "Two teams: Team 3 -> Team 1"
RALLY_TEAM_MODE_THREE = "Three teams: Team 3 -> Team 2 -> Team 1"


def rally_team_editor_modes(priority):
    """Return the selected editor mode and exact serialized priority values."""
    validated = validate_rally_team_priority(priority)
    options = {
        RALLY_TEAM_MODE_LEGACY: None,
        RALLY_TEAM_MODE_THREE: list(THREE_TEAM_RALLY_PRIORITY),
    }
    if validated is None or tuple(validated) == LEGACY_RALLY_TEAM_PRIORITY:
        return RALLY_TEAM_MODE_LEGACY, options
    if tuple(validated) == THREE_TEAM_RALLY_PRIORITY:
        return RALLY_TEAM_MODE_THREE, options
    custom_label = "Custom priority: " + " -> ".join(str(team) for team in validated)
    options[custom_label] = validated
    return custom_label, options


def rally_team_max_level_editor_values(action):
    """Return Team 1/2/3 editor text, preserving blank as unlimited."""
    return tuple(
        "" if maximum is None else str(maximum)
        for maximum in (
            action.team1_max_level,
            action.team2_max_level,
            action.team3_max_level,
        )
    )


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


def _cleanup_captured_templates(captured_paths, keep_template_path=None):
    """Remove captures created by a dialog unless the saved condition uses one."""
    keep_paths = (keep_template_path,) if keep_template_path else ()
    _cleanup_created_templates(captured_paths, keep_paths)


def _cleanup_created_templates(created_paths, keep_template_paths=()):
    """Remove only files this editor created and no saved condition still uses."""
    keep_paths = {
        os.path.normcase(os.path.abspath(project_path(path)))
        for path in keep_template_paths
        if path
    }
    for captured_path in created_paths:
        absolute_path = os.path.normcase(os.path.abspath(captured_path))
        if absolute_path in keep_paths:
            continue
        try:
            os.remove(captured_path)
        except OSError:
            pass


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


def _schedule_dialog_fit(window, parent):
    """Resize a dynamic dialog after Tk applies visibility changes."""

    def fit():
        try:
            if window.winfo_exists():
                center_window(window, parent)
        except tk.TclError:
            pass

    try:
        window.after_idle(fit)
    except tk.TclError:
        pass


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


def resolve_condition_preview_box(
    cond: ImageCondition,
    target_window_title="",
    monitor_index=1,
    window_rect_provider=find_window_rect,
):
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

        self.canvas = tk.Canvas(
            self, bg=transparent, highlightthickness=0, cursor="hand2"
        )
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
                x0 + 2,
                text_y,
                text=text,
                fill="#000000",
                anchor="w",
                font=("Segoe UI", 12, "bold"),
            )
            self.canvas.create_text(
                x0,
                text_y - 2,
                text=text,
                fill=color,
                anchor="w",
                font=("Segoe UI", 12, "bold"),
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


def condition_dialog(
    parent,
    cond: Optional[ImageCondition] = None,
    monitor_index=1,
    target_window_title="",
    created_template_paths=None,
):
    win = tk.Toplevel(parent)
    win.title("Edit Condition" if cond else "Add Condition")
    win.transient(parent)
    win.grab_set()
    win.resizable(True, True)
    win.configure(background=COLORS["surface"])
    result: dict[str, Optional[ImageCondition]] = {"value": None}
    captured_template_paths = []

    template_var = tk.StringVar(value=cond.template_path if cond else "")
    confidence_var = tk.DoubleVar(value=cond.confidence if cond else 0.85)
    comparison_template_var = tk.StringVar(
        value=cond.comparison_template_path if cond else ""
    )
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
    region_ratio_holder = {
        "ratio": list(cond.region_ratio) if (cond and cond.region_ratio) else None
    }
    region_window_size_holder = {
        "size": list(cond.region_window_size)
        if (cond and cond.region_window_size)
        else None
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

    ttk.Label(win, text="Template", style="Surface.TLabel").grid(
        row=0, column=0, sticky="w", **pad
    )
    template_entry = ttk.Entry(win, textvariable=template_var, width=42)
    template_entry.grid(row=0, column=1, columnspan=2, sticky="we", **pad)

    def browse():
        path = filedialog.askopenfilename(
            filetypes=[("PNG images", "*.png")], initialdir="templates", parent=win
        )
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
            captured_template_paths.append(path)
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

    ttk.Label(win, text="Confidence", style="Surface.TLabel").grid(
        row=2, column=0, sticky="w", **pad
    )
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
        on_toggle=lambda: _schedule_dialog_fit(win, parent),
    )
    advanced_matching.grid(
        row=3, column=0, columnspan=3, sticky="ew", padx=6, pady=(4, 2)
    )
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

    ttk.Label(win, text="Search region", style="Surface.TLabel").grid(
        row=5, column=0, sticky="w", **pad
    )
    ttk.Label(win, textvariable=region_var, style="Muted.TLabel").grid(
        row=5, column=1, sticky="w", **pad
    )

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
            box = resolve_condition_preview_box(
                temp_cond, target_window_title, monitor_index
            )
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
        label = (
            os.path.basename(template_var.get())
            if template_var.get()
            else "Search region"
        )
        if negate_var.get():
            label = f"NOT {label}"
        MultiRegionOverlay(
            win, [(box, label, "#ff9800" if negate_var.get() else "#ffcc00")]
        )

    ttk.Button(win, text="Show", command=show_region).grid(
        row=6, column=0, sticky="we", **pad
    )
    ttk.Button(win, text="Pick region", command=pick_region).grid(
        row=6, column=1, sticky="we", **pad
    )
    ttk.Button(win, text="Clear", command=clear_region).grid(
        row=6, column=2, sticky="we", **pad
    )

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
    ttk.Button(btns, text="Save", style="Primary.TButton", command=on_ok).pack(
        side="left", padx=4
    )
    win.bind("<Escape>", lambda _event: win.destroy())
    _schedule_dialog_fit(win, parent)

    win.wait_window()
    saved_condition = result["value"]
    _cleanup_captured_templates(
        captured_template_paths,
        None if saved_condition is None else saved_condition.template_path,
    )
    if saved_condition is not None and created_template_paths is not None:
        saved_path = os.path.normcase(
            os.path.abspath(project_path(saved_condition.template_path))
        )
        created_template_paths.extend(
            path
            for path in captured_template_paths
            if os.path.normcase(os.path.abspath(path)) == saved_path
        )
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
    win.transient(parent)
    win.grab_set()
    win.resizable(True, True)
    win.configure(background=COLORS["surface"])
    result: dict[str, Optional[Action]] = {"value": None}
    a = action or Action(type="click")
    step_names = step_names or []
    conditions = list(conditions or [])
    condition_values = condition_choices(conditions)

    action_type_labels = {
        "click": "Click",
        "click_matching_row": "Click matching row",
        "capture_rally_team_status": "Capture / clear Rally team status",
        "select_rally_team": "Select rally team",
        "gather_control": "Gather control",
        "key": "Press key",
        "wait": "Wait",
        "set_step": "Enable / disable step",
        "stop": "Stop scenario",
    }
    action_type_values = {label: value for value, label in action_type_labels.items()}

    ttk.Label(win, text="Action type", style="Surface.TLabel").grid(
        row=0, column=0, sticky="w", padx=10, pady=10
    )
    type_var = tk.StringVar(value=action_type_labels.get(a.type, "Click"))
    type_combo = ttk.Combobox(
        win,
        textvariable=type_var,
        values=list(action_type_values.keys()),
        state="readonly",
        width=22,
    )
    type_combo.grid(row=0, column=1, sticky="w", padx=6, pady=6)

    body = ttk.Frame(win, style="Surface.TFrame")
    body.grid(row=1, column=0, columnspan=2, sticky="we", padx=6, pady=4)

    click_frame = ttk.LabelFrame(body, text="Click")
    row_click_frame = ttk.LabelFrame(body, text="Click matching row")
    team_status_frame = ttk.LabelFrame(body, text="Fixed Rally team status")
    team_frame = ttk.LabelFrame(body, text="Select rally team")
    gather_frame = ttk.LabelFrame(body, text="Gather control")
    key_frame = ttk.LabelFrame(body, text="Key press")
    wait_frame = ttk.LabelFrame(body, text="Wait")
    step_frame = ttk.LabelFrame(body, text="Enable / disable a step")
    stop_frame = ttk.LabelFrame(body, text="Stop scenario")
    frames = {
        "click": click_frame,
        "click_matching_row": row_click_frame,
        "capture_rally_team_status": team_status_frame,
        "select_rally_team": team_frame,
        "gather_control": gather_frame,
        "key": key_frame,
        "wait": wait_frame,
        "set_step": step_frame,
        "stop": stop_frame,
    }

    ttk.Label(
        stop_frame,
        text="End the running scenario after this step matches.",
        style="Surface.TLabel",
    ).grid(row=0, column=0, sticky="w", padx=8, pady=8)

    # --- click fields ---
    cond_idx_var = tk.StringVar(
        value=condition_choice_for_index(
            conditions, a.on_condition_index, "Automatic target"
        )
    )
    x_var = tk.StringVar(value=str(a.x) if a.x is not None else "")
    y_var = tk.StringVar(value=str(a.y) if a.y is not None else "")
    offx_var = tk.IntVar(value=a.offset_x)
    offy_var = tk.IntVar(value=a.offset_y)
    button_var = tk.StringVar(value=a.button)

    ttk.Label(click_frame, text="Click target", style="Surface.TLabel").grid(
        row=0, column=0, sticky="w", padx=4, pady=2
    )
    ttk.Combobox(
        click_frame,
        textvariable=cond_idx_var,
        values=["Automatic target"] + condition_values,
        state="readonly",
        width=30,
    ).grid(row=0, column=1, columnspan=3, sticky="w", padx=4)
    ttk.Label(click_frame, text="Fixed point x", style="Surface.TLabel").grid(
        row=2, column=0, sticky="w", padx=4, pady=(8, 2)
    )
    ttk.Entry(click_frame, textvariable=x_var, width=7).grid(
        row=2, column=1, sticky="w"
    )
    ttk.Label(click_frame, text="y", style="Surface.TLabel").grid(
        row=2, column=2, sticky="w"
    )
    ttk.Entry(click_frame, textvariable=y_var, width=7).grid(
        row=2, column=3, sticky="w"
    )
    ttk.Button(
        click_frame,
        text="Use mouse position (2s)",
        command=lambda: schedule_mouse_position_fill(win, x_var, y_var),
    ).grid(row=2, column=4, sticky="w", padx=(8, 4))
    ttk.Label(click_frame, text="Offset x", style="Surface.TLabel").grid(
        row=3, column=0, sticky="w", padx=4, pady=2
    )
    ttk.Entry(click_frame, textvariable=offx_var, width=7).grid(
        row=3, column=1, sticky="w"
    )
    ttk.Label(click_frame, text="y", style="Surface.TLabel").grid(
        row=3, column=2, sticky="w"
    )
    ttk.Entry(click_frame, textvariable=offy_var, width=7).grid(
        row=3, column=3, sticky="w"
    )
    ttk.Label(click_frame, text="Button", style="Surface.TLabel").grid(
        row=4, column=0, sticky="w", padx=4, pady=2
    )
    ttk.Combobox(
        click_frame,
        textvariable=button_var,
        values=["left", "right", "middle"],
        state="readonly",
        width=8,
    ).grid(row=4, column=1, sticky="w")

    # --- click matching row fields ---
    match_idx_var = tk.StringVar(
        value=condition_choice_for_index(
            conditions, a.match_condition_index, "Select condition"
        )
    )
    row_cond_idx_var = tk.StringVar(
        value=condition_choice_for_index(
            conditions, a.on_condition_index, "Select condition"
        )
    )
    row_tolerance_var = tk.IntVar(value=a.row_tolerance)
    row_mode_var = tk.StringVar(value=a.row_mode)
    target_choice_var = tk.StringVar(value=a.target_choice)
    pre_click_delay_var = tk.DoubleVar(value=getattr(a, "pre_click_delay", 0.0))
    row_offx_var = tk.IntVar(value=a.offset_x)
    row_offy_var = tk.IntVar(value=a.offset_y)
    row_button_var = tk.StringVar(value=a.button)
    min_level_var = tk.StringVar(
        value=str(a.min_level) if a.min_level is not None else ""
    )
    smart_row_limits = has_smart_rally_team_prefilter(a)
    max_level_state, max_level_label = row_max_level_editor_state(a)
    max_level_var = tk.StringVar(
        value=("" if smart_row_limits or a.max_level is None else str(a.max_level))
    )
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

    ttk.Label(row_click_frame, text="Row reference", style="Surface.TLabel").grid(
        row=0, column=0, sticky="w", padx=4, pady=2
    )
    ttk.Combobox(
        row_click_frame,
        textvariable=match_idx_var,
        values=condition_values,
        state="readonly",
        width=30,
    ).grid(row=0, column=1, columnspan=3, sticky="w")
    ttk.Label(row_click_frame, text="Click target", style="Surface.TLabel").grid(
        row=1, column=0, sticky="w", padx=4, pady=2
    )
    ttk.Combobox(
        row_click_frame,
        textvariable=row_cond_idx_var,
        values=condition_values,
        state="readonly",
        width=30,
    ).grid(row=1, column=1, columnspan=3, sticky="w")
    ttk.Label(row_click_frame, text="Row tolerance", style="Surface.TLabel").grid(
        row=2, column=0, sticky="w", padx=4, pady=2
    )
    ttk.Entry(row_click_frame, textvariable=row_tolerance_var, width=7).grid(
        row=2, column=1, sticky="w"
    )
    ttk.Label(row_click_frame, text="Rows", style="Surface.TLabel").grid(
        row=3, column=0, sticky="w", padx=4, pady=2
    )
    ttk.Combobox(
        row_click_frame,
        textvariable=row_mode_var,
        values=["first", "all"],
        state="readonly",
        width=8,
    ).grid(row=3, column=1, sticky="w")
    ttk.Label(row_click_frame, text="Target choice", style="Surface.TLabel").grid(
        row=4, column=0, sticky="w", padx=4, pady=2
    )
    ttk.Combobox(
        row_click_frame,
        textvariable=target_choice_var,
        values=["leftmost", "rightmost", "nearest"],
        state="readonly",
        width=10,
    ).grid(row=4, column=1, sticky="w")
    ttk.Label(row_click_frame, text="Offset x", style="Surface.TLabel").grid(
        row=5, column=0, sticky="w", padx=4, pady=2
    )
    ttk.Entry(row_click_frame, textvariable=row_offx_var, width=7).grid(
        row=5, column=1, sticky="w"
    )
    ttk.Label(row_click_frame, text="y", style="Surface.TLabel").grid(
        row=5, column=2, sticky="w"
    )
    ttk.Entry(row_click_frame, textvariable=row_offy_var, width=7).grid(
        row=5, column=3, sticky="w"
    )
    ttk.Label(row_click_frame, text="Button", style="Surface.TLabel").grid(
        row=6, column=0, sticky="w", padx=4, pady=2
    )
    ttk.Combobox(
        row_click_frame,
        textvariable=row_button_var,
        values=["left", "right", "middle"],
        state="readonly",
        width=8,
    ).grid(row=6, column=1, sticky="w")
    ttk.Label(row_click_frame, text="Min level", style="Surface.TLabel").grid(
        row=7, column=0, sticky="w", padx=4, pady=(8, 2)
    )
    ttk.Entry(row_click_frame, textvariable=min_level_var, width=7).grid(
        row=7, column=1, sticky="w"
    )
    ttk.Label(
        row_click_frame,
        text=max_level_label,
        style="Surface.TLabel",
    ).grid(row=7, column=2, sticky="w")
    ttk.Entry(
        row_click_frame,
        textvariable=max_level_var,
        width=7,
        state=max_level_state,
    ).grid(row=7, column=3, sticky="w")
    ttk.Label(
        row_click_frame, text="Level box x / y / w / h", style="Surface.TLabel"
    ).grid(row=8, column=0, sticky="w", padx=4, pady=2)
    ttk.Entry(row_click_frame, textvariable=level_roi_x_var, width=7).grid(
        row=8, column=1, sticky="w"
    )
    ttk.Entry(row_click_frame, textvariable=level_roi_y_var, width=7).grid(
        row=8, column=2, sticky="w"
    )
    ttk.Entry(row_click_frame, textvariable=level_roi_w_var, width=7).grid(
        row=8, column=3, sticky="w"
    )
    ttk.Entry(row_click_frame, textvariable=level_roi_h_var, width=7).grid(
        row=8, column=4, sticky="w"
    )
    ttk.Label(row_click_frame, text="No-row click", style="Surface.TLabel").grid(
        row=9, column=0, sticky="w", padx=4, pady=(8, 2)
    )
    ttk.Combobox(
        row_click_frame,
        textvariable=no_match_cond_idx_var,
        values=["None"] + condition_values,
        state="readonly",
        width=30,
    ).grid(row=9, column=1, columnspan=3, sticky="w")
    ttk.Label(row_click_frame, text="Then disable", style="Surface.TLabel").grid(
        row=10, column=0, sticky="w", padx=4, pady=2
    )
    ttk.Entry(row_click_frame, textvariable=no_match_disable_steps_var, width=34).grid(
        row=10, column=1, columnspan=4, sticky="we"
    )
    ttk.Label(
        row_click_frame,
        text="Delay after level check",
        style="Surface.TLabel",
    ).grid(row=11, column=0, sticky="w", padx=4, pady=2)
    ttk.Entry(row_click_frame, textvariable=pre_click_delay_var, width=7).grid(
        row=11, column=1, sticky="w"
    )
    ttk.Label(row_click_frame, text="seconds", style="Surface.TLabel").grid(
        row=11, column=2, sticky="w"
    )

    advanced_rows = (2, 5, 7, 8, 9, 10, 11)
    advanced_widgets = [
        widget
        for row in advanced_rows
        for widget in row_click_frame.grid_slaves(row=row)
    ]
    advanced_configured = row_advanced_options_configured(a)
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
            text="Hide advanced options"
            if row_advanced_state["expanded"]
            else "Show advanced options"
        )

    def toggle_row_advanced():
        row_advanced_state["expanded"] = not row_advanced_state["expanded"]
        if row_advanced_state["expanded"]:
            row_advanced_state["opened"] = True
        render_row_advanced()
        _schedule_dialog_fit(win, parent)

    row_advanced_btn = ttk.Button(
        row_click_frame,
        text="",
        style="Disclosure.TButton",
        command=toggle_row_advanced,
    )
    row_advanced_btn.grid(row=12, column=0, columnspan=5, sticky="ew", pady=(8, 0))
    render_row_advanced()

    # --- smart rally-team fields ---
    team_anchor_var = tk.StringVar(
        value=condition_choice_for_index(
            conditions,
            a.on_condition_index,
            "Select condition",
        )
    )
    idle_template_var = tk.StringVar(value=getattr(a, "team_idle_template_path", ""))
    team1_idle_template_var = tk.StringVar(
        value=getattr(a, "team1_idle_template_path", "")
    )
    team3_idle_template_var = tk.StringVar(
        value=getattr(a, "team3_idle_template_path", "")
    )
    idle_confidence_var = tk.DoubleVar(value=getattr(a, "team_idle_confidence", 0.85))
    team_button_var = tk.StringVar(value=a.button)
    team1_region = getattr(a, "team1_idle_region", None) or [-249, 130, 40, 36]
    team1_offset = getattr(a, "team1_click_offset", None) or [-189, 168]
    team2_offset = getattr(a, "team2_click_offset", None) or [-63, 168]
    team3_region = getattr(a, "team3_idle_region", None) or [3, 130, 40, 36]
    team3_offset = getattr(a, "team3_click_offset", None) or [63, 168]
    team1_region_vars = [tk.IntVar(value=value) for value in team1_region]
    team1_offset_vars = [tk.IntVar(value=value) for value in team1_offset]
    team2_offset_vars = [tk.IntVar(value=value) for value in team2_offset]
    team3_region_vars = [tk.IntVar(value=value) for value in team3_region]
    team3_offset_vars = [tk.IntVar(value=value) for value in team3_offset]
    team1_max_text, team2_max_text, team3_max_text = rally_team_max_level_editor_values(
        a
    )
    team1_max_var = tk.StringVar(value=team1_max_text)
    team2_max_var = tk.StringVar(value=team2_max_text)
    team3_max_var = tk.StringVar(value=team3_max_text)
    team_mode_label, team_mode_options = rally_team_editor_modes(a.team_priority)
    team_mode_var = tk.StringVar(value=team_mode_label)
    team_dry_run_var = tk.BooleanVar(value=getattr(a, "rally_team_dry_run", False))

    ttk.Label(team_frame, text="Anchor condition", style="Surface.TLabel").grid(
        row=0, column=0, sticky="w", padx=4, pady=2
    )
    ttk.Combobox(
        team_frame,
        textvariable=team_anchor_var,
        values=condition_values,
        state="readonly",
        width=30,
    ).grid(row=0, column=1, columnspan=4, sticky="w")
    ttk.Label(
        team_frame,
        text="Shared idle template (legacy fallback)",
        style="Surface.TLabel",
    ).grid(row=1, column=0, sticky="w", padx=4, pady=2)
    ttk.Entry(team_frame, textvariable=idle_template_var, width=34).grid(
        row=1, column=1, columnspan=3, sticky="we"
    )

    def browse_idle_template(variable):
        path = filedialog.askopenfilename(
            filetypes=[("PNG images", "*.png")],
            initialdir="templates",
            parent=win,
        )
        if path:
            variable.set(portable_project_path(path))

    def idle_template_browse_command(variable: tk.StringVar):
        def browse() -> None:
            browse_idle_template(variable)

        return browse

    ttk.Button(
        team_frame,
        text="Browse",
        command=lambda: browse_idle_template(idle_template_var),
    ).grid(row=1, column=4, sticky="w", padx=4)
    for row, label, variable in (
        (2, "Team 1 idle template", team1_idle_template_var),
        (3, "Team 3 idle template", team3_idle_template_var),
    ):
        ttk.Label(team_frame, text=label, style="Surface.TLabel").grid(
            row=row, column=0, sticky="w", padx=4, pady=2
        )
        ttk.Entry(team_frame, textvariable=variable, width=34).grid(
            row=row, column=1, columnspan=3, sticky="we"
        )
        ttk.Button(
            team_frame,
            text="Browse",
            command=idle_template_browse_command(variable),
        ).grid(row=row, column=4, sticky="w", padx=4)
    ttk.Label(team_frame, text="Idle confidence", style="Surface.TLabel").grid(
        row=4, column=0, sticky="w", padx=4, pady=2
    )
    ttk.Spinbox(
        team_frame,
        textvariable=idle_confidence_var,
        from_=0.5,
        to=1.0,
        increment=0.01,
        width=8,
    ).grid(row=4, column=1, sticky="w")
    ttk.Label(team_frame, text="Button", style="Surface.TLabel").grid(
        row=4, column=2, sticky="w", padx=(10, 4)
    )
    ttk.Combobox(
        team_frame,
        textvariable=team_button_var,
        values=["left", "right", "middle"],
        state="readonly",
        width=8,
    ).grid(row=4, column=3, sticky="w")

    ttk.Label(team_frame, text="Team policy", style="Surface.TLabel").grid(
        row=5, column=0, sticky="w", padx=4, pady=(8, 2)
    )
    ttk.Combobox(
        team_frame,
        textvariable=team_mode_var,
        values=tuple(team_mode_options),
        state="readonly",
        width=43,
    ).grid(row=5, column=1, columnspan=4, sticky="w")
    for column, (label, variable) in enumerate(
        (
            ("Team 1 max level", team1_max_var),
            ("Team 2 max level", team2_max_var),
            ("Team 3 max level", team3_max_var),
        )
    ):
        grid_column = column * 2
        ttk.Label(team_frame, text=label, style="Surface.TLabel").grid(
            row=6, column=grid_column, sticky="w", padx=4, pady=(8, 2)
        )
        ttk.Entry(team_frame, textvariable=variable, width=9).grid(
            row=7, column=grid_column, sticky="w", padx=4
        )

    ttk.Label(team_frame, text="Team 2 click offset x/y", style="Surface.TLabel").grid(
        row=8, column=0, sticky="w", padx=4, pady=(8, 2)
    )
    ttk.Entry(team_frame, textvariable=team2_offset_vars[0], width=7).grid(
        row=8, column=1, sticky="w"
    )
    ttk.Entry(team_frame, textvariable=team2_offset_vars[1], width=7).grid(
        row=8, column=2, sticky="w"
    )
    ttk.Checkbutton(
        team_frame,
        text="Three-team dry run (log selection; do not click or dispatch)",
        variable=team_dry_run_var,
    ).grid(row=9, column=0, columnspan=5, sticky="w", padx=4, pady=2)

    def add_team_row(row, label, region_vars, offset_vars):
        ttk.Label(team_frame, text=label, style="Surface.TLabel").grid(
            row=row, column=0, sticky="w", padx=4, pady=(8, 2)
        )
        ttk.Label(team_frame, text="Idle x/y/w/h", style="Surface.TLabel").grid(
            row=row + 1, column=0, sticky="w", padx=4, pady=2
        )
        for column, variable in enumerate(region_vars, start=1):
            ttk.Entry(team_frame, textvariable=variable, width=7).grid(
                row=row + 1, column=column, sticky="w"
            )
        ttk.Label(team_frame, text="Click offset x/y", style="Surface.TLabel").grid(
            row=row + 2, column=0, sticky="w", padx=4, pady=2
        )
        ttk.Entry(team_frame, textvariable=offset_vars[0], width=7).grid(
            row=row + 2, column=1, sticky="w"
        )
        ttk.Entry(team_frame, textvariable=offset_vars[1], width=7).grid(
            row=row + 2, column=2, sticky="w"
        )

    add_team_row(
        10,
        "Team 3 visual controls",
        team3_region_vars,
        team3_offset_vars,
    )
    add_team_row(
        14,
        "Team 1 visual controls",
        team1_region_vars,
        team1_offset_vars,
    )
    ttk.Label(
        team_frame,
        text=(
            "These max levels also control which OCR levels Joining accepts.\n"
            "Three-team mode uses fixed slot identity; hero portraits are ignored.\n"
            "Use the main Save button to keep changes after restart."
        ),
        style="Muted.TLabel",
        justify="left",
    ).grid(row=18, column=0, columnspan=5, sticky="w", padx=4, pady=(8, 2))

    # --- fixed Rally team status snapshot fields ---
    status_mode_labels = {
        "Capture pre-entry snapshot": "capture",
        "Clear transient snapshot": "clear",
    }
    status_mode_var = tk.StringVar(
        value=next(
            (
                label
                for label, value in status_mode_labels.items()
                if value == getattr(a, "rally_team_status_mode", "capture")
            ),
            "Capture pre-entry snapshot",
        )
    )
    status_ttl_var = tk.DoubleVar(value=getattr(a, "rally_team_snapshot_ttl", 3.0))
    ttk.Label(team_status_frame, text="Operation", style="Surface.TLabel").grid(
        row=0, column=0, sticky="w", padx=4, pady=2
    )
    ttk.Combobox(
        team_status_frame,
        textvariable=status_mode_var,
        values=list(status_mode_labels),
        state="readonly",
        width=28,
    ).grid(row=0, column=1, sticky="w", padx=4)
    ttk.Label(
        team_status_frame, text="Snapshot TTL (seconds)", style="Surface.TLabel"
    ).grid(row=1, column=0, sticky="w", padx=4, pady=2)
    ttk.Spinbox(
        team_status_frame,
        textvariable=status_ttl_var,
        from_=0.1,
        to=30.0,
        increment=0.1,
        width=8,
    ).grid(row=1, column=1, sticky="w", padx=4)

    # --- resource-gathering state fields ---
    gather_command_labels = {
        "Select replacement march": "select_replacement",
        "Record successful dispatch": "record_success",
        "Cancel / retry same dispatch": "cancel_retry",
    }
    gather_command_values = {
        label: value for label, value in gather_command_labels.items()
    }
    gather_command_label = next(
        (
            label
            for label, value in gather_command_labels.items()
            if value == getattr(a, "gather_command", "record_success")
        ),
        "Record successful dispatch",
    )
    gather_command_var = tk.StringVar(value=gather_command_label)
    gather_anchor_var = tk.StringVar(
        value=condition_choice_for_index(
            conditions, a.on_condition_index, "Select condition"
        )
    )
    gather_target_count_var = tk.IntVar(value=getattr(a, "gather_target_count", 3))
    gather_order_var = tk.StringVar(
        value=", ".join(
            str(march) for march in getattr(a, "gather_replacement_order", [3, 2, 1])
        )
    )
    ttk.Label(gather_frame, text="Command", style="Surface.TLabel").grid(
        row=0, column=0, sticky="w", padx=4, pady=2
    )
    ttk.Combobox(
        gather_frame,
        textvariable=gather_command_var,
        values=list(gather_command_values.keys()),
        state="readonly",
        width=28,
    ).grid(row=0, column=1, sticky="w", padx=4)
    ttk.Label(gather_frame, text="Anchor condition", style="Surface.TLabel").grid(
        row=1, column=0, sticky="w", padx=4, pady=2
    )
    ttk.Combobox(
        gather_frame,
        textvariable=gather_anchor_var,
        values=condition_values,
        state="readonly",
        width=30,
    ).grid(row=1, column=1, sticky="w", padx=4)
    ttk.Label(gather_frame, text="Successful dispatches", style="Surface.TLabel").grid(
        row=2, column=0, sticky="w", padx=4, pady=2
    )
    ttk.Spinbox(
        gather_frame,
        textvariable=gather_target_count_var,
        from_=1,
        to=20,
        increment=1,
        width=8,
    ).grid(row=2, column=1, sticky="w", padx=4)
    ttk.Label(gather_frame, text="Replacement order", style="Surface.TLabel").grid(
        row=3, column=0, sticky="w", padx=4, pady=2
    )
    ttk.Entry(gather_frame, textvariable=gather_order_var, width=18).grid(
        row=3, column=1, sticky="w", padx=4
    )
    ttk.Label(
        gather_frame,
        text=(
            "The gather state is reset whenever the scenario starts. "
            "Only Select replacement march uses the anchor condition."
        ),
        style="Muted.TLabel",
        wraplength=380,
        justify="left",
    ).grid(row=4, column=0, columnspan=2, sticky="w", padx=4, pady=(6, 2))

    # --- key fields ---
    key_var = tk.StringVar(value=a.key)
    hold_var = tk.DoubleVar(value=a.hold)
    ttk.Label(key_frame, text="Key", style="Surface.TLabel").grid(
        row=0, column=0, sticky="w", padx=4, pady=2
    )
    ttk.Entry(key_frame, textvariable=key_var, width=16).grid(
        row=0, column=1, sticky="w"
    )
    ttk.Label(key_frame, text="Hold duration", style="Surface.TLabel").grid(
        row=1, column=0, sticky="w", padx=4, pady=2
    )
    ttk.Entry(key_frame, textvariable=hold_var, width=8).grid(
        row=1, column=1, sticky="w"
    )

    # --- wait fields ---
    seconds_var = tk.DoubleVar(value=a.seconds)
    seconds_max_var = tk.DoubleVar(
        value=a.seconds if a.seconds_max is None else a.seconds_max
    )
    ttk.Label(wait_frame, text="Minimum seconds", style="Surface.TLabel").grid(
        row=0, column=0, sticky="w", padx=4, pady=2
    )
    ttk.Spinbox(
        wait_frame,
        textvariable=seconds_var,
        from_=0.0,
        to=86400.0,
        increment=0.1,
        width=8,
    ).grid(row=0, column=1, sticky="w")
    ttk.Label(wait_frame, text="Maximum seconds", style="Surface.TLabel").grid(
        row=1, column=0, sticky="w", padx=4, pady=2
    )
    ttk.Spinbox(
        wait_frame,
        textvariable=seconds_max_var,
        from_=0.0,
        to=86400.0,
        increment=0.1,
        width=8,
    ).grid(row=1, column=1, sticky="w")
    ttk.Label(
        wait_frame,
        text="A new value is chosen in 0.1-second steps each time this action runs.",
        style="Muted.TLabel",
        wraplength=330,
    ).grid(row=2, column=0, columnspan=2, sticky="w", padx=4, pady=(6, 2))

    # --- set_step fields ---
    step_name_var = tk.StringVar(value=a.step_name)
    set_enabled_var = tk.BooleanVar(value=a.set_enabled)
    ttk.Label(step_frame, text="Step", style="Surface.TLabel").grid(
        row=0, column=0, sticky="w", padx=4, pady=2
    )
    ttk.Combobox(
        step_frame, textvariable=step_name_var, values=step_names, width=22
    ).grid(row=0, column=1, sticky="w")
    ttk.Checkbutton(step_frame, text="Enable step", variable=set_enabled_var).grid(
        row=1, column=0, columnspan=2, sticky="w", padx=4
    )

    def show_frame(*_):
        for f in frames.values():
            f.grid_forget()
        action_type = action_type_values.get(type_var.get(), "click")
        frames[action_type].grid(row=0, column=0, sticky="we")
        _schedule_dialog_fit(win, parent)

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
                new_action.match_condition_index = condition_index_from_choice(
                    mi, "Row reference"
                )
                new_action.on_condition_index = condition_index_from_choice(
                    ci, "Click target"
                )
                new_action.row_tolerance = row_tolerance_var.get()
                new_action.row_mode = row_mode_var.get()
                new_action.target_choice = target_choice_var.get()
                new_action.pre_click_delay = pre_click_delay_var.get()
                new_action.offset_x = row_offx_var.get()
                new_action.offset_y = row_offy_var.get()
                new_action.button = row_button_var.get()
                new_action.min_level = _parse_optional_int(
                    min_level_var.get(), "Min level"
                )
                new_action.max_level = (
                    None
                    if smart_row_limits
                    else _parse_optional_int(max_level_var.get(), "Max level")
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
                # Team-availability prefiltering is scenario-calibrated from
                # full-monitor screenshots. Preserve that calibration when a
                # user edits the ordinary row/level fields in this dialog.
                new_action.team_status_region = copy.deepcopy(a.team_status_region)
                new_action.team_status_reference_size = copy.deepcopy(
                    a.team_status_reference_size
                )
                new_action.team1_busy_template_path = a.team1_busy_template_path
                new_action.team3_busy_template_path = a.team3_busy_template_path
                new_action.team_busy_confidence = a.team_busy_confidence
                new_action.team1_max_level = None
                new_action.team2_max_level = None
                new_action.team3_max_level = None
                new_action.team_priority = None
            elif t == "capture_rally_team_status":
                new_action.rally_team_status_mode = status_mode_labels[
                    status_mode_var.get()
                ]
                new_action.rally_team_snapshot_ttl = status_ttl_var.get()
            elif t == "select_rally_team":
                anchor = team_anchor_var.get().strip()
                if anchor in ("", "Select condition"):
                    raise ValueError("Choose the dispatch/attack anchor condition.")
                shared_idle_template = idle_template_var.get().strip()
                team1_idle_template = team1_idle_template_var.get().strip()
                team3_idle_template = team3_idle_template_var.get().strip()
                selected_priority = team_mode_options[team_mode_var.get()]
                if selected_priority is None:
                    if not (team1_idle_template or shared_idle_template):
                        raise ValueError("Choose the Team 1 idle icon template.")
                    if not (team3_idle_template or shared_idle_template):
                        raise ValueError("Choose the Team 3 idle icon template.")
                new_action.on_condition_index = condition_index_from_choice(
                    anchor,
                    "Anchor condition",
                )
                new_action.team_idle_template_path = shared_idle_template
                new_action.team1_idle_template_path = team1_idle_template
                new_action.team3_idle_template_path = team3_idle_template
                new_action.team_idle_confidence = idle_confidence_var.get()
                new_action.button = team_button_var.get()
                new_action.team1_idle_region = [
                    variable.get() for variable in team1_region_vars
                ]
                new_action.team1_click_offset = [
                    variable.get() for variable in team1_offset_vars
                ]
                new_action.team1_max_level = _parse_optional_int(
                    team1_max_var.get(),
                    "Team 1 max level",
                )
                new_action.team2_max_level = _parse_optional_int(
                    team2_max_var.get(),
                    "Team 2 max level",
                )
                new_action.team2_click_offset = [
                    variable.get() for variable in team2_offset_vars
                ]
                new_action.team3_idle_region = [
                    variable.get() for variable in team3_region_vars
                ]
                new_action.team3_click_offset = [
                    variable.get() for variable in team3_offset_vars
                ]
                new_action.team3_max_level = _parse_optional_int(
                    team3_max_var.get(),
                    "Team 3 max level",
                )
                new_action.team_priority = copy.deepcopy(selected_priority)
                new_action.rally_team_dry_run = team_dry_run_var.get()
            elif t == "gather_control":
                new_action.gather_command = gather_command_values[
                    gather_command_var.get()
                ]
                new_action.gather_target_count = gather_target_count_var.get()
                try:
                    new_action.gather_replacement_order = [
                        int(part.strip())
                        for part in gather_order_var.get().split(",")
                        if part.strip()
                    ]
                except ValueError as exc:
                    raise ValueError(
                        "Replacement order must be comma-separated march numbers"
                    ) from exc
                if new_action.gather_command == "select_replacement":
                    new_action.on_condition_index = condition_index_from_choice(
                        gather_anchor_var.get(),
                        "Gather replacement anchor",
                    )
            elif t == "key":
                if not key_var.get().strip():
                    messagebox.showerror("Missing key", "Enter a key name.", parent=win)
                    return
                new_action.key = key_var.get().strip()
                new_action.hold = hold_var.get()
            elif t == "wait":
                new_action.seconds = seconds_var.get()
                maximum = seconds_max_var.get()
                new_action.seconds_max = (
                    None if maximum == new_action.seconds else maximum
                )
            elif t == "set_step":
                if not step_name_var.get().strip():
                    messagebox.showerror(
                        "Missing step", "Choose a step name.", parent=win
                    )
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
    ttk.Button(btns, text="Save", style="Primary.TButton", command=on_ok).pack(
        side="left", padx=4
    )
    win.bind("<Escape>", lambda _event: win.destroy())
    _schedule_dialog_fit(win, parent)

    win.wait_window()
    return result["value"]


# ----------------------------------------------------------------------
# Step editor dialog
# ----------------------------------------------------------------------


def step_dialog(
    parent,
    step: Optional[Step] = None,
    existing_names=None,
    all_step_names=None,
    monitor_index=1,
    target_window_title="",
):
    win = tk.Toplevel(parent)
    win.title("Edit Step" if step else "Add Step")
    win.transient(parent)
    win.grab_set()
    win.resizable(True, True)
    win.configure(background=COLORS["surface"])
    result: dict[str, Optional[Step]] = {"value": None}

    s = step or Step(name="")
    # Work on independent copies so Cancel never leaks edits into the scenario.
    conditions = copy.deepcopy(s.conditions)
    actions = copy.deepcopy(s.actions)
    created_template_paths = []
    existing_names = existing_names or set()
    all_step_names = all_step_names or []

    pad: dict[str, Any] = {"padx": 6, "pady": 4}

    name_var = tk.StringVar(value=s.name)
    enabled_var = tk.BooleanVar(value=s.enabled)
    operator_var = tk.StringVar(value=s.condition_operator)
    cooldown_var = tk.DoubleVar(value=s.cooldown)
    repeatable_var = tk.BooleanVar(value=s.repeatable)

    ttk.Label(win, text="Step name", style="Surface.TLabel").grid(
        row=0, column=0, sticky="w", **pad
    )
    ttk.Entry(win, textvariable=name_var, width=28).grid(
        row=0, column=1, sticky="w", **pad
    )
    ttk.Checkbutton(win, text="Enabled at scenario start", variable=enabled_var).grid(
        row=0, column=2, columnspan=2, sticky="w", **pad
    )

    ttk.Label(win, text="Condition rule", style="Surface.TLabel").grid(
        row=1, column=0, sticky="w", **pad
    )
    ttk.Combobox(
        win, textvariable=operator_var, values=["AND", "OR"], state="readonly", width=6
    ).grid(row=1, column=1, sticky="w", **pad)
    ttk.Label(win, text="Cooldown", style="Surface.TLabel").grid(
        row=1, column=2, sticky="w", **pad
    )
    ttk.Entry(win, textvariable=cooldown_var, width=8).grid(
        row=1, column=3, sticky="w", **pad
    )
    ttk.Checkbutton(win, text="Repeat while matched", variable=repeatable_var).grid(
        row=1, column=4, sticky="w", **pad
    )

    ttk.Label(win, text="Conditions", style="Section.TLabel").grid(
        row=2, column=0, sticky="w", **pad
    )
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
            created_template_paths=created_template_paths,
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
            created_template_paths=created_template_paths,
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
            messagebox.showinfo(
                "Not a template",
                "Select a template condition to duplicate its image.",
                parent=win,
            )
            return
        name = simpledialog.askstring(
            "Duplicate template",
            "New template image name:",
            initialvalue=os.path.splitext(os.path.basename(original.template_path))[0]
            + "_copy",
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
        created_template_paths.append(new_path)
        conditions.append(copied)
        refresh_conditions()

    cbtns = ttk.Frame(win, style="Surface.TFrame")
    cbtns.grid(row=4, column=0, columnspan=5, sticky="w", padx=6, pady=(0, 8))
    ttk.Button(cbtns, text="Add", command=add_condition).pack(side="left", padx=2)
    ttk.Button(cbtns, text="Edit", command=edit_condition).pack(side="left", padx=2)
    ttk.Button(
        cbtns, text="Duplicate template", command=duplicate_condition_template
    ).pack(side="left", padx=2)
    ttk.Button(cbtns, text="Remove", command=remove_condition).pack(side="left", padx=2)

    ttk.Label(win, text="Actions", style="Section.TLabel").grid(
        row=5, column=0, columnspan=3, sticky="w", **pad
    )
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
            act_listbox.insert(
                tk.END, f"{i + 1}. {action_display_summary(act, conditions)}"
            )

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
    ttk.Button(abtns, text="Move up", command=lambda: move_action(-1)).pack(
        side="left", padx=2
    )
    ttk.Button(abtns, text="Move down", command=lambda: move_action(1)).pack(
        side="left", padx=2
    )

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
            name=nm,
            conditions=conditions,
            actions=actions,
            condition_operator=operator_var.get(),
            enabled=enabled_var.get(),
            cooldown=cooldown,
            repeatable=repeatable_var.get(),
        )
        win.destroy()

    btns = ttk.Frame(win, style="Surface.TFrame")
    btns.grid(row=8, column=0, columnspan=5, sticky="e", padx=6, pady=12)
    ttk.Button(btns, text="Cancel", command=win.destroy).pack(side="left", padx=4)
    ttk.Button(btns, text="Save", style="Primary.TButton", command=on_save).pack(
        side="left", padx=4
    )
    win.bind("<Escape>", lambda _event: win.destroy())
    _schedule_dialog_fit(win, parent)

    win.wait_window()
    saved_step = result["value"]
    _cleanup_created_templates(
        created_template_paths,
        (
            condition.template_path
            for condition in saved_step.conditions
            if condition.template_path
        )
        if saved_step is not None
        else (),
    )
    return result["value"]
