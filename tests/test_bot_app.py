import unittest
from unittest.mock import patch


class BotAppTests(unittest.TestCase):
    def test_bot_app_places_user_interface_before_advanced_tools(self):
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
            ):
                ui = BotApp(root)
            tabs = [ui.notebook.tab(tab_id, "text") for tab_id in ui.notebook.tabs()]
            bot_tabs = [
                ui.bot_frame.tabs.tab(tab_id, "text")
                for tab_id in ui.bot_frame.tabs.tabs()
            ]
        finally:
            root.destroy()

        self.assertEqual(tabs, ["Bot", "Advanced", "Alert Setup"])
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
