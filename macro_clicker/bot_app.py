"""Dedicated bot-style application shell layered over the existing App backend."""

from __future__ import annotations

import os
import tkinter as tk
import traceback
from datetime import datetime
from tkinter import messagebox, ttk
from typing import Optional

import keyboard

from .alert_watcher import SingleInstanceLock
from .app import App
from .bot.adapters import configured_scenario
from .bot.controller import FEATURE_GATHER
from .bot.ui import BotFrame
from .engine import MacroEngine
from .models import validate_scenario
from .runtime_paths import STARTUP_ERROR_LOG
from .ui_components import create_root


class BotApp(App):
    """Normal-user bot shell that keeps the original editors as advanced tools."""

    def __init__(self, root):
        self._advanced_tools_visible = False
        super().__init__(root)
        root.title("PC Automation Bot")

    def _build_ui(self):
        super()._build_ui()
        self.notebook.tab(self.macro_tab, text="Advanced")
        self.notebook.tab(self.alert_tab, text="Alert Setup")
        self.notebook.pack_forget()

        self.bot_surface = ttk.Frame(self.root)
        self.bot_surface.pack(fill="both", expand=True)
        self.bot_surface.columnconfigure(0, weight=1)
        self.bot_surface.rowconfigure(0, weight=1)
        self.bot_frame = BotFrame(
            self.bot_surface,
            host=self,
            alert_frame=self.alert_tab,
            show_advanced=self._show_advanced_tools,
            show_alert_setup=self._show_alert_setup,
        )
        self.bot_frame.grid(row=0, column=0, sticky="nsew")

        self.tool_header = ttk.Frame(self.root, style="Card.TFrame", padding=(12, 8))
        ttk.Button(
            self.tool_header,
            text="← Back to Bot",
            command=self._show_bot_surface,
        ).pack(side="left")
        ttk.Label(
            self.tool_header,
            text="Advanced tools — changes here affect the underlying automation configuration.",
            style="Surface.TLabel",
        ).pack(side="left", padx=(12, 0))

    def _clear_advanced_start_hotkey(self):
        """Remove only the legacy Scenario-editor start hotkey, if registered."""

        old_handle = getattr(self, "_start_hotkey_handle", None)
        self._start_hotkey_handle = None
        self._registered_start_hotkey = None
        self._start_hotkey_registration_token = None
        if old_handle is not None:
            try:
                keyboard.remove_hotkey(old_handle)
            except Exception:
                pass

    def _register_start_hotkey(self):
        """Register the legacy Scenario start hotkey only while Advanced is open."""

        if not getattr(self, "_advanced_tools_visible", False):
            self._clear_advanced_start_hotkey()
            return False
        return super()._register_start_hotkey()

    def _check_auto_start(self, now=None):
        """Keep the legacy Scenario auto-start dormant outside Advanced mode."""

        if not getattr(self, "_advanced_tools_visible", False):
            return False
        return super()._check_auto_start(now)

    def _show_bot_surface(self):
        try:
            self._advanced_tools_visible = False
            self._register_start_hotkey()
            self.notebook.pack_forget()
            self.tool_header.pack_forget()
            if not self.bot_surface.winfo_manager():
                self.bot_surface.pack(fill="both", expand=True)
        except tk.TclError:
            return

    def _show_tool_tab(self, tab, label):
        try:
            self.bot_surface.pack_forget()
            if not self.tool_header.winfo_manager():
                self.tool_header.pack(fill="x", padx=12, pady=(12, 0))
            if not self.notebook.winfo_manager():
                self.notebook.pack(fill="both", expand=True)
            self.notebook.tab(tab, text=label)
            self.notebook.select(tab)
            self._advanced_tools_visible = tab is self.macro_tab
            self._register_start_hotkey()
        except tk.TclError:
            return

    def _show_advanced_tools(self):
        self._show_tool_tab(self.macro_tab, "Advanced")

    def _show_alert_setup(self):
        self._show_tool_tab(self.alert_tab, "Alert Setup")

    def _start_bot_feature(self, feature, config, *, gather_team=None):
        """Start a configured feature without changing the Advanced editor state."""

        if getattr(self, "_step_test_running", False):
            messagebox.showwarning(
                "Step test running",
                "Wait for the step test to finish before starting the bot.",
                parent=self.root,
            )
            return False
        if self.engine and self.engine.is_running:
            return False

        engine = None
        try:
            scenario = configured_scenario(
                feature,
                config,
                gather_team=gather_team,
            )
            conflict = self._macro_alert_hotkey_conflict(
                scenario.start_hotkey,
                scenario.kill_switch,
            )
            if conflict:
                raise ValueError(conflict)
            validate_scenario(scenario, require_files=True)
            engine = MacroEngine(scenario, log=self._queue_log)
            engine.start()
        except Exception as exc:
            if engine is not None:
                try:
                    engine.stop()
                except Exception:
                    pass
            self.engine = None
            self._set_engine_stopped_ui()
            self.ui_feedback.play("error")
            messagebox.showerror(
                "Failed to start bot feature",
                str(exc),
                parent=self.root,
            )
            return False

        self.engine = engine
        self._bot_engine_feature = feature
        self._bot_gather_team = gather_team if feature == FEATURE_GATHER else None
        self._engine_ui_active = True
        self._engine_ready_announced = False
        self._set_macro_editor_locked(True)
        self.run_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        if engine.is_ready:
            self._set_engine_running_ui()
        else:
            self.status_label.config(
                text="◌ Preparing OCR…",
                style="Preparing.Status.TLabel",
            )
        return True

    def _start_bot_gather_team(self, team, config):
        """Start one fail-closed Gather attempt for the exact selected team."""

        return self._start_bot_feature(
            FEATURE_GATHER,
            config,
            gather_team=int(team),
        )

    def _log(self, msg):
        super()._log(msg)
        bot_frame = getattr(self, "bot_frame", None)
        if bot_frame is not None:
            timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            bot_frame.append_runtime_log(f"{timestamp} {msg}")


def main():
    """Launch BotApp while preserving the existing single-instance behavior."""

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
                "Automation Bot already running",
                "Another copy of the automation application is already running.",
                parent=notice,
            )
        else:
            messagebox.showerror(
                "Automation Bot could not start",
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
            BotApp(root)
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
                "Automation Bot could not start",
                f"{type(exc).__name__}: {exc}\n\n"
                f"Details were written to:\n{STARTUP_ERROR_LOG}",
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
