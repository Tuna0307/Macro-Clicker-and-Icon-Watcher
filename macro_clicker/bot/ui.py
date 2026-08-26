"""Dedicated user-facing bot frame layered over the existing backend."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ..ui_components import action_button
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

    def __init__(
        self,
        master,
        *,
        host,
        alert_frame,
        show_advanced,
        show_alert_setup,
    ):
        super().__init__(master)
        self.host = host
        self.alert_frame = alert_frame
        self.show_advanced = show_advanced
        self.show_alert_setup = show_alert_setup
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
        # A small product-level header makes the normal-user surface visually
        # distinct from the legacy editor while keeping all controls in ttk/CTk
        # so the project's existing DPI behavior remains unchanged.
        header = ttk.Frame(self, style="Card.TFrame", padding=(20, 14))
        header.pack(fill="x", padx=12, pady=(12, 0))
        ttk.Label(header, text="Automation Bot", style="Title.TLabel").pack(
            anchor="w"
        )
        ttk.Label(
            header,
            text="Configure everyday tasks here. Advanced scenario and template tools stay hidden unless you open them explicitly.",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(3, 0))

        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill="both", expand=True)
        self.pages = {}
        for name in self.TAB_NAMES:
            page = ttk.Frame(self.tabs, padding=22)
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
        card = ttk.LabelFrame(self.pages[page_name], text=title, padding=(20, 16))
        card.grid(row=row, column=0, sticky="ew", pady=(0, 14))
        card.columnconfigure(1, weight=1)
        return card

    @staticmethod
    def _spin(card, row, label, variable, low, high, increment=1):
        ttk.Label(card, text=label).grid(row=row, column=0, sticky="w", pady=6)
        ttk.Spinbox(
            card,
            textvariable=variable,
            from_=low,
            to=high,
            increment=increment,
            width=12,
        ).grid(row=row, column=1, sticky="w", padx=(16, 0), pady=6)

    @staticmethod
    def _button_row(card, row, *buttons):
        """Build a button row with optional primary/danger emphasis.

        Entries are ``(text, command)`` for ordinary actions or
        ``(text, command, kind)`` where kind is ``primary`` or ``danger``.
        """

        holder = ttk.Frame(card, style="Surface.TFrame")
        holder.grid(row=row, column=0, columnspan=4, sticky="w", pady=(16, 0))
        for index, button_spec in enumerate(buttons):
            text, command = button_spec[:2]
            kind = button_spec[2] if len(button_spec) > 2 else "default"
            if kind in {"primary", "danger"}:
                button = action_button(
                    holder,
                    text=text,
                    command=command,
                    kind=kind,
                    width=116,
                )
            else:
                button = ttk.Button(holder, text=text, command=command)
            button.pack(
                side="left",
                padx=(0 if index == 0 else 8, 0),
            )
