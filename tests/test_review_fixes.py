import os
import queue
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

import app
import alert_watcher
import capture_tool
from engine import MacroEngine
from level_ocr import LevelOcrReader
from models import Action, Scenario, Step


class FakeVar:
    def __init__(self, value=None):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class FakeCapture:
    def __init__(self):
        self.saved_path = None

    def save(self, path):
        self.saved_path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(b"png")


class ReviewFixTests(unittest.TestCase):
    def test_level_ocr_preserves_real_three_digit_lv_levels(self):
        reader = LevelOcrReader()

        self.assertEqual(reader._extract_level("Lv.151"), 151)
        self.assertEqual(reader._extract_level("Lv.170"), 170)
        self.assertEqual(reader._extract_level("LV250"), 25)
        self.assertEqual(reader._extract_level("LV-407"), 40)
        self.assertEqual(reader._extract_level("L.500"), 50)

    def test_capture_template_suffixes_filename_not_parent_directory(self):
        crop = FakeCapture()
        with tempfile.TemporaryDirectory(suffix=".png_backup") as tmp:
            existing = os.path.join(tmp, "Rally.png")
            with open(existing, "wb") as f:
                f.write(b"old")

            with patch.object(capture_tool, "select_region", return_value=((0, 0, 10, 10), crop)), \
                    patch.object(capture_tool.simpledialog, "askstring", return_value="Rally"):
                path = capture_tool.capture_template(None, save_dir=tmp)

        self.assertEqual(os.path.basename(path), "Rally_1.png")
        self.assertEqual(os.path.dirname(path), tmp)
        self.assertEqual(crop.saved_path, path)

    def test_engine_stop_closes_capture_without_spurious_log_when_never_started(self):
        logs = []
        closed = []
        engine = object.__new__(MacroEngine)
        engine.log = logs.append
        engine._stop_event = threading.Event()
        engine._hotkey_handle = None
        engine._thread = None
        engine.sct = type("Capture", (), {"close": lambda self: closed.append(True)})()

        engine.stop()

        self.assertEqual(logs, [])
        self.assertEqual(closed, [True])

    def test_wait_action_returns_immediately_when_stop_is_set(self):
        logs = []
        engine = object.__new__(MacroEngine)
        engine.log = logs.append
        engine._stop_event = threading.Event()
        engine._stop_event.set()

        start = time.perf_counter()
        engine._run_action(Step(name="Wait"), Action(type="wait", seconds=0.25), {}, {})

        self.assertLess(time.perf_counter() - start, 0.05)

    def test_save_as_refuses_to_overwrite_existing_scenario(self):
        ui = object.__new__(app.App)
        ui.root = None
        ui.scenario = Scenario(name="Current")
        ui.scenario_var = FakeVar("Current")
        ui._save_scenario = Mock()

        with patch.object(app.simpledialog, "askstring", return_value="Existing"), \
                patch.object(app, "list_scenarios", return_value=["Existing"]), \
                patch.object(app.messagebox, "showerror") as showerror:
            ui._save_scenario_as()

        ui._save_scenario.assert_not_called()
        showerror.assert_called_once()
        self.assertEqual(ui.scenario.name, "Current")
        self.assertEqual(ui.scenario_var.get(), "Current")

    def test_delete_scenario_resets_active_scenario(self):
        ui = object.__new__(app.App)
        ui.scenario = Scenario(name="Old", steps=[Step(name="Step")])
        ui.scenario_var = FakeVar("Old")
        ui.poll_var = FakeVar(0.25)
        ui.monitor_var = FakeVar(1)
        ui.kill_var = FakeVar("f12")
        ui.target_window_var = FakeVar("")
        ui._refresh_scenario_list = Mock()
        ui._refresh_steps = Mock()

        with patch.object(app.messagebox, "askyesno", return_value=True), \
                patch.object(app, "delete_scenario") as delete_scenario:
            ui._delete_scenario()

        delete_scenario.assert_called_once_with("Old")
        self.assertEqual(ui.scenario.name, "untitled")
        self.assertEqual(ui.scenario.steps, [])
        self.assertEqual(ui.scenario_var.get(), "untitled")

    def test_parse_optional_int_reports_invalid_numbers(self):
        self.assertIsNone(app._parse_optional_int("", "Condition"))
        self.assertEqual(app._parse_optional_int("7", "Condition"), 7)
        with self.assertRaises(ValueError):
            app._parse_optional_int("abc", "Condition")

    def test_models_accept_missing_names_in_malformed_json(self):
        step = Step.from_dict({})
        scenario = Scenario.from_dict({})

        self.assertEqual(step.name, "")
        self.assertEqual(scenario.name, "untitled")

    def test_alert_watcher_drains_queues_without_empty_get_race(self):
        q = queue.Queue()
        q.put("one")
        q.put("two")

        self.assertEqual(list(alert_watcher._drain_queue(q)), ["one", "two"])
        self.assertEqual(list(alert_watcher._drain_queue(q)), [])


if __name__ == "__main__":
    unittest.main()
