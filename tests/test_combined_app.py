import unittest
from unittest.mock import patch


class CombinedAppTests(unittest.TestCase):
    def test_main_window_contains_macro_and_icon_alert_tabs(self):
        import tkinter as tk
        import alert_watcher
        import app

        alert_watcher.HAVE_KEYBOARD = False
        alert_watcher.HAVE_PYSTRAY = False

        root = tk.Tk()
        root.withdraw()
        try:
            with patch.object(app, "maintain_logs"), patch.object(app.App, "_write_log_file"):
                ui = app.App(root)
            tabs = [
                ui.notebook.tab(tab_id, "text")
                for tab_id in ui.notebook.tabs()
            ]
        finally:
            root.destroy()

        self.assertEqual(tabs, ["Macro Builder", "Icon Alerts"])


if __name__ == "__main__":
    unittest.main()
