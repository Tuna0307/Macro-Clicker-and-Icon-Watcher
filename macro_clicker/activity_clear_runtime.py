"""Add a non-destructive Clear log control to the Macro Builder Activity pane.

The button only clears the on-screen Activity view and any messages already
queued for that view. Persistent ``pc_macro_builder.log`` history is preserved,
and the running macro/scenario is not stopped or reset.

This is installed by the supported desktop entry points after ``app.py`` has
finished importing, which keeps the change isolated from the large App module.
"""

from __future__ import annotations

import queue
from tkinter import ttk

from .ui_components import Tooltip

_INSTALLED = False
_ORIGINAL_BUILD_UI = None


def clear_activity_view(app) -> None:
    """Clear only the visible Activity stream and stale queued UI messages."""

    log_text = getattr(app, "log_text", None)
    if log_text is not None:
        log_text.delete("1.0", "end")

    app._log_line_count = 0

    log_queue = getattr(app, "log_queue", None)
    if log_queue is not None:
        while True:
            try:
                log_queue.get_nowait()
            except queue.Empty:
                break

    # Preserve the disk log for debugging/history, but leave the Activity pane
    # visually blank so the user's next observation starts from a clean view.
    write_log = getattr(app, "_write_log_file", None)
    if callable(write_log):
        write_log("---- activity view cleared ----")


def _add_clear_button(app) -> None:
    toggle = getattr(app, "activity_toggle", None)
    if toggle is None:
        return

    header = toggle.master
    # Activity title occupies column 0. Keep the disclosure at the far right and
    # place Clear log immediately before it.
    toggle.grid_configure(column=2)
    app.activity_clear_button = ttk.Button(
        header,
        text="Clear log",
        style="Toolbar.TButton",
        command=lambda: clear_activity_view(app),
    )
    app.activity_clear_button.grid(row=0, column=1, padx=(0, 8))
    Tooltip(
        app.activity_clear_button,
        "Clear the visible Activity log; saved log history is kept",
    )


def install_activity_clear_runtime() -> None:
    """Install the Activity clear button once for subsequently-created Apps."""

    global _INSTALLED
    global _ORIGINAL_BUILD_UI
    if _INSTALLED:
        return

    from . import app as app_module

    _ORIGINAL_BUILD_UI = app_module.App._build_ui

    def build_ui(self):
        _ORIGINAL_BUILD_UI(self)
        _add_clear_button(self)

    app_module.App._build_ui = build_ui
    _INSTALLED = True
