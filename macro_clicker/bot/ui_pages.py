"""Page builders for the dedicated bot interface."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .controller import (
    FEATURE_DEVELOPMENT,
    FEATURE_GATHER,
    FEATURE_RALLY,
    FEATURE_SCIENCE,
)


class BotPagesMixin:
    def _build_dashboard(self):
        card = self._card("Dashboard", "Bot Status")
        self.dashboard_status_var = tk.StringVar(value="● Stopped")
        self.active_task_var = tk.StringVar(value="None")
        self.next_task_var = tk.StringVar(value="None")
        self.last_action_var = tk.StringVar(value="Ready")
        self.alert_status_var = tk.StringVar(value="Disabled")
        self.rally_status_var = tk.StringVar(value="Disabled")
        self.gather_status_var = tk.StringVar(value="Disabled")
        self.positions_status_var = tk.StringVar(value="Disabled")
        self.schedule_status_var = tk.StringVar(value="Disabled")

        ttk.Label(
            card,
            textvariable=self.dashboard_status_var,
            style="Section.TLabel",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))
        rows = (
            ("Current task", self.active_task_var),
            ("Next task", self.next_task_var),
            ("Last status", self.last_action_var),
            ("Alerts", self.alert_status_var),
        )
        for row, (label, var) in enumerate(rows, start=1):
            ttk.Label(card, text=label, style="Surface.TLabel").grid(
                row=row, column=0, sticky="nw", pady=4
            )
            ttk.Label(
                card,
                textvariable=var,
                style="Surface.TLabel",
                wraplength=620,
                justify="left",
            ).grid(row=row, column=1, sticky="w", padx=(16, 0), pady=4)
        self._button_row(
            card,
            5,
            ("Start Bot", self._start_bot, "primary"),
            ("Stop", self._stop_bot, "danger"),
        )

        live = self._card("Dashboard", "Feature Status", 1)
        live_rows = (
            ("Rally", self.rally_status_var),
            ("Gather", self.gather_status_var),
            ("Positions", self.positions_status_var),
            ("Schedule", self.schedule_status_var),
        )
        for row, (label, var) in enumerate(live_rows):
            ttk.Label(live, text=label, style="Surface.TLabel").grid(
                row=row, column=0, sticky="nw", pady=5
            )
            ttk.Label(
                live,
                textvariable=var,
                style="Surface.TLabel",
                wraplength=620,
                justify="left",
            ).grid(row=row, column=1, sticky="w", padx=(16, 0), pady=5)

        quick = self._card("Dashboard", "Quick Actions", 2)
        ttk.Label(
            quick,
            text=(
                "Run one task immediately without changing which tasks are enabled "
                "for a full Bot session."
            ),
            style="Surface.TLabel",
        ).grid(row=0, column=0, columnspan=2, sticky="w")
        self._button_row(
            quick,
            1,
            ("Run Rally", lambda: self._run_feature_direct(FEATURE_RALLY)),
            ("Start Gather", lambda: self._run_feature_direct(FEATURE_GATHER)),
            ("Development", lambda: self._run_feature_direct(FEATURE_DEVELOPMENT)),
            ("Science", lambda: self._run_feature_direct(FEATURE_SCIENCE)),
        )

    def _build_rally(self):
        card = self._card("Rally", "Gold Mob Rally")
        self.rally_enabled_var = tk.BooleanVar()
        self.rally_min_var = tk.IntVar()
        self.rally_max_var = tk.IntVar()
        self.team1_max_var = tk.IntVar()
        self.team3_max_var = tk.IntVar()
        self.join_delay_var = tk.DoubleVar()
        ttk.Checkbutton(
            card,
            text="Enable Gold Mob Rally",
            variable=self.rally_enabled_var,
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))
        self._spin(card, 1, "Minimum mob level", self.rally_min_var, 0, 999)
        self._spin(card, 2, "Maximum mob level", self.rally_max_var, 0, 999)
        self._spin(card, 3, "Team 1 maximum level", self.team1_max_var, 0, 999)
        self._spin(card, 4, "Team 3 maximum level", self.team3_max_var, 0, 999)
        self._spin(card, 5, "Join delay (seconds)", self.join_delay_var, 0, 30, 0.1)
        ttk.Label(
            card,
            text="Team 3 remains preferred when eligible; Team 1 is the fallback.",
            style="Muted.TLabel",
        ).grid(row=6, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self._button_row(
            card,
            7,
            ("Save", self._save_from_ui),
            ("Run Rally", lambda: self._run_feature_direct(FEATURE_RALLY), "primary"),
        )

    def _build_gather(self):
        card = self._card("Gather", "Continuous Resource Gathering")
        self.gather_enabled_var = tk.BooleanVar()
        self.resource_var = tk.StringVar(value="Gold")
        self.gather_start_level_var = tk.IntVar(value=12)
        self.gather_team_vars = {team: tk.BooleanVar(value=True) for team in (1, 2, 3)}

        ttk.Checkbutton(
            card,
            text="Enable Auto Gather",
            variable=self.gather_enabled_var,
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))
        ttk.Label(card, text="Resource", style="Surface.TLabel").grid(
            row=1, column=0, sticky="w", pady=5
        )
        ttk.Combobox(
            card,
            textvariable=self.resource_var,
            values=("Gold",),
            state="readonly",
            width=12,
        ).grid(row=1, column=1, sticky="w", padx=(16, 0), pady=5)
        self._spin(
            card,
            2,
            "Starting / maximum search level",
            self.gather_start_level_var,
            1,
            99,
        )

        ttk.Label(card, text="Teams allowed to gather", style="Surface.TLabel").grid(
            row=3, column=0, sticky="nw", pady=6
        )
        teams = ttk.Frame(card, style="Surface.TFrame")
        teams.grid(row=3, column=1, sticky="w", padx=(16, 0), pady=6)
        for team, var in self.gather_team_vars.items():
            ttk.Checkbutton(teams, text=f"Team {team}", variable=var).pack(
                side="left", padx=(0, 12)
            )

        ttk.Label(
            card,
            text=(
                "Auto Gather uses whichever selected team is visually idle. "
                "If every selected team is travelling, gathering, returning, or otherwise busy, "
                "the Bot waits. It never recalls or replaces a busy team."
            ),
            style="Muted.TLabel",
            wraplength=760,
            justify="left",
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Label(
            card,
            text=(
                "Search behavior: start at the configured level, lower the level when unavailable, "
                "and keep searching until a resource is found."
            ),
            style="Muted.TLabel",
            wraplength=760,
            justify="left",
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(4, 0))
        self._button_row(
            card,
            6,
            ("Save", self._save_from_ui),
            ("Start Auto Gather", lambda: self._run_feature_direct(FEATURE_GATHER), "primary"),
            ("Stop Auto Gather", self._stop_continuous_gather, "danger"),
        )

        status = self._card("Gather", "Team Status", 1)
        self.gather_team_status_vars = {
            team: tk.StringVar(value="Not monitored") for team in (1, 2, 3)
        }
        for row, team in enumerate((1, 2, 3)):
            ttk.Label(status, text=f"Team {team}", style="Surface.TLabel").grid(
                row=row, column=0, sticky="w", pady=5
            )
            ttk.Label(
                status,
                textvariable=self.gather_team_status_vars[team],
                style="Surface.TLabel",
            ).grid(row=row, column=1, sticky="w", padx=(16, 0), pady=5)
        ttk.Label(
            status,
            text=(
                "Countdowns are scheduling hints only. The game screen must visually "
                "confirm Idle before a new dispatch."
            ),
            style="Muted.TLabel",
            wraplength=760,
            justify="left",
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))

    def _build_positions(self):
        card = self._card("Positions", "Position Applications")
        self.development_enabled_var = tk.BooleanVar()
        self.science_enabled_var = tk.BooleanVar()
        self.positions_retry_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            card,
            text="Enable Development Position",
            variable=self.development_enabled_var,
        ).grid(row=0, column=0, sticky="w", pady=5)
        ttk.Button(
            card,
            text="Run Development",
            style="Primary.TButton",
            command=lambda: self._run_feature_direct(FEATURE_DEVELOPMENT),
        ).grid(row=0, column=1, sticky="w", padx=(16, 0))
        ttk.Checkbutton(
            card,
            text="Enable Science Position",
            variable=self.science_enabled_var,
        ).grid(row=1, column=0, sticky="w", pady=5)
        ttk.Button(
            card,
            text="Run Science",
            style="Primary.TButton",
            command=lambda: self._run_feature_direct(FEATURE_SCIENCE),
        ).grid(row=1, column=1, sticky="w", padx=(16, 0))
        ttk.Checkbutton(
            card,
            text="Retry automatically when a position is unavailable",
            variable=self.positions_retry_var,
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(10, 0))
        ttk.Label(
            card,
            text=(
                "Turn this off if a full Bot cycle should close the unavailable "
                "position and continue to its next enabled task."
            ),
            style="Muted.TLabel",
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(5, 0))
        self._button_row(card, 4, ("Save", self._save_from_ui))

    def _build_alerts(self):
        card = self._card("Alerts", "Icon Alerts")
        self.alerts_enabled_var = tk.BooleanVar()
        self.digs_enabled_var = tk.BooleanVar()
        self.secret_task_enabled_var = tk.BooleanVar()
        self.alert_sound_var = tk.BooleanVar()
        self.alert_volume_var = tk.IntVar(value=70)
        controls = (
            ("Enable passive alerts", self.alerts_enabled_var),
            ("Digs", self.digs_enabled_var),
            ("Secret Task", self.secret_task_enabled_var),
            ("Sound", self.alert_sound_var),
        )
        for row, (text, var) in enumerate(controls):
            ttk.Checkbutton(card, text=text, variable=var).grid(
                row=row, column=0, columnspan=2, sticky="w", pady=4
            )
        self._spin(card, 4, "Volume (%)", self.alert_volume_var, 0, 100)
        self._button_row(
            card,
            5,
            ("Save", self._save_from_ui),
            ("Start Alerts", self._start_alerts, "primary"),
            ("Stop Alerts", self._stop_alerts, "danger"),
            ("Advanced Alert Setup", self._show_alert_setup),
        )

    def _build_schedule(self):
        card = self._card("Schedule", "Bot Schedule")
        self.schedule_enabled_var = tk.BooleanVar()
        self.schedule_start_var = tk.StringVar(value="06:00")
        self.schedule_stop_var = tk.StringVar(value="23:00")
        self.day_vars = {
            day: tk.BooleanVar(value=True)
            for day in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
        }
        ttk.Checkbutton(
            card,
            text="Enable schedule",
            variable=self.schedule_enabled_var,
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        for row, (label, var) in enumerate(
            (("Start time", self.schedule_start_var), ("Stop time", self.schedule_stop_var)),
            start=1,
        ):
            ttk.Label(card, text=label, style="Surface.TLabel").grid(
                row=row, column=0, sticky="w", pady=5
            )
            ttk.Entry(card, textvariable=var, width=10).grid(
                row=row, column=1, sticky="w", padx=(16, 0), pady=5
            )
        days = ttk.Frame(card, style="Surface.TFrame")
        days.grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))
        for day, var in self.day_vars.items():
            ttk.Checkbutton(days, text=day, variable=var).pack(
                side="left", padx=(0, 7)
            )
        self._button_row(card, 4, ("Save", self._save_from_ui))

    def _build_logs(self):
        page = self.pages["Logs"]
        page.rowconfigure(0, weight=1)
        self.bot_log = tk.Text(page, state="disabled", height=20, wrap="none")
        self.bot_log.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(page, orient="vertical", command=self.bot_log.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.bot_log.configure(yscrollcommand=scroll.set)

    def _build_settings(self):
        card = self._card("Settings", "General")
        self.target_window_var = tk.StringVar()
        ttk.Label(card, text="Target window title", style="Surface.TLabel").grid(
            row=0, column=0, sticky="w", pady=5
        )
        ttk.Entry(card, textvariable=self.target_window_var, width=44).grid(
            row=0, column=1, sticky="w", padx=(16, 0), pady=5
        )
        ttk.Label(
            card,
            text="Advanced keeps the Scenario / Step / template tools for debugging.",
            style="Muted.TLabel",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self._button_row(
            card,
            2,
            ("Save", self._save_from_ui),
            ("Open Advanced", self.show_advanced),
        )
