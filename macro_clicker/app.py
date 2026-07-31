"""
PC Macro Builder -- main application.

A scenario is a list of Steps. Each Step has Conditions (images that
must/must-not be on screen) and Actions (click / key / wait / enable
or disable another step). Run it, watch the log, stop with the button
or your kill-switch key.
"""

import json
import math
import os
import queue
import threading
import tkinter as tk
import traceback
from datetime import datetime
from tkinter import messagebox, simpledialog, ttk
from typing import Any, Optional, cast

import keyboard
from PIL import ImageDraw, ImageTk

from .alert_watcher import AlertWatcherFrame, SingleInstanceLock
from .app_helpers import (
    duplicate_scenario,
    duplicate_step,
    find_case_insensitive_name,
    rewrite_step_references,
)
from .detection_core import MATCH_MODE_LIST_TAGS
from .engine import _WINDOW_UNAVAILABLE, MacroEngine
from .editors import (
    MultiRegionOverlay,
    _monitor_box,
    _parse_optional_int as _parse_optional_int,
    _parse_required_int as _parse_required_int,
    action_dialog,
    condition_dialog,
    resolve_condition_preview_box,
    schedule_mouse_position_fill as schedule_mouse_position_fill,
    step_dialog,
)
from .hotkeys import (
    canonical_hotkey,
    hotkeys_conflict,
    permissive_single_key_conflict,
)
from .log_maintenance import (
    DEFAULT_DEBUG_MAX_AGE_DAYS,
    DEFAULT_DEBUG_MAX_FILES,
    DEFAULT_LOG_BACKUPS,
    DEFAULT_MAX_LOG_BYTES,
    maintain_logs,
    rotate_log_file,
)
from .models import (
    Scenario,
    Step,
    delete_scenario,
    list_scenarios,
    load_scenario,
    save_scenario,
    validate_scenario,
    validate_scenario_name,
)
from .runtime_paths import LOG_DIR, STARTUP_ERROR_LOG
from .ui_components import (
    COLORS,
    StatusPulse,
    Tooltip,
    UiFeedback,
    action_button,
    action_display_summary,
    center_window,
    configure_theme,
    create_root,
)
from .ui_preferences import UiPreferences, load_ui_preferences, save_ui_preferences
from .window_locator import visible_window_titles

START_MACRO_HOTKEY = "f8"


def _start_stop_hotkeys_conflict(start_key, stop_key):
    """Apply the runtime's permissive single-key Stop behavior."""

    return permissive_single_key_conflict(stop_key, start_key)


class App:
    def __init__(self, root):
        self.root = root
        root.title("PC Macro Builder")
        root.geometry("1320x860")
        root.minsize(1100, 720)
        self._configure_style()

        self.ui_preferences = load_ui_preferences()
        self.ui_feedback = UiFeedback(enabled=self.ui_preferences.sounds_enabled)
        self._activity_animation_id = None
        self.status_pulse = None

        self.scenario = Scenario(name="untitled")
        self.engine: Optional[MacroEngine] = None
        self._clean_scenario_snapshot = None
        self._loaded_scenario_name = None
        self._engine_ui_active = False
        self._step_test_running = False
        self._runtime_locked_widget_states: Optional[dict[Any, bool]] = None
        self.log_queue = queue.Queue()
        self.control_queue = queue.Queue()
        self._start_hotkey_handle = None
        self._start_hotkey_registration_token = None
        self._start_request_generation = 0
        self._pending_start_request = None
        self._start_request_in_progress = False
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
            font=("Segoe UI Semibold", 10),
        )
        style.configure(
            "RunningPulse.Status.TLabel",
            background=COLORS["surface"],
            foreground=COLORS["success_bright"],
            font=("Segoe UI Semibold", 10),
        )
        style.configure(
            "Stopped.Status.TLabel",
            background=COLORS["surface"],
            foreground=COLORS["muted"],
            font=("Segoe UI Semibold", 10),
        )
        style.configure(
            "Preparing.Status.TLabel",
            background=COLORS["surface"],
            foreground=COLORS["warning"],
            font=("Segoe UI Semibold", 10),
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

        top = ttk.Frame(self.macro_tab, style="Card.TFrame", padding=(18, 14))
        top.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 8))
        top.columnconfigure(1, weight=1)

        ttk.Label(top, text="Scenario", style="Surface.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        self.scenario_var = tk.StringVar()
        self.scenario_combo = ttk.Combobox(
            top, textvariable=self.scenario_var, state="readonly", width=28
        )
        self.scenario_combo.grid(row=0, column=1, sticky="w", padx=(8, 14))
        self.scenario_combo.bind("<<ComboboxSelected>>", self._on_scenario_selected)
        new_btn = ttk.Button(
            top, text="New", style="Toolbar.TButton", command=self._new_scenario
        )
        new_btn.grid(row=0, column=2, padx=2)
        save_btn = ttk.Button(
            top, text="Save", style="Toolbar.TButton", command=self._save_scenario
        )
        save_btn.grid(row=0, column=3, padx=2)
        ttk.Button(
            top, text="Save as", style="Toolbar.TButton", command=self._save_scenario_as
        ).grid(row=0, column=4, padx=2)
        ttk.Button(
            top,
            text="Duplicate",
            style="Toolbar.TButton",
            command=self._duplicate_scenario,
        ).grid(row=0, column=5, padx=2)
        delete_btn = ttk.Button(
            top, text="Delete", style="Toolbar.TButton", command=self._delete_scenario
        )
        delete_btn.grid(row=0, column=6, padx=(2, 14))
        Tooltip(new_btn, "Create a scenario")
        Tooltip(save_btn, "Save the current scenario")
        Tooltip(delete_btn, "Delete the current scenario")

        self.status_label = ttk.Label(
            top, text="● Stopped", style="Stopped.Status.TLabel"
        )
        self.status_label.grid(row=0, column=7, padx=(8, 10))
        self.status_pulse = StatusPulse(
            self.status_label,
            ("Running.Status.TLabel", "RunningPulse.Status.TLabel"),
        )
        self.run_btn = action_button(top, text="Run", command=self._start_engine)
        self.run_btn.grid(row=0, column=8, padx=3)
        self.stop_btn = action_button(
            top,
            text="Stop",
            kind="danger",
            state="disabled",
            command=self._stop_engine,
        )
        self.stop_btn.grid(row=0, column=9, padx=3)
        self.run_tooltip = Tooltip(
            self.run_btn,
            f"Start the selected scenario ({self.scenario.start_hotkey.upper()})",
        )
        Tooltip(self.stop_btn, "Stop the running scenario")

        config = ttk.Frame(self.macro_tab, style="Card.TFrame", padding=(18, 12))
        config.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 8))
        config.columnconfigure(1, weight=1)
        ttk.Label(config, text="Target window", style="Surface.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        self.target_window_var = tk.StringVar(value=self.scenario.target_window_title)
        self.target_window_combo = ttk.Combobox(
            config, textvariable=self.target_window_var, width=48
        )
        self.target_window_combo.grid(row=0, column=1, sticky="ew", padx=(10, 6))
        refresh_btn = ttk.Button(
            config, text="Refresh", command=self._refresh_window_list
        )
        refresh_btn.grid(row=0, column=2, padx=3)
        settings_btn = ttk.Button(
            config, text="Scenario settings", command=self._open_scenario_settings
        )
        settings_btn.grid(row=0, column=3, padx=(8, 0))
        Tooltip(refresh_btn, "Refresh visible windows")

        self.poll_var = tk.DoubleVar(value=self.scenario.poll_interval)
        self.monitor_var = tk.IntVar(value=self.scenario.monitor_index)
        self.start_var = tk.StringVar(value=self.scenario.start_hotkey)
        self.kill_var = tk.StringVar(value=self.scenario.kill_switch)
        self.diagnostics_var = tk.BooleanVar(value=self.scenario.diagnostics_enabled)
        self._refresh_window_list()

        workspace = ttk.PanedWindow(self.macro_tab, orient="horizontal")
        workspace.grid(row=2, column=0, sticky="nsew", padx=12, pady=(0, 8))

        navigator = ttk.Frame(workspace, style="Card.TFrame", padding=16, width=340)
        inspector = ttk.Frame(workspace, style="Card.TFrame", padding=18)
        workspace.add(navigator, weight=1)
        workspace.add(inspector, weight=3)

        navigator.columnconfigure(0, weight=1)
        navigator.rowconfigure(2, weight=1)
        ttk.Label(navigator, text="Steps", style="Title.TLabel").grid(
            row=0, column=0, sticky="w"
        )
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
            button = ttk.Button(
                nav_tools, text=text, style="Icon.TButton", command=command
            )
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
        steps_scroll = ttk.Scrollbar(
            navigator,
            orient="vertical",
            command=self.steps_tree.yview,
        )
        steps_scroll.grid(row=2, column=1, sticky="ns")
        self.steps_tree.configure(yscrollcommand=steps_scroll.set)
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
        ttk.Label(
            header, textvariable=self.selected_step_name_var, style="Title.TLabel"
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            header, textvariable=self.selected_step_meta_var, style="Muted.TLabel"
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))
        ttk.Button(header, text="Edit step", command=self._edit_step).grid(
            row=0, column=1, rowspan=2, padx=(8, 4)
        )
        self.test_step_btn = ttk.Button(
            header,
            text="Test",
            command=self._test_step,
        )
        self.test_step_btn.grid(row=0, column=2, rowspan=2, padx=4)
        ttk.Button(header, text="Show regions", command=self._show_step_regions).grid(
            row=0, column=3, rowspan=2, padx=(4, 0)
        )

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
        ttk.Label(cond_header, text="Conditions", style="Section.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Button(
            cond_header, text="Edit", command=self._edit_selected_condition
        ).grid(row=0, column=1)
        self.condition_tree = ttk.Treeview(
            conditions_panel,
            columns=("rule", "scope"),
            show="tree headings",
            selectmode="browse",
        )
        self.condition_tree.heading("#0", text="Template")
        self.condition_tree.heading("rule", text="Match")
        self.condition_tree.heading("scope", text="Scope")
        self.condition_tree.column("#0", width=135, minwidth=95)
        self.condition_tree.column("rule", width=85, anchor="center")
        self.condition_tree.column("scope", width=78)
        self.condition_tree.grid(row=1, column=0, sticky="nsew")
        condition_scroll = ttk.Scrollbar(
            conditions_panel,
            orient="vertical",
            command=self.condition_tree.yview,
        )
        condition_scroll.grid(row=1, column=1, sticky="ns")
        self.condition_tree.configure(yscrollcommand=condition_scroll.set)
        self.condition_tree.bind(
            "<Double-1>", lambda _event: self._edit_selected_condition()
        )

        actions_panel = ttk.Frame(details, style="Surface.TFrame")
        actions_panel.grid(row=0, column=1, rowspan=2, sticky="nsew", padx=(7, 0))
        actions_panel.columnconfigure(0, weight=1)
        actions_panel.rowconfigure(1, weight=1)
        action_header = ttk.Frame(actions_panel, style="Surface.TFrame")
        action_header.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        action_header.columnconfigure(0, weight=1)
        ttk.Label(action_header, text="Actions", style="Section.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Button(action_header, text="Edit", command=self._edit_selected_action).grid(
            row=0, column=1
        )
        self.action_tree = ttk.Treeview(
            actions_panel, columns=("order",), show="tree headings", selectmode="browse"
        )
        self.action_tree.heading("#0", text="Action")
        self.action_tree.heading("order", text="#")
        self.action_tree.column("#0", width=300, minwidth=180)
        self.action_tree.column("order", width=38, anchor="center", stretch=False)
        self.action_tree.grid(row=1, column=0, sticky="nsew")
        action_scroll = ttk.Scrollbar(
            actions_panel,
            orient="vertical",
            command=self.action_tree.yview,
        )
        action_scroll.grid(row=1, column=1, sticky="ns")
        self.action_tree.configure(yscrollcommand=action_scroll.set)
        self.action_tree.bind("<Double-1>", lambda _event: self._edit_selected_action())

        activity = ttk.Frame(self.macro_tab, style="Card.TFrame", padding=(16, 10))
        activity.grid(row=3, column=0, sticky="ew", padx=12, pady=(0, 12))
        activity.columnconfigure(0, weight=1)
        activity_header = ttk.Frame(activity, style="Surface.TFrame")
        activity_header.grid(row=0, column=0, sticky="ew")
        activity_header.columnconfigure(0, weight=1)
        ttk.Label(activity_header, text="Activity", style="Section.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        self.activity_toggle = ttk.Button(
            activity_header,
            text="Collapse",
            style="Toolbar.TButton",
            command=self._toggle_activity,
        )
        self.activity_toggle.grid(row=0, column=1)
        self.activity_body = ttk.Frame(activity, style="Surface.TFrame")
        self.activity_body.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        self.activity_body.columnconfigure(0, weight=1)
        self.log_text = tk.Text(
            self.activity_body,
            height=7,
            state="disabled",
            bg=COLORS["surface_alt"],
            fg=COLORS["text"],
            insertbackground=COLORS["text"],
            selectbackground=COLORS["accent_soft"],
            relief="flat",
            borderwidth=0,
            highlightthickness=1,
            highlightbackground=COLORS["border"],
            highlightcolor=COLORS["accent"],
            font=("Cascadia Mono", 9),
            wrap="none",
        )
        log_scroll = ttk.Scrollbar(
            self.activity_body, orient="vertical", command=self.log_text.yview
        )
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.grid(row=0, column=0, sticky="ew")
        log_scroll.grid(row=0, column=1, sticky="ns")
        self._activity_visible = True
        self._activity_expanded_height = 7

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
            return self._scenario_snapshot() != getattr(
                self, "_clean_scenario_snapshot", None
            )
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
        if not self._engine_is_active_or_stopping():
            return True
        messagebox.showwarning(
            "Macro running",
            "Stop the macro before changing scenario settings, loading, "
            "duplicating, or deleting a scenario.",
            parent=self.root,
        )
        return False

    def _apply_scenario_to_ui(self, scenario, loaded_name=None, clean=True):
        self.scenario = scenario
        self.scenario_var.set(scenario.name)
        self.poll_var.set(scenario.poll_interval)
        self.monitor_var.set(scenario.monitor_index)
        if hasattr(self, "start_var"):
            self.start_var.set(scenario.start_hotkey)
        self.kill_var.set(scenario.kill_switch)
        self.target_window_var.set(scenario.target_window_title)
        if hasattr(self, "diagnostics_var"):
            self.diagnostics_var.set(scenario.diagnostics_enabled)
        self._loaded_scenario_name = loaded_name
        self._refresh_steps()
        if hasattr(self, "_start_hotkey_handle"):
            self._register_start_hotkey()
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
            message = f"[warn] could not list windows: {e}"
            if hasattr(self, "log_text"):
                self._log(message)
            else:
                # Initial window enumeration runs before the activity widget
                # exists. Queue the warning so an optional provider failure
                # cannot turn into an AttributeError during application startup.
                self._queue_log(message)

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
            raise ValueError(
                "Poll interval and monitor must be valid numbers."
            ) from exc
        if not math.isfinite(poll_interval) or poll_interval < 0.01:
            raise ValueError(
                "Poll interval must be a finite number of at least 0.01 seconds."
            )
        if monitor_index < 1:
            raise ValueError("Monitor must be 1 or greater.")
        self.scenario.poll_interval = poll_interval
        self.scenario.monitor_index = monitor_index
        start_var = getattr(self, "start_var", None)
        self.scenario.start_hotkey = (
            start_var.get().strip()
            if start_var is not None
            else self.scenario.start_hotkey
        ) or START_MACRO_HOTKEY
        self.scenario.kill_switch = self.kill_var.get().strip() or "f12"
        self.scenario.target_window_title = self.target_window_var.get().strip()
        diagnostics_var = getattr(self, "diagnostics_var", None)
        if diagnostics_var is not None:
            self.scenario.diagnostics_enabled = bool(diagnostics_var.get())

    def _open_scenario_settings(self):
        if not self._require_stopped_for_scenario_change():
            return
        win = tk.Toplevel(self.root)
        win.title("Scenario settings")
        win.transient(self.root)
        win.grab_set()
        win.resizable(False, False)
        win.configure(background=COLORS["surface"])

        body = ttk.Frame(win, style="Surface.TFrame", padding=24)
        body.grid(row=0, column=0, sticky="nsew")
        body.columnconfigure(1, weight=1)
        poll_var = tk.DoubleVar(value=self.poll_var.get())
        monitor_var = tk.IntVar(value=self.monitor_var.get())
        start_var = tk.StringVar(value=self.start_var.get())
        kill_var = tk.StringVar(value=self.kill_var.get())
        diagnostics_var = tk.BooleanVar(value=self.diagnostics_var.get())
        sounds_var = tk.BooleanVar(value=self.ui_preferences.sounds_enabled)
        animations_var = tk.BooleanVar(value=self.ui_preferences.animations_enabled)

        ttk.Label(body, text="Scenario", style="Section.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 8)
        )
        ttk.Label(body, text="Poll interval", style="Surface.TLabel").grid(
            row=1, column=0, sticky="w", padx=(0, 24), pady=6
        )
        ttk.Entry(body, textvariable=poll_var, width=14).grid(
            row=1, column=1, sticky="ew", pady=6
        )
        ttk.Label(body, text="Monitor", style="Surface.TLabel").grid(
            row=2, column=0, sticky="w", padx=(0, 24), pady=6
        )
        ttk.Entry(body, textvariable=monitor_var, width=14).grid(
            row=2, column=1, sticky="ew", pady=6
        )
        ttk.Label(body, text="Start key", style="Surface.TLabel").grid(
            row=3, column=0, sticky="w", padx=(0, 24), pady=6
        )
        ttk.Entry(body, textvariable=start_var, width=14).grid(
            row=3, column=1, sticky="ew", pady=6
        )
        ttk.Label(body, text="Stop key", style="Surface.TLabel").grid(
            row=4, column=0, sticky="w", padx=(0, 24), pady=6
        )
        ttk.Entry(body, textvariable=kill_var, width=14).grid(
            row=4, column=1, sticky="ew", pady=6
        )
        ttk.Checkbutton(
            body,
            text="Collect bounded diagnostic screenshots",
            variable=diagnostics_var,
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(8, 4))

        ttk.Separator(body).grid(
            row=6, column=0, columnspan=2, sticky="ew", pady=(14, 12)
        )
        ttk.Label(body, text="Interface", style="Section.TLabel").grid(
            row=7, column=0, columnspan=2, sticky="w", pady=(0, 5)
        )
        ttk.Checkbutton(
            body,
            text="Play gentle interface sounds",
            variable=sounds_var,
        ).grid(row=8, column=0, columnspan=2, sticky="w", pady=2)
        ttk.Checkbutton(
            body,
            text="Use subtle interface animations",
            variable=animations_var,
        ).grid(row=9, column=0, columnspan=2, sticky="w", pady=2)

        buttons = ttk.Frame(body, style="Surface.TFrame")
        buttons.grid(row=10, column=0, columnspan=2, sticky="e", pady=(20, 0))

        def save_settings():
            # Recheck in case a non-UI caller started the engine while this
            # modal dialog was open.
            if not self._require_stopped_for_scenario_change():
                return
            try:
                poll = float(poll_var.get())
                monitor = int(monitor_var.get())
            except (tk.TclError, ValueError):
                messagebox.showerror(
                    "Invalid settings",
                    "Poll interval and monitor must be numbers.",
                    parent=win,
                )
                return
            if not math.isfinite(poll) or poll < 0.01:
                messagebox.showerror(
                    "Invalid settings",
                    "Poll interval must be a finite number of at least 0.01 seconds.",
                    parent=win,
                )
                return
            if monitor < 1:
                messagebox.showerror(
                    "Invalid settings", "Monitor must be 1 or greater.", parent=win
                )
                return
            start_key = start_var.get().strip() or START_MACRO_HOTKEY
            stop_key = kill_var.get().strip() or "f12"
            try:
                canonical_hotkey(start_key)
                canonical_hotkey(stop_key)
            except (ValueError, TypeError) as exc:
                messagebox.showerror(
                    "Invalid settings",
                    f"Start key or Stop key is invalid: {exc}",
                    parent=win,
                )
                return
            if _start_stop_hotkeys_conflict(start_key, stop_key):
                messagebox.showerror(
                    "Invalid settings",
                    "Start key and Stop key must not use overlapping physical "
                    "key sequences.",
                    parent=win,
                )
                return
            conflict = self._macro_alert_hotkey_conflict(start_key, stop_key)
            if conflict:
                messagebox.showerror("Hotkey conflict", conflict, parent=win)
                return
            try:
                _monitor_box(monitor)
            except (RuntimeError, ValueError, OSError) as exc:
                messagebox.showerror("Invalid settings", str(exc), parent=win)
                return
            self.poll_var.set(poll)
            self.monitor_var.set(monitor)
            self.start_var.set(start_key)
            self.kill_var.set(stop_key)
            self.diagnostics_var.set(bool(diagnostics_var.get()))
            self.ui_preferences = UiPreferences(
                sounds_enabled=bool(sounds_var.get()),
                animations_enabled=bool(animations_var.get()),
            )
            self.ui_feedback.enabled = self.ui_preferences.sounds_enabled
            try:
                save_ui_preferences(self.ui_preferences)
            except (OSError, TypeError, ValueError) as exc:
                self._queue_log(f"[warn] could not save interface preferences: {exc}")
            if hasattr(self, "alert_tab"):
                self.alert_tab.apply_ui_preferences(self.ui_preferences)
            if (
                not self.ui_preferences.animations_enabled
                and self.status_pulse is not None
            ):
                engine = self.engine
                engine_running = engine is not None and engine.is_running
                engine_ready = engine is not None and engine.is_ready
                if engine_ready:
                    final_style = "Running.Status.TLabel"
                elif engine_running:
                    final_style = "Preparing.Status.TLabel"
                else:
                    final_style = "Stopped.Status.TLabel"
                self.status_pulse.stop(final_style)
            elif (
                self.ui_preferences.animations_enabled
                and self.engine is not None
                and self.engine.is_ready
                and self.status_pulse is not None
            ):
                self.status_pulse.start()
            self._register_start_hotkey()
            self.ui_feedback.play("success")
            win.destroy()

        ttk.Button(buttons, text="Cancel", command=win.destroy).pack(
            side="left", padx=4
        )
        ttk.Button(
            buttons, text="Save", style="Primary.TButton", command=save_settings
        ).pack(side="left", padx=4)
        win.bind("<Escape>", lambda _event: win.destroy())
        win.bind("<Return>", lambda _event: save_settings())
        win.after_idle(lambda: center_window(win, self.root))

    def _toggle_activity(self):
        target_visible = not self._activity_visible
        self._activity_visible = target_visible
        self.activity_toggle.configure(text="Collapse" if target_visible else "Expand")

        after_id = getattr(self, "_activity_animation_id", None)
        if after_id is not None:
            try:
                self.root.after_cancel(after_id)
            except tk.TclError:
                pass
            self._activity_animation_id = None

        animate = getattr(
            getattr(self, "ui_preferences", None),
            "animations_enabled",
            False,
        )
        if not animate:
            if target_visible:
                self.activity_body.grid()
                self.log_text.configure(height=self._activity_expanded_height)
            else:
                self.activity_body.grid_remove()
            return

        if target_visible:
            self.log_text.configure(height=1)
            self.activity_body.grid()

        end_height = self._activity_expanded_height if target_visible else 1

        def animate_height():
            try:
                current = int(self.log_text.cget("height"))
                if current == end_height:
                    self._activity_animation_id = None
                    if not target_visible:
                        self.activity_body.grid_remove()
                    return
                current += 1 if current < end_height else -1
                self.log_text.configure(height=current)
                self._activity_animation_id = self.root.after(24, animate_height)
            except tk.TclError:
                self._activity_animation_id = None

        animate_height()

    def _validate_scenario_name_for_ui(self, name):
        try:
            return validate_scenario_name(name)
        except ValueError as exc:
            messagebox.showerror("Invalid scenario name", str(exc))
            return None

    def _new_scenario(self):
        if not self._require_stopped_for_scenario_change():
            return
        name = simpledialog.askstring(
            "New scenario", "Scenario name:", parent=self.root
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
        name = simpledialog.askstring(
            "Save as",
            "New scenario name:",
            initialvalue=self.scenario.name,
            parent=self.root,
        )
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
        parent = cast(tk.Misc, getattr(self, "root", None))
        if messagebox.askyesno("Delete", warning, parent=parent):
            try:
                delete_scenario(name)
            except Exception as exc:
                messagebox.showerror("Delete failed", str(exc), parent=parent)
                return
            self._apply_scenario_to_ui(
                Scenario(name="untitled"), loaded_name=None, clean=True
            )
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
            selected = (
                previous
                if previous is not None and previous < len(self.scenario.steps)
                else 0
            )
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
            self.condition_tree.insert(
                "", "end", iid=str(condition_index), text=name, values=(rule, scope)
            )
        for action_index, action in enumerate(step.actions):
            self.action_tree.insert(
                "",
                "end",
                iid=str(action_index),
                text=action_display_summary(action, step.conditions),
                values=(action_index + 1,),
            )

    def _edit_selected_condition(self):
        if not self._require_stopped_for_scenario_change():
            return
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
        if not self._require_stopped_for_scenario_change():
            return
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
        if not self._require_stopped_for_scenario_change():
            return
        existing = {s.name for s in self.scenario.steps}
        all_names = [s.name for s in self.scenario.steps]
        s = step_dialog(
            self.root,
            existing_names=existing,
            all_step_names=all_names,
            monitor_index=self.monitor_var.get(),
            target_window_title=self.target_window_var.get().strip(),
        )
        if s:
            self.scenario.steps.append(s)
            self._refresh_steps()
            new_index = len(self.scenario.steps) - 1
            self.steps_tree.selection_set(str(new_index))
            self.steps_tree.focus(str(new_index))

    def _edit_step(self):
        if not self._require_stopped_for_scenario_change():
            return
        idx = self._selected_step_index()
        if idx is None:
            return
        old_name = self.scenario.steps[idx].name
        existing = {s.name for s in self.scenario.steps}
        all_names = [s.name for s in self.scenario.steps]
        s = step_dialog(
            self.root,
            step=self.scenario.steps[idx],
            existing_names=existing,
            all_step_names=all_names,
            monitor_index=self.monitor_var.get(),
            target_window_title=self.target_window_var.get().strip(),
        )
        if s:
            self.scenario.steps[idx] = s
            if s.name != old_name:
                rewrite_step_references(self.scenario.steps, old_name, s.name)
            self._refresh_steps()

    def _remove_step(self):
        if not self._require_stopped_for_scenario_change():
            return
        index = self._selected_step_index()
        if index is not None and messagebox.askyesno(
            "Remove step", "Remove the selected step?"
        ):
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
        if not self._require_stopped_for_scenario_change():
            return
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
            messagebox.showwarning(
                "Macro running", "Stop the macro before testing a step."
            )
            return
        if self._step_test_running:
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
        scenario = Scenario.from_dict(self.scenario.to_dict())
        step = scenario.steps[index]
        self._step_test_running = True
        self.test_step_btn.configure(state="disabled", text="Testing...")
        worker = threading.Thread(
            target=self._run_step_preview_worker,
            args=(scenario, step),
            name="macro-step-preview",
            daemon=True,
        )
        try:
            worker.start()
        except Exception as exc:
            self._step_test_running = False
            self.test_step_btn.configure(state="normal", text="Test")
            messagebox.showerror(
                "Test failed",
                f"Could not start the preview worker:\n{exc}",
                parent=self.root,
            )

    def _run_step_preview_worker(self, scenario, step):
        engine = None
        preview = None
        error = None
        try:
            engine = MacroEngine(scenario, log=self._queue_log)
            preview = engine.preview_step(step)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        finally:
            if engine is not None:
                try:
                    engine.stop()
                except Exception as exc:
                    if error is None:
                        error = f"{type(exc).__name__}: {exc}"
        self.control_queue.put(("step_preview", step, preview, error))

    def _finish_step_preview(self, step, preview, error):
        self._step_test_running = False
        self.test_step_btn.configure(state="normal", text="Test")
        if error is not None:
            messagebox.showerror("Test failed", error, parent=self.root)
            return
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
        body = tk.Frame(win)
        body.pack(fill="both", expand=True)
        canvas = tk.Canvas(body, highlightthickness=0)
        scrollbar = ttk.Scrollbar(body, orient="vertical", command=canvas.yview)
        content = tk.Frame(canvas)
        content_window = canvas.create_window((0, 0), window=content, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        content.bind(
            "<Configure>",
            lambda _event: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.bind(
            "<Configure>",
            lambda event: canvas.itemconfigure(content_window, width=event.width),
        )

        summary = "MATCH" if preview["met"] else "NO MATCH"
        tk.Label(
            content,
            text=f"{summary}: {len(preview['matches'])} detection(s)",
            font=("Segoe UI", 11, "bold"),
            fg="#2e7d32" if preview["met"] else "#c62828",
        ).pack(anchor="w", padx=8, pady=6)

        previews = preview.get("condition_previews") or []
        if not previews:
            tk.Label(content, text="No screenshot available.").pack(
                anchor="w", padx=8, pady=4
            )
        else:
            images_frame = tk.Frame(content)
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
                    tk.Label(
                        images_frame, text="No screenshot available for this condition."
                    ).pack(anchor="w")
                    continue

                display_image = image.copy()
                draw = ImageDraw.Draw(display_image)
                for match in condition_preview["matches"]:
                    box = match.get("image_box", match["box"])
                    draw.rectangle(box, outline="lime", width=4)
                    text_pos = (box[0], max(0, box[1] - 18))
                    draw.text(text_pos, match["label"], fill="lime")

                max_w, max_h = 860, 180
                scale = min(
                    max_w / display_image.width, max_h / display_image.height, 1.0
                )
                if scale < 1.0:
                    display_image = display_image.resize(
                        (
                            int(display_image.width * scale),
                            int(display_image.height * scale),
                        )
                    )

                photo = ImageTk.PhotoImage(display_image)
                image_refs.append(photo)
                img_label = tk.Label(images_frame, image=photo)
                img_label.pack(anchor="w", pady=2)
            win._preview_image_refs = image_refs

        details = tk.Text(content, height=8, state="normal")
        details.pack(fill="x", padx=8, pady=8)
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
        if not self._require_stopped_for_scenario_change():
            return
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
    def _set_macro_editor_locked(self, locked):
        macro_tab = getattr(self, "macro_tab", None)
        if macro_tab is None:
            return
        if locked:
            if getattr(self, "_runtime_locked_widget_states", None) is not None:
                return
            states: dict[Any, bool] = {}
            stack = [macro_tab]
            excluded = {
                getattr(self, "run_btn", None),
                getattr(self, "stop_btn", None),
            }
            while stack:
                parent = stack.pop()
                try:
                    children = parent.winfo_children()
                except (AttributeError, tk.TclError):
                    continue
                stack.extend(children)
                for widget in children:
                    if widget in excluded or not isinstance(
                        widget,
                        (ttk.Button, ttk.Combobox, ttk.Treeview),
                    ):
                        continue
                    try:
                        states[widget] = bool(widget.instate(("disabled",)))
                        widget.state(["disabled"])
                    except tk.TclError:
                        pass
            self._runtime_locked_widget_states = states
            return

        saved_states: Optional[dict[Any, bool]] = getattr(
            self,
            "_runtime_locked_widget_states",
            None,
        )
        self._runtime_locked_widget_states = None
        if not saved_states:
            return
        for widget, was_disabled in saved_states.items():
            if was_disabled:
                continue
            try:
                widget.state(["!disabled"])
            except tk.TclError:
                pass

    def _set_engine_stopped_ui(self):
        was_active = getattr(self, "_engine_ui_active", False)
        self._engine_ui_active = False
        if self.status_pulse is not None:
            self.status_pulse.stop("Stopped.Status.TLabel")
        self.run_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self._set_macro_editor_locked(False)
        self.status_label.config(text="● Stopped", style="Stopped.Status.TLabel")
        if was_active:
            self.ui_feedback.play("stop")

    def _set_engine_running_ui(self):
        if getattr(self, "_engine_ready_announced", False):
            return
        self._engine_ready_announced = True
        self.status_label.config(text="● Running", style="Running.Status.TLabel")
        if self.ui_preferences.animations_enabled and self.status_pulse is not None:
            self.status_pulse.start()
        self.ui_feedback.play("start")

    def _start_engine(self):
        previous_start_state = getattr(self, "_start_request_in_progress", False)
        self._start_request_in_progress = True
        try:
            return self._start_engine_attempt()
        finally:
            self._start_request_in_progress = previous_start_state

    def _start_engine_attempt(self):
        self._invalidate_queued_start_requests()
        if self.engine and self.engine.is_running:
            return
        if not self.scenario.steps:
            messagebox.showwarning("No steps", "Add at least one step before running.")
            return
        engine = None
        try:
            self._sync_scenario_settings()
            conflict = self._macro_alert_hotkey_conflict(
                self.scenario.start_hotkey,
                self.scenario.kill_switch,
            )
            if conflict:
                raise ValueError(conflict)
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
            self.ui_feedback.play("error")
            messagebox.showerror("Failed to start", str(e))
            return
        self.engine = engine
        self._engine_ui_active = True
        self._engine_ready_announced = False
        self._set_macro_editor_locked(True)
        self.run_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        if engine.is_ready:
            self._set_engine_running_ui()
        else:
            self.status_label.config(
                text="◌ Preparing OCR…", style="Preparing.Status.TLabel"
            )

    def _stop_engine(self):
        self._invalidate_queued_start_requests()
        if self.engine:
            try:
                self.engine.request_stop()
            except Exception as exc:
                self._queue_log(f"[error] failed to stop engine cleanly: {exc}")
        if self.engine and self.engine.is_running:
            self._engine_ui_active = True
            if self.status_pulse is not None:
                self.status_pulse.stop("Preparing.Status.TLabel")
            self.run_btn.config(state="disabled")
            self.stop_btn.config(state="disabled")
            self.status_label.config(
                text="◌ Stopping…", style="Preparing.Status.TLabel"
            )
            return False
        if self.engine:
            self.engine.stop()
        self._set_engine_stopped_ui()
        return True

    def _register_start_hotkey(self):
        self._invalidate_queued_start_requests()
        old_handle = getattr(self, "_start_hotkey_handle", None)
        self._start_hotkey_handle = None
        self._registered_start_hotkey = None
        self._start_hotkey_registration_token = None
        if old_handle is not None:
            try:
                keyboard.remove_hotkey(old_handle)
            except Exception:
                pass
        scenario = getattr(self, "scenario", None)
        hotkey = (
            self.start_var.get().strip()
            if hasattr(self, "start_var")
            else getattr(scenario, "start_hotkey", START_MACRO_HOTKEY)
        ) or START_MACRO_HOTKEY
        stop_hotkey = (
            self.kill_var.get().strip()
            if hasattr(self, "kill_var")
            else getattr(scenario, "kill_switch", "f12")
        ) or "f12"
        conflict = self._macro_alert_hotkey_conflict(
            hotkey,
            stop_hotkey,
            include_stop=False,
        )
        if conflict:
            self._queue_log(f"[warn] start hotkey was not registered: {conflict}")
            return False
        registration_token = object()
        try:
            new_handle = keyboard.add_hotkey(
                hotkey,
                lambda token=registration_token: self._request_start_from_hotkey(token),
            )
        except Exception as exc:
            self._queue_log(
                f"[warn] could not register start hotkey {hotkey.upper()}: {exc}"
            )
            return False
        self._start_hotkey_handle = new_handle
        self._registered_start_hotkey = hotkey
        self._start_hotkey_registration_token = registration_token
        if hasattr(self, "run_tooltip"):
            self.run_tooltip.text = f"Start the selected scenario ({hotkey.upper()})"
        return True

    def _macro_alert_hotkey_conflict(
        self,
        start_hotkey,
        stop_hotkey,
        *,
        include_stop=True,
    ):
        alert_tab = getattr(self, "alert_tab", None)
        alert_settings = getattr(alert_tab, "settings", None)
        if alert_settings is None:
            return None
        macro_bindings = [("Macro start", start_hotkey)]
        if include_stop:
            macro_bindings.append(("Macro stop", stop_hotkey))
        alert_bindings = (
            ("Icon Alerts start/stop", alert_settings.start_stop_hotkey),
            ("Icon Alerts test", alert_settings.test_alert_hotkey),
        )
        for macro_label, macro_hotkey in macro_bindings:
            for alert_label, alert_hotkey in alert_bindings:
                try:
                    conflict = (
                        permissive_single_key_conflict(
                            macro_hotkey,
                            alert_hotkey,
                        )
                        if macro_label == "Macro stop"
                        else hotkeys_conflict(macro_hotkey, alert_hotkey)
                    )
                except (TypeError, ValueError):
                    # Alert registration reports its own malformed setting.
                    continue
                if conflict:
                    return (
                        f"{macro_label} and {alert_label} use overlapping physical "
                        f"key sequences ({macro_hotkey!r} / {alert_hotkey!r}). "
                        "Choose different hotkeys before running the macro."
                    )
        return None

    def _request_start_from_hotkey(self, registration_token=None):
        current_token = getattr(self, "_start_hotkey_registration_token", None)
        if registration_token is None:
            registration_token = current_token
        if registration_token is None or registration_token is not current_token:
            return
        if getattr(self, "_start_request_in_progress", False):
            return
        if self._engine_is_active_or_stopping():
            return
        generation = getattr(self, "_start_request_generation", 0)
        request_identity = (registration_token, generation)
        if getattr(self, "_pending_start_request", None) == request_identity:
            return
        self._pending_start_request = request_identity
        # Carry the registration identity through the Tk queue. A callback can
        # be valid when it runs on the keyboard thread, then become stale
        # before the UI thread consumes this command after a scenario switch
        # or a newer Run/Stop generation.
        self.control_queue.put(("start", registration_token, generation))

    def _invalidate_queued_start_requests(self):
        self._start_request_generation = (
            getattr(self, "_start_request_generation", 0) + 1
        )
        self._pending_start_request = None

    def _engine_is_active_or_stopping(self):
        engine = getattr(self, "engine", None)
        return bool(
            getattr(self, "_engine_ui_active", False)
            or (engine is not None and engine.is_running)
        )

    def _start_engine_from_hotkey(self):
        # Recheck on the Tk thread. Multiple callbacks may already be queued,
        # and a queued callback must not restart a run that is still stopping.
        if self._engine_is_active_or_stopping():
            return
        root = getattr(self, "root", None)
        try:
            if root is not None and root.grab_current() is not None:
                registered_hotkey = (
                    getattr(self, "_registered_start_hotkey", None)
                    or START_MACRO_HOTKEY
                )
                self._queue_log(
                    f"[safety] {registered_hotkey.upper()} "
                    "ignored while a dialog is open"
                )
                return
        except tk.TclError:
            return
        self._start_engine()

    def _remove_start_hotkey(self):
        self._invalidate_queued_start_requests()
        self._start_hotkey_registration_token = None
        self._registered_start_hotkey = None
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
                if (
                    isinstance(command, tuple)
                    and len(command) == 3
                    and command[0] == "start"
                ):
                    registration_token = command[1]
                    generation = command[2]
                    current_token = getattr(
                        self, "_start_hotkey_registration_token", None
                    )
                    current_generation = getattr(self, "_start_request_generation", 0)
                    if (
                        registration_token is not None
                        and registration_token is current_token
                        and generation == current_generation
                    ):
                        self._start_request_in_progress = True
                        self._pending_start_request = None
                        try:
                            self._start_engine_from_hotkey()
                        finally:
                            self._start_request_in_progress = False
                elif (
                    isinstance(command, tuple)
                    and len(command) == 4
                    and command[0] == "step_preview"
                ):
                    self._finish_step_preview(*command[1:])
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
            if self.engine is not None:
                self.engine.stop()
            self._set_engine_stopped_ui()
        elif (
            getattr(self, "_engine_ui_active", False)
            and self.engine is not None
            and self.engine.is_ready
        ):
            self._set_engine_running_ui()
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
                rotate_log_file(
                    self.log_file_path, self.log_max_bytes, self.log_backups
                )
                self._log_file_handle = open(self.log_file_path, "a", encoding="utf-8")
            self._log_file_handle.write(line + "\n")
            self._log_write_count += 1
            if self._log_write_count % 20 == 0:
                self._log_file_handle.flush()
            if self._log_write_count % 100 == 0 and os.path.exists(self.log_file_path):
                if os.path.getsize(self.log_file_path) >= self.log_max_bytes:
                    self._close_log_file()
                    rotate_log_file(
                        self.log_file_path, self.log_max_bytes, self.log_backups
                    )
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
        if self.status_pulse is not None:
            self.status_pulse.stop()
        activity_animation = getattr(self, "_activity_animation_id", None)
        if activity_animation is not None:
            try:
                self.root.after_cancel(activity_animation)
            except tk.TclError:
                pass
            self._activity_animation_id = None
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
        if lock_error is None:
            messagebox.showwarning(
                "PC Macro Builder already running",
                "Another copy of PC Macro Builder or Icon Alert Watcher is "
                "already running.",
                parent=notice,
            )
        else:
            messagebox.showerror(
                "PC Macro Builder could not start",
                "The application could not acquire its single-instance lock. "
                "No second copy was started.\n\n"
                f"{type(lock_error).__name__}: {lock_error}",
                parent=notice,
            )
        notice.destroy()
        return 1

    root = None
    try:
        root = create_root()
        try:
            App(root)
        except Exception as exc:
            try:
                os.makedirs(os.path.dirname(STARTUP_ERROR_LOG), exist_ok=True)
                with open(STARTUP_ERROR_LOG, "a", encoding="utf-8") as handle:
                    handle.write(
                        f"\n[{datetime.now().isoformat(timespec='seconds')}]\n"
                    )
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
