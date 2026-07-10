import os
import queue
import json
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

import numpy as np

import app
import alert_watcher
import capture_tool
import engine as engine_module
import models
from engine import MacroEngine
from level_ocr import LevelOcrReader
from models import Action, ImageCondition, Scenario, Step


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

    def test_click_point_moves_only_once_when_move_duration_is_zero(self):
        engine = object.__new__(MacroEngine)
        engine.click_move_duration = 0.0
        calls = []

        with patch.object(engine_module.pyautogui, "moveTo", side_effect=lambda *args, **kwargs: calls.append(("move", args, kwargs))), \
                patch.object(engine_module.pyautogui, "click", side_effect=lambda *args, **kwargs: calls.append(("click", args, kwargs))):
            engine._click_point(10, 20, "left")

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "click")
        self.assertEqual(calls[0][2]["x"], 10)
        self.assertEqual(calls[0][2]["y"], 20)

    def test_stop_joins_running_thread_before_returning(self):
        logs = []
        joined = []
        closed = []

        class FakeThread:
            def __init__(self):
                self.alive = True

            def is_alive(self):
                return self.alive

            def join(self, timeout=None):
                joined.append(timeout)
                self.alive = False

        engine = object.__new__(MacroEngine)
        engine.log = logs.append
        engine._stop_event = threading.Event()
        engine._hotkey_handle = None
        engine._ever_started = True
        engine._thread = FakeThread()
        engine.sct = type("Capture", (), {"close": lambda self: closed.append(True)})()

        engine.stop()

        self.assertEqual(joined, [2.0])
        self.assertEqual(closed, [True])
        self.assertEqual(logs, ["Scenario stopped."])

    def test_f11_start_hotkey_queues_start_on_ui_thread(self):
        ui = object.__new__(app.App)
        ui.control_queue = queue.Queue()
        ui._start_hotkey_handle = None

        with patch.object(app.keyboard, "add_hotkey", return_value="f11-handle") as add_hotkey:
            ui._register_start_hotkey()
            callback = add_hotkey.call_args.args[1]
            callback()

        self.assertEqual(add_hotkey.call_args.args[0], "f11")
        self.assertEqual(ui._start_hotkey_handle, "f11-handle")
        self.assertEqual(ui.control_queue.get_nowait(), "start")

    def test_f11_start_hotkey_is_ignored_while_engine_is_running(self):
        ui = object.__new__(app.App)
        ui.engine = type("Engine", (), {"is_running": True})()
        ui._start_engine = Mock()

        ui._start_engine_from_hotkey()

        ui._start_engine.assert_not_called()

    def test_model_default_paths_are_project_relative(self):
        self.assertTrue(os.path.isabs(models.SCENARIOS_DIR))
        self.assertTrue(os.path.isabs(models.TEMPLATES_DIR))
        self.assertTrue(
            os.path.normpath(models.project_path("templates/icon.png")).endswith(
                os.path.join("templates", "icon.png")
            )
        )

    def test_action_from_dict_coerces_saved_json_types(self):
        action = Action.from_dict({
            "type": "click_matching_row",
            "on_condition_index": "2",
            "match_condition_index": "1",
            "row_tolerance": "45",
            "max_level": "55",
            "no_match_disable_steps": "Joining, Attack Confirm",
            "set_enabled": "false",
        })

        self.assertEqual(action.on_condition_index, 2)
        self.assertEqual(action.match_condition_index, 1)
        self.assertEqual(action.row_tolerance, 45)
        self.assertEqual(action.max_level, 55)
        self.assertEqual(action.no_match_disable_steps, ["Joining", "Attack Confirm"])
        self.assertFalse(action.set_enabled)

    def test_load_scenario_reports_malformed_json_as_value_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with open(os.path.join(temp_dir, "Broken.json"), "w", encoding="utf-8") as f:
                f.write("{not json")

            with self.assertRaises(ValueError) as ctx:
                models.load_scenario("Broken", folder=temp_dir)

        self.assertIn("Could not load scenario 'Broken'", str(ctx.exception))

    def test_level_ocr_text_entry_extraction_handles_recursive_data(self):
        reader = LevelOcrReader()
        raw = []
        raw.append(raw)

        self.assertEqual(list(reader._extract_text_entries(raw)), [])

    def test_cycle_reuses_frame_cache_across_steps(self):
        engine = object.__new__(MacroEngine)
        engine.scenario = Scenario(
            name="cache",
            steps=[
                Step(name="one", conditions=[ImageCondition(template_path="templates/a.png", confidence=2.0)]),
                Step(name="two", conditions=[ImageCondition(template_path="templates/b.png", confidence=2.0)]),
            ],
        )
        engine._last_fired = {"one": 0.0, "two": 0.0}
        engine._stop_event = threading.Event()
        engine.log = lambda _message: None
        engine._resolve_capture_region = lambda _cond: (1, 2, 30, 30)
        frame = np.zeros((30, 30, 3), dtype=np.uint8)
        grab_calls = []

        def grab(region=None):
            grab_calls.append(region)
            return frame, 1, 2

        engine._grab = grab
        engine._load_template = lambda _path: np.zeros((3, 3, 3), dtype=np.uint8)

        engine._cycle()

        self.assertEqual(grab_calls, [(1, 2, 30, 30)])

    def test_cycle_passes_frame_cache_to_compatible_override(self):
        seen_caches = []

        class CacheAwareEngine(MacroEngine):
            def _evaluate_step(self, step, frame_cache=None):
                seen_caches.append(frame_cache)
                return False, {}, {}

        engine = object.__new__(CacheAwareEngine)
        engine.scenario = Scenario(name="cache-aware", steps=[Step(name="one", cooldown=0.0)])
        engine._last_fired = {"one": 0.0}
        engine._stop_event = threading.Event()
        engine.log = lambda _message: None

        engine._cycle()

        self.assertEqual(len(seen_caches), 1)
        self.assertIsInstance(seen_caches[0], dict)

    def test_log_file_flushes_once_on_hundredth_write(self):
        class FakeHandle:
            def __init__(self):
                self.flush_count = 0
                self.writes = []

            def write(self, text):
                self.writes.append(text)

            def flush(self):
                self.flush_count += 1

        ui = object.__new__(app.App)
        handle = FakeHandle()
        ui._log_file_handle = handle
        ui._log_write_count = 99
        ui.log_max_bytes = 10_000
        ui.log_backups = 3

        with tempfile.TemporaryDirectory() as temp_dir:
            ui.log_dir = temp_dir
            ui.log_file_path = os.path.join(temp_dir, "pc_macro_builder.log")
            with open(ui.log_file_path, "w", encoding="utf-8") as f:
                f.write("")

            ui._write_log_file("line")

        self.assertEqual(handle.flush_count, 1)

    def test_app_log_write_does_not_makedirs_on_each_line(self):
        class FakeHandle:
            def write(self, _text):
                pass

            def flush(self):
                pass

        ui = object.__new__(app.App)
        ui._log_file_handle = FakeHandle()
        ui._log_write_count = 0
        ui.log_file_path = "unused.log"
        ui.log_max_bytes = 10_000
        ui.log_backups = 3

        with patch.object(app.os, "makedirs") as makedirs:
            ui._write_log_file("line")

        makedirs.assert_not_called()

    def test_screen_region_picker_ignores_release_and_drag_without_press(self):
        picker = object.__new__(alert_watcher.ScreenRegionPicker)
        picker.start_x = None
        picker.start_y = None
        picker.rect_id = None
        picker.canvas = Mock()

        event = type("Event", (), {"x": 10, "y": 20})()

        picker._on_drag(event)
        picker._on_release(event)

        picker.canvas.coords.assert_not_called()

    def test_alert_log_trim_uses_counter(self):
        class FakeText:
            def __init__(self):
                self.deleted = []

            def config(self, **_kwargs):
                pass

            def insert(self, *_args):
                pass

            def delete(self, start, end):
                self.deleted.append((start, end))

            def see(self, *_args):
                pass

            def index(self, *_args):
                raise AssertionError("line counting should use the counter, not Text.index")

        frame = object.__new__(alert_watcher.AlertWatcherFrame)
        frame.log_text = FakeText()
        frame.log_text_max_lines = 2
        frame._log_line_count = 2

        frame._append_log("third")

        self.assertEqual(frame._log_line_count, 2)
        self.assertEqual(frame.log_text.deleted, [("1.0", "2.0")])

    def test_save_scenario_uses_atomic_replace(self):
        scenario = Scenario(name="Atomic")
        with tempfile.TemporaryDirectory() as temp_dir, \
                patch.object(models.os, "replace", wraps=models.os.replace) as replace:
            path = models.save_scenario(scenario, folder=temp_dir)

            replace.assert_called_once()
            self.assertFalse(os.path.exists(f"{path}.tmp"))
            with open(path, encoding="utf-8") as f:
                self.assertEqual(json.load(f)["name"], "Atomic")

    def test_save_scenario_rejects_filename_unsafe_names(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            for name in ("../escape", "bad/name", "bad\\name", "bad:name", "   "):
                with self.subTest(name=name):
                    with self.assertRaises(ValueError):
                        models.save_scenario(Scenario(name=name), folder=temp_dir)

    def test_alert_settings_save_uses_atomic_replace(self):
        settings = alert_watcher.AppSettings(target_window_title="Game")
        with tempfile.TemporaryDirectory() as temp_dir, \
                patch.object(alert_watcher.os, "replace", wraps=alert_watcher.os.replace) as replace:
            path = os.path.join(temp_dir, "settings.json")

            alert_watcher.save_settings(path, settings)

            replace.assert_called_once()
            self.assertFalse(os.path.exists(f"{path}.tmp"))

    def test_engine_cycle_does_not_inspect_signature_every_cycle_when_cached(self):
        engine = object.__new__(MacroEngine)
        engine.scenario = Scenario(name="cached", steps=[Step(name="one", cooldown=0.0)])
        engine._last_fired = {"one": 0.0}
        engine._stop_event = threading.Event()
        engine.log = lambda _message: None
        engine._evaluate_uses_frame_cache = False
        engine._evaluate_step = lambda _step: (False, {}, {})

        with patch.object(engine_module.inspect, "signature", side_effect=AssertionError("should be cached")):
            engine._cycle()

    def test_mouse_position_fill_hides_dialog_and_writes_pointer_coordinates(self):
        class FakeDialog:
            def __init__(self):
                self.hidden = False
                self.after_delay = None
                self.callback = None
                self.grabbed = False

            def withdraw(self):
                self.hidden = True

            def after(self, delay, callback):
                self.after_delay = delay
                self.callback = callback

            def winfo_pointerx(self):
                return 123

            def winfo_pointery(self):
                return 456

            def deiconify(self):
                self.hidden = False

            def lift(self):
                pass

            def grab_set(self):
                self.grabbed = True

        dialog = FakeDialog()
        x_var = FakeVar("")
        y_var = FakeVar("")

        app.schedule_mouse_position_fill(dialog, x_var, y_var, delay_ms=10)
        dialog.callback()

        self.assertEqual(dialog.after_delay, 10)
        self.assertEqual(x_var.get(), "123")
        self.assertEqual(y_var.get(), "456")
        self.assertFalse(dialog.hidden)
        self.assertTrue(dialog.grabbed)


if __name__ == "__main__":
    unittest.main()
