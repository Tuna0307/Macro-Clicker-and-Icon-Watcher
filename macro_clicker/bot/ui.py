"""Dedicated user-facing bot frame layered over the existing backend."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .config import load_bot_config
from .controller import BotController
from .ui_pages import BotPagesMixin
from .ui_runtime import BotRuntimeMixin


class BotFrame(BotPagesMixin, BotRuntimeMixin, ttk.Frame):
    """Normal-user bot UI; Scenario/Step details remain in Advanced."""

    TAB_NAMES = (
        "Dashboard",
        "Rally",
        "Gather",
        "Positions",
        "Alerts",
        "Schedule",
        "Logs",
        "Settings",
    )

    def __init__(self, master, *, host, alert_frame, show_advanced):
        super().__init__(master)
        self.host = host
        self.alert_frame = alert_frame
        self.show_advanced = show_advanced
        self.config = load_bot_config()
        self.controller = BotController(
            lambda: self.config,
            self._run_feature,
            self._stop_feature,
        )
        self._last_start_token = None
        self._last_stop_token = None
        self._build_ui()
        self._load_vars()
        self.after(500, self._poll_status)
        self.after(1000, self._poll_schedule)

    def _build_ui(self):
        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill="both", expand=True)
        self.pages = {}
        for name in self.TAB_NAMES:
            page = ttk.Frame(self.tabs, padding=18)
            page.columnconfigure(0, weight=1)
            self.pages[name] = page
            self.tabs.add(page, text=name)
        self._build_dashboard()
        self._build_rally()
        self._build_gather()
        self._build_positions()
        self._build_alerts()
        self._build_schedule()
        self._build_logs()
        self._build_settings()

    def _card(self, page_name, title, row=0):
        card = ttk.LabelFrame(self.pages[page_name], text=title, padding=16)
        card.grid(row=row, column=0, sticky="ew", pady=(0, 12))
        card.columnconfigure(1, weight=1)
        return card

    @staticmethod
    def _spin(card, row, label, variable, low, high, increment=1):
        ttk.Label(card, text=label).grid(row=row, column=0, sticky="w", pady=5)
        ttk.Spinbox(
            card,
            textvariable=variable,
            from_=low,
            to=high,
            increment=increment,
            width=12,
        ).grid(row=row, column=1, sticky="w", padx=(12, 0), pady=5)

    @staticmethod
    def _button_row(card, row, *buttons):
        holder = ttk.Frame(card)
        holder.grid(row=row, column=0, columnspan=4, sticky="w", pady=(14, 0))
        for index, (text, command) in enumerate(buttons):
            ttk.Button(holder, text=text, command=command).pack(
                side="left",
                padx=(0 if index == 0 else 8, 0),
            )
