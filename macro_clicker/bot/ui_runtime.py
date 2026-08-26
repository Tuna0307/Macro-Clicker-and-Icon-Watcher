"""Configuration, runtime, alert, scheduling, and status behavior for BotFrame."""

from __future__ import annotations

import copy
import tkinter as tk
from datetime import datetime
from tkinter import messagebox

from .config import BotConfigError, save_bot_config, validate_bot_config
from .controller import FEATURE_LABELS
from .status import build_dashboard_snapshot


class BotRuntimeMixin:
    def _load_vars(self):
        c = self.config
        self.rally_enabled_var.set(c.rally.enabled)
        self.rally_min_var.set(c.rally.min_level)
        self.rally_max_var.set(c.rally.max_level)
        self.team1_max_var.set(c.rally.team1_max_level)
        self.team3_max_var.set(c.rally.team3_max_level)
        self.join_delay_var.set(c.rally.join_delay)
        self.gather_enabled_var.set(c.gather.enabled)
        self.resource_var.set(c.gather.resource)
        self.gather_start_level_var.set(c.gather.start_level)
        self.gather_marches_var.set(c.gather.march_count)
        self.replacement_order_var.set(" → ".join(map(str, c.gather.replacement_order)))
        self.development_enabled_var.set(c.positions.development_enabled)
        self.science_enabled_var.set(c.positions.science_enabled)
        self.positions_retry_var.set(c.positions.retry_automatically)
        self.alerts_enabled_var.set(c.alerts.enabled)
        self.digs_enabled_var.set(c.alerts.digs_enabled)
        self.secret_task_enabled_var.set(c.alerts.secret_task_enabled)
        self.alert_sound_var.set(c.alerts.sound_enabled)
        self.alert_volume_var.set(c.alerts.volume_percent)
        self.schedule_enabled_var.set(c.schedule.enabled)
        self.schedule_start_var.set(c.schedule.start_time)
        self.schedule_stop_var.set(c.schedule.stop_time)
        for day, var in self.day_vars.items():
            var.set(day in c.schedule.days)
        self.target_window_var.set(c.target_window_title)

    @staticmethod
    def _parse_order(text):
        parts = str(text).replace("→", ",").replace("->", ",").split(",")
        order = [int(part.strip()) for part in parts if part.strip()]
        if len(order) != 3 or set(order) != {1, 2, 3}:
            raise BotConfigError(
                "Replacement order must contain 1, 2, and 3 exactly once."
            )
        return order

    def _collect_config(self):
        # Build edits on a copy. A malformed value in one field must never
        # partially mutate the last known-good in-memory config before validation
        # finishes, because scheduling and direct-run actions read self.config.
        c = copy.deepcopy(self.config)
        try:
            c.target_window_title = (
                self.target_window_var.get().strip() or "Last War-Survival Game"
            )
            c.rally.enabled = bool(self.rally_enabled_var.get())
            c.rally.min_level = int(self.rally_min_var.get())
            c.rally.max_level = int(self.rally_max_var.get())
            c.rally.team1_max_level = int(self.team1_max_var.get())
            c.rally.team3_max_level = int(self.team3_max_var.get())
            c.rally.join_delay = float(self.join_delay_var.get())
            c.gather.enabled = bool(self.gather_enabled_var.get())
            c.gather.resource = self.resource_var.get().strip() or "Gold"
            c.gather.start_level = int(self.gather_start_level_var.get())
            c.gather.march_count = int(self.gather_marches_var.get())
            c.gather.replacement_order = self._parse_order(
                self.replacement_order_var.get()
            )
            c.gather.search_until_found = True
            c.positions.development_enabled = bool(self.development_enabled_var.get())
            c.positions.science_enabled = bool(self.science_enabled_var.get())
            c.positions.retry_automatically = bool(self.positions_retry_var.get())
            c.alerts.enabled = bool(self.alerts_enabled_var.get())
            c.alerts.digs_enabled = bool(self.digs_enabled_var.get())
            c.alerts.secret_task_enabled = bool(self.secret_task_enabled_var.get())
            c.alerts.sound_enabled = bool(self.alert_sound_var.get())
            c.alerts.volume_percent = int(self.alert_volume_var.get())
            c.schedule.enabled = bool(self.schedule_enabled_var.get())
            c.schedule.start_time = self.schedule_start_var.get().strip()
            c.schedule.stop_time = self.schedule_stop_var.get().strip()
            c.schedule.days = [
                day for day, var in self.day_vars.items() if var.get()
            ]
            validate_bot_config(c)
        except (tk.TclError, ValueError, TypeError) as exc:
            raise BotConfigError(str(exc)) from exc
        return c

    def _save_from_ui(self, quiet=False):
        try:
            pending = self._collect_config()
            save_bot_config(pending)
            self.config = pending
            self._apply_alert_preferences()
        except (OSError, ValueError) as exc:
            if not quiet:
                messagebox.showerror(
                    "Could not save bot settings",
                    str(exc),
                    parent=self,
                )
            return False
        self._append_log("Bot settings saved.")
        return True

    # ---- active automation -------------------------------------------------
    def _run_feature(self, feature):
        return bool(self.host._start_bot_feature(feature, self.config))

    def _stop_feature(self):
        self.host._stop_engine()
        return True

    def _run_feature_direct(self, feature):
        if not self._save_from_ui():
            return False
        if self.controller.run_feature(feature):
            self._append_log(f"Started {FEATURE_LABELS.get(feature, feature)}.")
            return True
        messagebox.showwarning(
            "Automation busy",
            self.controller.status.last_message,
            parent=self,
        )
        return False

    def _start_bot(self, save_first=True):
        # Manual starts use what is currently on screen and save it first.
        # Scheduled starts deliberately use the last explicit Save so partially
        # typed edits cannot become live just because a schedule fires.
        if save_first and not self._save_from_ui():
            return

        requested_clicking_features = self.controller.enabled_features()
        alerts = self.config.alerts.enabled and self._start_alerts(save_first=False)
        active = self.controller.start() if requested_clicking_features else False

        if active:
            self._append_log(self.controller.status.last_message)
            return

        if requested_clicking_features:
            # Do not hide a requested automation failure merely because passive
            # alerts happened to start successfully in parallel.
            message = self.controller.status.last_message
            self._append_log(message)
            if alerts:
                self._append_log("Passive alerts are still running.")
            messagebox.showwarning(
                "Bot automation did not start",
                message,
                parent=self,
            )
            return

        if alerts:
            self._append_log("Bot started with passive alerts only.")
            return

        messagebox.showinfo(
            "Nothing enabled",
            "Enable at least one bot feature first.",
            parent=self,
        )

    def _stop_bot(self):
        self.controller.stop()
        self._stop_alerts()
        self._append_log("Bot stop requested.")

    # ---- alerts ------------------------------------------------------------
    def _show_alert_setup(self):
        self.show_alert_setup()

    def _apply_alert_preferences(self):
        frame = self.alert_frame
        flags = {
            "digs text cyan": self.config.alerts.digs_enabled,
            "resources dig icon": self.config.alerts.digs_enabled,
            "secret task": self.config.alerts.secret_task_enabled,
        }
        changed = False
        for item in frame.tm.snapshot():
            desired = flags.get(str(item.get("name", "")).casefold())
            current = bool(item.get("enabled", True))
            if desired is not None and current != bool(desired):
                frame.tm.set_enabled(item["id"], bool(desired), save=False)
                changed = True
        if changed:
            frame.tm._save()
            frame._refresh_list()
            watcher = frame.watcher
            if watcher is not None and watcher.is_alive():
                watcher.templates_changed()
        frame.target_window_var.set(self.config.target_window_title)
        volume = (
            self.config.alerts.volume_percent if self.config.alerts.sound_enabled else 0
        )
        frame.volume_var.set(volume)
        frame._save_settings()

    def _start_alerts(self, save_first=True):
        if save_first and not self._save_from_ui(quiet=True):
            return False
        watcher = self.alert_frame.watcher
        if watcher is None or not watcher.is_alive():
            self.alert_frame._start_watching()
        watcher = self.alert_frame.watcher
        return bool(watcher is not None and watcher.is_alive())

    def _stop_alerts(self):
        try:
            self.alert_frame._stop_watching()
        except (AttributeError, tk.TclError):
            pass
        return True

    # ---- status / schedule -------------------------------------------------
    def _poll_status(self):
        try:
            engine = getattr(self.host, "engine", None)
            running = bool(engine is not None and engine.is_running)
            if (
                not running
                and self.controller.status.active_feature is not None
                and engine is not None
            ):
                self.controller.engine_stopped()
                engine = getattr(self.host, "engine", None)

            watcher = self.alert_frame.watcher
            alerts = bool(watcher is not None and watcher.is_alive())
            snapshot = build_dashboard_snapshot(
                config=self.config,
                controller=self.controller,
                engine=engine,
                alerts_running=alerts,
            )
            self.dashboard_status_var.set(snapshot.overall)
            self.active_task_var.set(snapshot.current_task)
            self.next_task_var.set(snapshot.next_task)
            self.last_action_var.set(snapshot.last_status)
            self.alert_status_var.set(snapshot.alerts)
            self.rally_status_var.set(snapshot.rally)
            self.gather_status_var.set(snapshot.gather)
            self.positions_status_var.set(snapshot.positions)
            self.schedule_status_var.set(snapshot.schedule)
        except tk.TclError:
            return
        self.after(500, self._poll_status)

    def _poll_schedule(self):
        try:
            # Scheduling uses only the last validated/saved config. Typing into
            # a field should not silently change a live schedule before Save.
            schedule = self.config.schedule
            if schedule.enabled:
                now = datetime.now()
                token = now.strftime("%Y-%m-%d %H:%M")
                clock = now.strftime("%H:%M")
                if now.strftime("%a") in schedule.days:
                    if clock == schedule.start_time and token != self._last_start_token:
                        self._last_start_token = token
                        self._append_log("Scheduled start triggered.")
                        self._start_bot(save_first=False)
                    if clock == schedule.stop_time and token != self._last_stop_token:
                        self._last_stop_token = token
                        self._append_log("Scheduled stop triggered.")
                        self._stop_bot()
        except tk.TclError:
            pass
        self.after(1000, self._poll_schedule)

    def append_runtime_log(self, line):
        try:
            self.bot_log.config(state="normal")
            self.bot_log.insert(tk.END, str(line) + "\n")
            self.bot_log.see(tk.END)
            self.bot_log.config(state="disabled")
        except tk.TclError:
            pass

    def _append_log(self, message):
        try:
            self.host._queue_log(f"[bot] {message}")
        except (AttributeError, tk.TclError):
            self.append_runtime_log(f"{datetime.now():%H:%M:%S} [bot] {message}")
