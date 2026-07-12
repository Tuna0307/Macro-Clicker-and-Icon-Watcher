import threading
import unittest
from unittest.mock import patch

import cv2
import numpy as np

import engine as engine_module
from engine import MacroEngine
from models import Action, ImageCondition, Scenario, Step


class EngineSafetyTests(unittest.TestCase):
    def _bare_engine(self):
        engine = object.__new__(MacroEngine)
        engine._stop_event = threading.Event()
        engine.log = lambda _message: None
        return engine

    def test_stop_during_wait_prevents_every_following_action(self):
        engine = self._bare_engine()
        clicked = []
        step = Step(
            name="stop-after-wait",
            actions=[
                Action(type="wait", seconds=1.0),
                Action(type="click", x=50, y=60),
            ],
            cooldown=0.0,
        )
        engine.scenario = Scenario(name="safety", steps=[step])
        engine._last_fired = {step.name: 0.0}
        engine._evaluate_step = lambda _step: (True, {}, {})
        engine._click_point = lambda x, y, button: clicked.append((x, y, button))

        def stop_during_wait(_seconds):
            engine._stop_event.set()
            return True

        engine._sleep_until_stop = stop_during_wait

        engine._cycle()

        self.assertEqual(clicked, [])

    def test_click_point_rechecks_stop_after_mouse_move(self):
        engine = self._bare_engine()
        engine.click_move_duration = 0.2

        def stop_after_move(*_args, **_kwargs):
            engine._stop_event.set()

        with patch.object(engine_module.pyautogui, "moveTo", side_effect=stop_after_move), \
                patch.object(engine_module.pyautogui, "click") as click:
            clicked = engine._click_point(10, 20, "left")

        self.assertFalse(clicked)
        click.assert_not_called()

    def test_click_point_outside_all_monitors_is_skipped(self):
        engine = self._bare_engine()
        engine.click_move_duration = 0.0
        engine.sct = type(
            "Capture",
            (),
            {
                "monitors": [
                    {"left": 0, "top": 0, "width": 100, "height": 100},
                    {"left": 0, "top": 0, "width": 100, "height": 100},
                ]
            },
        )()

        with patch.object(engine_module.pyautogui, "click") as click:
            result = engine._click_point(500, 500, "left")

        self.assertFalse(result)
        click.assert_not_called()

    def test_side_effect_clears_frame_cache_before_later_step(self):
        engine = self._bare_engine()
        rng = np.random.default_rng(17)
        template = rng.integers(0, 256, (8, 8, 3), dtype=np.uint8)
        visible = np.zeros((35, 35, 3), dtype=np.uint8)
        visible[10:18, 12:20] = template
        absent = np.zeros_like(visible)
        current_frame = [visible]
        grabs = []
        clicks = []

        def grab(region=None):
            grabs.append(region)
            return current_frame[0].copy(), 0, 0

        def click(x, y, button):
            clicks.append((x, y, button))
            current_frame[0] = absent
            return True

        def condition():
            return ImageCondition(template_path="target.png", confidence=0.99)
        steps = [
            Step(
                name="first",
                conditions=[condition()],
                actions=[Action(type="click", on_condition_index=0)],
                cooldown=0.0,
            ),
            Step(
                name="second",
                conditions=[condition()],
                actions=[Action(type="click", on_condition_index=0)],
                cooldown=0.0,
            ),
        ]
        engine.scenario = Scenario(name="fresh-frames", steps=steps)
        engine._last_fired = {step.name: 0.0 for step in steps}
        engine._resolve_capture_region = lambda _condition: (0, 0, 35, 35)
        engine._grab = grab
        engine._load_template = lambda _path: template
        engine._click_point = click

        engine._cycle()

        self.assertEqual(len(grabs), 2)
        self.assertEqual(len(clicks), 1)

    def test_explicit_missing_click_target_never_falls_back_to_another_condition(self):
        engine = self._bare_engine()
        clicks = []
        logs = []
        engine.log = logs.append
        engine._click_point = lambda x, y, button: clicks.append((x, y, button))
        points = {0: (10, 20)}

        changed = engine._run_action(
            Step(name="targeting"),
            Action(type="click", on_condition_index=1),
            points,
            {},
        )

        self.assertFalse(changed)
        self.assertEqual(clicks, [])
        self.assertTrue(any("condition #1 has no match" in message for message in logs))

        engine._run_action(Step(name="automatic"), Action(type="click"), points, {})
        self.assertEqual(clicks, [(10, 20, "left")])

    def test_best_match_is_selected_globally_across_scales(self):
        engine = self._bare_engine()
        rng = np.random.default_rng(23)
        template = rng.integers(0, 256, (20, 20, 3), dtype=np.uint8)
        decoy = template.copy()
        decoy[2:7, 2:7] = 255 - decoy[2:7, 2:7]
        scaled = cv2.resize(template, (24, 24), interpolation=cv2.INTER_LINEAR)
        frame = np.zeros((80, 120, 3), dtype=np.uint8)
        frame[8:28, 7:27] = decoy
        frame[42:66, 73:97] = scaled

        matches = engine._find_template_matches_in_frame(
            frame, template, confidence=0.55, collect_all=False
        )

        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0][:2], (73, 42))
        self.assertEqual(matches[0][5], 1.2)

    def test_collect_all_keeps_targets_at_different_scales(self):
        engine = self._bare_engine()
        rng = np.random.default_rng(31)
        template = rng.integers(0, 256, (20, 20, 3), dtype=np.uint8)
        scaled = cv2.resize(template, (24, 24), interpolation=cv2.INTER_LINEAR)
        frame = np.zeros((85, 130, 3), dtype=np.uint8)
        frame[7:27, 9:29] = template
        frame[45:69, 82:106] = scaled

        matches = engine._find_template_matches_in_frame(
            frame, template, confidence=0.85, collect_all=True
        )

        self.assertTrue(any(match[:2] == (9, 7) and match[5] == 1.0 for match in matches))
        self.assertTrue(any(match[:2] == (82, 45) and match[5] == 1.2 for match in matches))

    def test_flat_low_variance_template_does_not_create_false_match_flood(self):
        engine = self._bare_engine()
        template = np.zeros((5, 5, 3), dtype=np.uint8)
        frame = np.zeros((80, 80, 3), dtype=np.uint8)

        matches = engine._find_template_matches_in_frame(
            frame, template, confidence=0.99, collect_all=True
        )

        self.assertEqual(matches, [])

    def test_low_variance_template_uses_sqdiff_for_a_unique_location(self):
        engine = self._bare_engine()
        template = np.zeros((5, 5, 3), dtype=np.uint8)
        frame = np.full((50, 60, 3), 255, dtype=np.uint8)
        frame[20:25, 30:35] = template

        matches = engine._find_template_matches_in_frame(
            frame, template, confidence=0.99, collect_all=False
        )

        self.assertEqual(matches[0][:2], (30, 20))
        self.assertEqual(matches[0][5], 1.0)

    def test_spatially_flat_colored_template_is_treated_as_low_variance(self):
        engine = self._bare_engine()
        template = np.full((8, 8, 3), (10, 80, 220), dtype=np.uint8)
        ambiguous = np.full((60, 70, 3), (10, 80, 220), dtype=np.uint8)

        self.assertEqual(
            engine._find_template_matches_in_frame(
                ambiguous, template, confidence=0.99, collect_all=False
            ),
            [],
        )

        unique = np.zeros_like(ambiguous)
        unique[22:30, 31:39] = template
        matches = engine._find_template_matches_in_frame(
            unique, template, confidence=0.99, collect_all=False
        )
        self.assertEqual(matches[0][:2], (31, 22))

    def test_large_frame_coarse_search_verifies_exact_full_resolution_location(self):
        engine = self._bare_engine()
        rng = np.random.default_rng(101)
        template = rng.integers(0, 256, (24, 30, 3), dtype=np.uint8)
        frame = np.zeros((600, 1000, 3), dtype=np.uint8)
        frame[421:445, 713:743] = template

        matches = engine._find_template_matches_in_frame(
            frame, template, confidence=0.99, collect_all=False
        )

        self.assertEqual(matches[0][:2], (713, 421))
        self.assertAlmostEqual(matches[0][4], 1.0, places=5)

    def test_engine_refuses_to_start_without_required_kill_switch(self):
        class FakeCapture:
            def close(self):
                pass

        with patch.object(engine_module.mss, "MSS", return_value=FakeCapture()), \
                patch.object(
                    engine_module.keyboard,
                    "add_hotkey",
                    side_effect=OSError("hook unavailable"),
                ):
            runtime = MacroEngine(Scenario(name="safe"))
            with self.assertRaisesRegex(RuntimeError, "required kill switch"):
                runtime.start()

        self.assertFalse(runtime.is_running)
        self.assertTrue(runtime._sct_closed)

    def test_runtime_and_preview_compare_rival_only_near_each_target(self):
        engine = self._bare_engine()
        rng = np.random.default_rng(43)
        target = rng.integers(0, 256, (12, 12, 3), dtype=np.uint8)
        rival = target.copy()
        rival[2:7, 2:7] = 255 - rival[2:7, 2:7]
        frame = np.zeros((100, 40, 3), dtype=np.uint8)
        frame[6:18, 10:22] = target
        frame[72:84, 10:22] = rival
        templates = {"target.png": target, "rival.png": rival}
        engine._load_template = templates.__getitem__
        condition = ImageCondition(
            template_path="target.png",
            confidence=0.5,
            comparison_template_path="rival.png",
            comparison_margin=0.03,
        )

        runtime_ok, runtime_matches = engine._evaluate_template_condition(
            0, condition, frame, 0, 0, collect_all=False
        )
        preview_ok, preview_matches, _ = engine._preview_template_condition(
            0, condition, frame, 0, 0, None
        )

        self.assertTrue(runtime_ok)
        self.assertTrue(preview_ok)
        self.assertEqual(runtime_matches[0]["image_box"], (10, 6, 22, 18))
        self.assertEqual(preview_matches[0]["image_box"], (10, 6, 22, 18))

    def test_capture_retries_transient_failures_with_interruptible_backoff(self):
        engine = self._bare_engine()
        engine.scenario = Scenario(name="capture", monitor_index=1)
        logs = []
        backoffs = []
        engine.log = logs.append
        engine._sleep_until_stop = lambda seconds: backoffs.append(seconds) or False

        class FlakyCapture:
            monitors = [
                {"left": 0, "top": 0, "width": 20, "height": 10},
                {"left": 3, "top": 4, "width": 8, "height": 6},
            ]

            def __init__(self):
                self.calls = 0

            def grab(self, monitor):
                self.calls += 1
                if self.calls < 3:
                    raise OSError("temporary BitBlt failure")
                return np.zeros((monitor["height"], monitor["width"], 4), dtype=np.uint8)

        engine.sct = FlakyCapture()

        frame, left, top = engine._grab()

        self.assertEqual(frame.shape, (6, 8, 3))
        self.assertEqual((left, top), (3, 4))
        self.assertEqual(engine.sct.calls, 3)
        self.assertEqual(backoffs, [0.05, 0.1])
        self.assertEqual(len(logs), 2)

    def test_performance_messages_are_rate_limited_by_key(self):
        engine = self._bare_engine()

        self.assertTrue(engine._should_log_perf(("step", "slow"), now=100.0))
        self.assertFalse(engine._should_log_perf(("step", "slow"), now=105.0))
        self.assertTrue(engine._should_log_perf(("step", "slow"), now=110.0))
        self.assertTrue(engine._should_log_perf(("step", "other"), now=105.0))

    def test_stop_between_conditions_short_circuits_remaining_work(self):
        engine = self._bare_engine()
        calls = []

        def evaluate_condition(index, _condition, _cache, collect_all=True):
            calls.append((index, collect_all))
            engine._stop_event.set()
            return True, []

        engine._evaluate_condition = evaluate_condition
        step = Step(
            name="conditions",
            conditions=[
                ImageCondition(template_path="one.png"),
                ImageCondition(template_path="two.png"),
            ],
        )

        met, _, _ = engine._evaluate_step(step)

        self.assertFalse(met)
        self.assertEqual([index for index, _ in calls], [0])


if __name__ == "__main__":
    unittest.main()
