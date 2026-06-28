import unittest

import numpy as np

from engine import MacroEngine
from models import Action, ImageCondition, Scenario, Step


class EnginePerformanceTests(unittest.TestCase):
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
        engine._grab = lambda region=None: (np.zeros((40, 40, 3), dtype=np.uint8), 10, 20)
        engine._load_template = lambda path: np.zeros((3, 3, 3), dtype=np.uint8)

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

    def test_read_level_from_frame_uses_digit_templates(self):
        engine = object.__new__(MacroEngine)
        templates = {
            "2": np.array(
                [
                    [0, 255, 255, 0],
                    [255, 0, 0, 255],
                    [0, 0, 255, 0],
                    [0, 255, 0, 0],
                    [255, 255, 255, 255],
                ],
                dtype=np.uint8,
            ),
            "7": np.array(
                [
                    [255, 255, 255, 255],
                    [0, 0, 0, 255],
                    [0, 0, 255, 0],
                    [0, 255, 0, 0],
                    [0, 255, 0, 0],
                ],
                dtype=np.uint8,
            ),
        }
        frame = np.zeros((12, 18), dtype=np.uint8)
        frame[3:8, 4:8] = templates["2"]
        frame[3:8, 10:14] = templates["7"]

        self.assertEqual(engine._read_level_from_frame(frame, templates, confidence=0.99), 27)

    def test_read_level_from_frame_respects_min_digits(self):
        engine = object.__new__(MacroEngine)
        templates = {
            "8": np.array(
                [
                    [255, 255, 255],
                    [255, 0, 255],
                    [255, 255, 255],
                    [255, 0, 255],
                    [255, 255, 255],
                ],
                dtype=np.uint8,
            ),
        }
        frame = np.zeros((9, 9), dtype=np.uint8)
        frame[2:7, 3:6] = templates["8"]

        self.assertIsNone(engine._read_level_from_frame(frame, templates, confidence=0.99, min_digits=2))
        self.assertEqual(engine._read_level_from_frame(frame, templates, confidence=0.99, min_digits=1), 8)

    def test_digit_preprocessing_ignores_background_color(self):
        engine = object.__new__(MacroEngine)
        glyph = np.array(
            [
                [0, 0, 255, 0],
                [0, 255, 255, 0],
                [255, 0, 255, 0],
                [255, 255, 255, 255],
                [0, 0, 255, 0],
            ],
            dtype=np.uint8,
        )
        red_background = np.full(glyph.shape, 76, dtype=np.uint8)
        red_background[glyph > 0] = 255
        patterned_background = np.array(
            [
                [41, 152, 88, 121],
                [97, 64, 139, 33],
                [155, 45, 118, 92],
                [68, 132, 57, 104],
                [119, 37, 166, 73],
            ],
            dtype=np.uint8,
        )
        patterned_background[glyph > 0] = 255

        red_mask = engine._preprocess_digit_image(red_background)
        patterned_mask = engine._preprocess_digit_image(patterned_background)

        self.assertGreater(int((red_mask > 0).sum()), 0)
        self.assertGreater(int((patterned_mask > 0).sum()), 0)
        self.assertTrue(np.array_equal(red_mask, patterned_mask))

    def test_digit_preprocessing_removes_small_bright_specks(self):
        engine = object.__new__(MacroEngine)
        digit = np.array(
            [
                [0, 0, 255, 0],
                [0, 255, 255, 0],
                [255, 0, 255, 0],
                [255, 255, 255, 255],
                [0, 0, 255, 0],
            ],
            dtype=np.uint8,
        )
        noisy = np.zeros((9, 9), dtype=np.uint8)
        noisy[2:7, 2:6] = digit
        noisy[0, 0] = 255
        noisy[0, 8] = 255
        noisy[8, 0] = 255
        clean = np.zeros((9, 9), dtype=np.uint8)
        clean[2:7, 2:6] = digit

        self.assertTrue(np.array_equal(
            engine._preprocess_digit_image(clean),
            engine._preprocess_digit_image(noisy),
        ))


if __name__ == "__main__":
    unittest.main()
