import unittest
from unittest.mock import patch


class BotAppTests(unittest.TestCase):
    def test_bot_app_hides_advanced_tools_until_requested(self):
        import tkinter as tk

        from macro_clicker import alert_watcher
        from macro_clicker.bot_app import BotApp

        alert_watcher.HAVE_KEYBOARD = False
        alert_watcher.HAVE_PYSTRAY = False

        root = tk.Tk()
        root.withdraw()
        try:
            with (
                patch("macro_clicker.app.maintain_logs"),
                patch("macro_clicker.app.App._write_log_file"),
                patch("macro_clicker.app.App._register_start_hotkey"),
                patch("macro_clicker.alert_watcher.AlertWatcherFrame._setup_hotkeys"),
            ):
                ui = BotApp(root)

            tabs = [ui.notebook.tab(tab_id, "text") for tab_id in ui.notebook.tabs()]
            bot_tabs = [
                ui.bot_frame.tabs.tab(tab_id, "text")
                for tab_id in ui.bot_frame.tabs.tabs()
            ]
            selected_at_start = ui.notebook.select()
            advanced_state = ui.notebook.tab(ui.macro_tab, "state")
            alert_setup_state = ui.notebook.tab(ui.alert_tab, "state")

            ui._show_advanced_tools()
            advanced_state_after = ui.notebook.tab(ui.macro_tab, "state")
            selected_advanced = ui.notebook.select()

            ui._show_alert_setup()
            alert_setup_state_after = ui.notebook.tab(ui.alert_tab, "state")
            selected_alert_setup = ui.notebook.select()
        finally:
            root.destroy()

        self.assertEqual(tabs, ["Bot", "Advanced", "Alert Setup"])
        self.assertEqual(selected_at_start, str(ui.bot_tab))
        self.assertEqual(advanced_state, "hidden")
        self.assertEqual(alert_setup_state, "hidden")
        self.assertEqual(advanced_state_after, "normal")
        self.assertEqual(selected_advanced, str(ui.macro_tab))
        self.assertEqual(alert_setup_state_after, "normal")
        self.assertEqual(selected_alert_setup, str(ui.alert_tab))
        self.assertEqual(
            bot_tabs,
            [
                "Dashboard",
                "Rally",
                "Gather",
                "Positions",
                "Alerts",
                "Schedule",
                "Logs",
                "Settings",
            ],
        )


if __name__ == "__main__":
    unittest.main()
