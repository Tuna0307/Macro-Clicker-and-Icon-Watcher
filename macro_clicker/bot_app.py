"""Dedicated bot-style application shell layered over the existing App backend."""

from __future__ import annotations

import os
import tkinter as tk
import traceback
from datetime import datetime
from tkinter import messagebox, ttk
from typing import Optional

from .alert_watcher import SingleInstanceLock
from .app import App
from .bot.adapters import configured_scenario
from .bot.ui import BotFrame
from .engine import MacroEngine
from .models import validate_scenario
from .runtime_paths import STARTUP_ERROR_LOG
from .ui_components import create_root


class BotApp(App):
    """Normal-user bot shell that keeps the original editors as advanced tools."""

    def __init__(self, root):
        super().__init__(root)
        root.title("PC Automation Bot")

    def _build_ui(self):
        # Build the mature editor and alert UI exactly as before, then place a
        # new user-facing Bot tab in front of them. This avoids rewriting the
        # working App layout while the product transitions to bot mode.
        super()._build_ui()
        self.notebook.tab(self.macro_tab, text="Advanced")
        self.notebook.tab(self.alert_tab, text="Alert Setup")

        self.bot_tab = ttk.Frame(self.notebook)
        self.notebook.insert(0, self.bot_tab, text="Bot")
        self.bot_tab.columnconfigure(0, weight=1)
        self.bot_tab.rowconfigure(0, weight=1)
        self.bot_frame = BotFrame(
            self.bot_tab,
            host=self,
            alert_frame=self.alert_tab,
            show_advanced=lambda: self.notebook.select(self.macro_tab),
        )
        self.bot_frame.grid(row=0, column=0, sticky="nsew")

    def _start_bot_feature(self, feature, config):
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
            scenario = configured_scenario(feature, config)
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
                    handle.write(
                        f"\n[{datetime.now().isoformat(timespec='seconds')}]\n"
                    )
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
