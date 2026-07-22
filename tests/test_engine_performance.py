import unittest

import numpy as np

from macro_clicker import engine as engine_module
from macro_clicker.engine import MacroEngine
from macro_clicker.rally_matching import _MATCHING_ROW_SNAPSHOT_KEY
from macro_clicker.models import Action, ImageCondition, Scenario, Step


class EnginePerformanceTests(unittest.TestCase):
    @staticmethod
    def _row_cycle_engine(step):
        engine = object.__new__(MacroEngine)
        engine.scenario = Scenario(name="row-perf", steps=[step])
        engine._last_fired = {step.name: 0.0}
        engine._stop_event = type(
            "Stop",
            (),
            {
                "is_set": lambda self: False,
                "wait": lambda self, _seconds: False,
            },
        )()
        engine._evaluate_uses_frame_cache = True
        engine._step_names_snapshot = ()
        engine._all_match_indices = {}
        engine._last_perf_log = {}
        engine._begin_level_diagnostic_generation = lambda: None
        engine._record_matching_row_diagnostic = lambda *args, **kwargs: None
        engine.log = lambda _message: None
        return engine

    def test_first_matching_row_action_reuses_initial_atomic_evaluation(self):
        reference = {"center": (80, 120), "box": (40, 90, 120, 150)}
        target = {"center": (260, 120), "box": (240, 100, 280, 140)}
        action = Action(
            type="click_matching_row",
            match_condition_index=0,
            on_condition_index=1,
        )
        step = Step(
            name="Joining",
            conditions=[
                ImageCondition(template_path="templates/Mob.png"),
                ImageCondition(template_path="templates/Join.png"),
            ],
            actions=[action],
            cooldown=0.0,
        )
        engine = self._row_cycle_engine(step)
        evaluations = []
        refreshes = []
        clicks = []
        snapshot = object()

        def evaluate_step(current_step, frame_cache=None):
            evaluations.append(current_step.name)
            frame_cache[_MATCHING_ROW_SNAPSHOT_KEY] = snapshot
            frame_cache[engine_module._MATCHING_ROW_SNAPSHOT_STEP_KEY] = current_step
            return (
                True,
                {0: reference["center"], 1: target["center"]},
                {0: [reference], 1: [target]},
            )

        engine._evaluate_step = evaluate_step
        engine._refresh_click_matching_row_matches = (
            lambda *_args: refreshes.append(True) or None
        )
        engine._click_point = lambda x, y, button: clicks.append((x, y, button))

        engine._cycle()

        self.assertEqual(evaluations, ["Joining"])
        self.assertEqual(refreshes, [])
        self.assertIs(engine._matching_row_snapshot, snapshot)
        self.assertEqual(clicks, [(260, 120, "left")])

    def test_prior_screen_change_forces_matching_row_refresh(self):
        reference = {"center": (80, 120), "box": (40, 90, 120, 150)}
        target = {"center": (260, 120), "box": (240, 100, 280, 140)}
        action = Action(
            type="click_matching_row",
            match_condition_index=0,
            on_condition_index=1,
        )
        step = Step(
            name="Joining",
            conditions=[
                ImageCondition(template_path="templates/Mob.png"),
                ImageCondition(template_path="templates/Join.png"),
            ],
            actions=[Action(type="wait", seconds=0.01), action],
            cooldown=0.0,
        )
        engine = self._row_cycle_engine(step)
        refreshes = []
        clicks = []

        def evaluate_step(current_step, frame_cache=None):
            frame_cache[_MATCHING_ROW_SNAPSHOT_KEY] = object()
            frame_cache[engine_module._MATCHING_ROW_SNAPSHOT_STEP_KEY] = current_step
            return (
                True,
                {0: reference["center"], 1: target["center"]},
                {0: [reference], 1: [target]},
            )

        def refresh(*_args):
            refreshes.append(True)
            return (
                {0: reference["center"], 1: target["center"]},
                {0: [reference], 1: [target]},
            )

        engine._evaluate_step = evaluate_step
        engine._refresh_click_matching_row_matches = refresh
        engine._click_point = lambda x, y, button: clicks.append((x, y, button))

        engine._cycle()

        self.assertEqual(refreshes, [True])
        self.assertEqual(clicks, [(260, 120, "left")])

    def test_reused_row_evaluation_still_refreshes_and_rechecks_level_after_delay(self):
        reference = {"center": (80, 120), "box": (40, 90, 120, 150)}
        target = {"center": (260, 120), "box": (240, 100, 280, 140)}
        action = Action(
            type="click_matching_row",
            match_condition_index=0,
            on_condition_index=1,
            max_level=60,
            pre_click_delay=0.25,
        )
        step = Step(
            name="Joining",
            conditions=[
                ImageCondition(template_path="templates/Mob.png"),
                ImageCondition(template_path="templates/Join.png"),
            ],
            actions=[action],
            cooldown=0.0,
        )
        engine = self._row_cycle_engine(step)
        refreshes = []
        level_reads = []
        clicks = []

        def evaluate_step(current_step, frame_cache=None):
            frame_cache[_MATCHING_ROW_SNAPSHOT_KEY] = object()
            frame_cache[engine_module._MATCHING_ROW_SNAPSHOT_STEP_KEY] = current_step
            return (
                True,
                {0: reference["center"], 1: target["center"]},
                {0: [reference], 1: [target]},
            )

        def refresh(*_args):
            refreshes.append(True)
            return (
                {0: reference["center"], 1: target["center"]},
                {0: [reference], 1: [target]},
            )

        engine._evaluate_step = evaluate_step
        engine._refresh_click_matching_row_matches = refresh
        engine._read_level_for_row = (
            lambda _action, _reference: level_reads.append(True) or 45
        )
        engine._click_point = lambda x, y, button: clicks.append((x, y, button))

        engine._cycle()

        self.assertEqual(refreshes, [True])
        self.assertEqual(level_reads, [True, True])
        self.assertEqual(clicks, [(260, 120, "left")])

    def test_evaluate_step_reuses_capture_for_conditions_in_same_region(self):
        engine = object.__new__(MacroEngine)
        engine.scenario = Scenario(name="perf")
        engine._resolve_capture_region = lambda cond: (10, 20, 40, 40)
        frame = np.zeros((40, 40, 3), dtype=np.uint8)
        grab_calls = []

        def grab(region=None):
            grab_calls.append(region)
            return frame, 10, 20

        engine._grab = grab
        engine._load_template = lambda path: np.zeros((3, 3, 3), dtype=np.uint8)
        step = Step(
            name="same-region",
            conditions=[
                ImageCondition(template_path="templates/a.png", confidence=0.99),
                ImageCondition(template_path="templates/b.png", confidence=0.99),
            ],
        )

        engine._evaluate_step(step)

        self.assertEqual(grab_calls, [(10, 20, 40, 40)])

    def test_cycle_reuses_target_window_lookup_across_conditions(self):
        engine = object.__new__(MacroEngine)
        engine.scenario = Scenario(name="perf", target_window_title="Game")
        engine._target_window_rect = None
        engine._target_window_missing_logged = False
        engine._last_fired = {"same-window": 0.0}
        engine._stop_event = type("Stop", (), {"is_set": lambda self: False})()
        engine.log = lambda message: None
        frame = np.zeros((40, 40, 3), dtype=np.uint8)
        window_lookups = []

        def window_rect_provider(title):
            window_lookups.append(title)
            return (10, 20, 40, 40)

        engine._window_rect_provider = window_rect_provider
        engine._grab = lambda region=None: (frame, 10, 20)
        engine._load_template = lambda path: np.zeros((3, 3, 3), dtype=np.uint8)
        engine.scenario.steps = [
            Step(
                name="same-window",
                conditions=[
                    ImageCondition(template_path="templates/a.png", confidence=2.0),
                    ImageCondition(template_path="templates/b.png", confidence=2.0),
                ],
                cooldown=0.0,
            )
        ]

        engine._cycle()

        self.assertEqual(window_lookups, ["Game"])

    def test_cycle_reports_when_a_step_fired(self):
        engine = object.__new__(MacroEngine)
        engine.scenario = Scenario(name="perf")
        engine._last_fired = {"fire": 0.0}
        engine._stop_event = type("Stop", (), {"is_set": lambda self: False})()
        engine._evaluate_step = lambda step: (True, {}, {})
        engine._run_action = lambda step, action, points, matches: None
        engine.log = lambda message: None
        engine.scenario.steps = [Step(name="fire", cooldown=0.0)]

        self.assertTrue(engine._cycle())

    def test_grab_falls_back_when_saved_monitor_is_unavailable(self):
        engine = object.__new__(MacroEngine)
        engine.scenario = Scenario(name="perf", monitor_index=9)
        logs = []
        grabbed = []

        class FakeCapture:
            monitors = [
                {"left": 0, "top": 0, "width": 20, "height": 10},
                {"left": 3, "top": 4, "width": 8, "height": 6},
            ]

            def grab(self, monitor):
                grabbed.append(monitor)
                return np.zeros((monitor["height"], monitor["width"], 4), dtype=np.uint8)

        engine.sct = FakeCapture()
        engine.log = logs.append

        _, left, top = engine._grab()
        engine._grab()

        self.assertEqual((left, top), (3, 4))
        self.assertEqual(grabbed[0], engine.sct.monitors[1])
        self.assertEqual(len(logs), 1)

    def test_monitor_fallback_uses_the_same_resolution_for_template_scaling(self):
        engine = object.__new__(MacroEngine)
        engine.scenario = Scenario(name="fallback", monitor_index=9)
        engine.log = lambda _message: None
        engine._monitor_index_warning_logged = None

        class FakeCapture:
            monitors = [
                {"left": 0, "top": 0, "width": 2560, "height": 1440},
                {"left": 0, "top": 0, "width": 2560, "height": 1440},
            ]

        engine.sct = FakeCapture()
        condition = ImageCondition(
            template_path="templates/a.png",
            template_reference_size=[1920, 1080],
        )

        kwargs = engine._condition_matching_kwargs(condition)

        self.assertEqual(kwargs["current_size"], (2560, 1440))

    def test_cycle_uses_monotonic_time_for_cooldowns(self):
        engine = object.__new__(MacroEngine)
        engine.scenario = Scenario(name="clock")
        engine._last_fired = {}
        engine._stop_event = type("Stop", (), {"is_set": lambda self: False})()

        from unittest.mock import patch

        from macro_clicker import engine as engine_module

        with patch.object(engine_module.time, "monotonic", return_value=10.0) as monotonic, \
                patch.object(engine_module.time, "time", side_effect=AssertionError("wall clock used")):
            engine._cycle()

        monotonic.assert_called_once()

    def test_scaled_templates_are_cached(self):
        engine = object.__new__(MacroEngine)
        template = np.zeros((20, 30, 3), dtype=np.uint8)

        from unittest.mock import patch

        from macro_clicker import engine as engine_module

        with patch.object(engine_module.cv2, "resize", wraps=engine_module.cv2.resize) as resize:
            first = engine._scaled_template(template, 1.2)
            second = engine._scaled_template(template, 1.2)

        self.assertIs(first, second)
        resize.assert_called_once()

    def test_level_ocr_warm_up_logs_when_ready(self):
        engine = object.__new__(MacroEngine)
        logs = []
        calls = []

        class FakeReader:
            init_error = None

            def warm_up(self):
                calls.append("warm")
                return True

        engine.log = logs.append
        engine._level_ocr_reader = FakeReader()

        engine._warm_up_level_ocr()

        self.assertEqual(calls, ["warm"])
        self.assertTrue(any("[ocr] warm-up ready" in message for message in logs))

    def test_smart_row_with_blank_ordinary_limits_still_uses_level_ocr(self):
        engine = object.__new__(MacroEngine)
        engine.scenario = Scenario(
            name="Smart OCR",
            steps=[
                Step(
                    name="Joining",
                    actions=[
                        Action(
                            type="click_matching_row",
                            team_status_region=[0, 0, 100, 100],
                            team_status_reference_size=[1920, 1080],
                            team1_busy_template_path="team1-busy.png",
                            team3_busy_template_path="team3-busy.png",
                        )
                    ],
                )
            ],
        )

        self.assertTrue(engine._scenario_uses_level_ocr())

    def test_scenario_waits_for_ocr_warm_up_before_running(self):
        engine = object.__new__(MacroEngine)
        calls = []
        logs = []
        engine.scenario = Scenario(name="levels", kill_switch="f12")
        engine.log = logs.append
        engine._stop_event = type("Stop", (), {"is_set": lambda self: False})()
        engine._warm_up_level_ocr = lambda: calls.append("warm") or True
        engine._run_loop = lambda: calls.append("run")

        engine._run_after_ocr_warmup()

        self.assertEqual(calls, ["warm", "run"])
        self.assertTrue(any("Scenario 'levels' started" in message for message in logs))

    def test_evaluate_step_short_circuits_failed_and_condition(self):
        engine = object.__new__(MacroEngine)
        calls = []

        def evaluate_condition(index, cond, frame_cache, collect_all=True):
            calls.append(index)
            return index != 0, []

        engine._evaluate_condition = evaluate_condition
        step = Step(
            name="short-circuit",
            conditions=[
                ImageCondition(template_path="templates/a.png"),
                ImageCondition(template_path="templates/b.png"),
            ],
            condition_operator="AND",
        )

        met, points, matches = engine._evaluate_step(step)

        self.assertFalse(met)
        self.assertEqual(calls, [0])
        self.assertEqual(points, {})
        self.assertEqual(matches, {0: []})

    def test_simple_click_condition_uses_best_match_only(self):
        engine = object.__new__(MacroEngine)
        engine.scenario = Scenario(name="perf")
        engine._resolve_capture_region = lambda cond: (10, 20, 40, 40)
        template = np.array(
            [
                [[0, 0, 0], [255, 255, 255], [0, 0, 0]],
                [[255, 255, 255], [0, 0, 0], [255, 255, 255]],
                [[0, 0, 0], [255, 255, 255], [0, 0, 0]],
            ],
            dtype=np.uint8,
        )
        frame = np.zeros((40, 40, 3), dtype=np.uint8)
        frame[12:15, 18:21] = template
        engine._grab = lambda region=None: (frame, 10, 20)
        engine._load_template = lambda path: template

        def fail_if_collecting_all_matches(*args, **kwargs):
            raise AssertionError("simple click conditions should not collect every match")

        engine._find_template_matches = fail_if_collecting_all_matches
        step = Step(
            name="simple-click",
            conditions=[ImageCondition(template_path="templates/a.png", confidence=0.5)],
            actions=[Action(type="click", on_condition_index=0)],
        )

        met, points, matches = engine._evaluate_step(step)

        self.assertTrue(met)
        self.assertIn(0, points)
        self.assertEqual(len(matches[0]), 1)

if __name__ == "__main__":
    unittest.main()
