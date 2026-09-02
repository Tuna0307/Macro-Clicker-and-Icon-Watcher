import time
import unittest
from unittest.mock import patch

import numpy as np

from macro_clicker.engine import MacroEngine
from macro_clicker.models import Action, ImageCondition, Step, load_scenario
from macro_clicker import rally_hot_path_runtime as hot


class RallyHotPathRuntimeTests(unittest.TestCase):
    def _engine(self):
        engine = object.__new__(MacroEngine)
        engine.scenario = load_scenario("Rally gold mob_ 3 team")
        engine.log = lambda _message: None
        engine._rally_hot_path_three_team = True
        engine._rally_hot_base_armed = False
        engine._rally_hot_profile_armed = False
        engine._rally_hot_base_not_before = 0.0
        engine._pending_rally_level = None
        engine._pending_rally_team_selected = None
        return engine

    def test_start_switches_only_three_team_runtime_to_fast_poll(self):
        engine = self._engine()
        engine.scenario.poll_interval = 0.25
        original = hot._ORIGINAL_START
        try:
            hot._ORIGINAL_START = lambda _self: "started"
            result = MacroEngine.start(engine)
        finally:
            hot._ORIGINAL_START = original

        self.assertEqual(result, "started")
        self.assertEqual(engine.scenario.poll_interval, 0.05)
        self.assertEqual(engine.RALLY_DIAGNOSTIC_BUILD, "JOIN-HOT-RACE-v6")
        self.assertFalse(engine._rally_hot_base_armed)
        self.assertFalse(engine._rally_hot_profile_armed)

    def test_misclick_base_keeps_full_screen_step_but_is_not_polled_unarmed(self):
        engine = self._engine()
        base = next(
            step for step in engine.scenario.steps if step.name == "MisClick Base"
        )
        self.assertIsNone(base.conditions[0].region)
        self.assertEqual(base.conditions[0].region_mode, "screen")

        calls = []
        original = hot._ORIGINAL_EVALUATE_STEP
        try:
            hot._ORIGINAL_EVALUATE_STEP = (
                lambda _self, _step, frame_cache=None: calls.append(_step.name)
                or (True, {}, {})
            )
            self.assertEqual(MacroEngine._evaluate_step(engine, base), (False, {}, {}))
            self.assertEqual(calls, [])

            engine._rally_hot_base_armed = True
            engine._rally_hot_base_not_before = time.monotonic() + 1.0
            self.assertEqual(MacroEngine._evaluate_step(engine, base), (False, {}, {}))
            self.assertEqual(calls, [])

            engine._rally_hot_base_not_before = 0.0
            self.assertEqual(MacroEngine._evaluate_step(engine, base), (True, {}, {}))
            self.assertEqual(calls, ["MisClick Base"])
        finally:
            hot._ORIGINAL_EVALUATE_STEP = original

    def test_profile_detector_is_event_gated(self):
        engine = self._engine()
        profile = next(
            step for step in engine.scenario.steps if step.name == "MisClick Profile"
        )
        calls = []
        original = hot._ORIGINAL_EVALUATE_STEP
        try:
            hot._ORIGINAL_EVALUATE_STEP = (
                lambda _self, _step, frame_cache=None: calls.append(_step.name)
                or (True, {}, {})
            )
            self.assertEqual(
                MacroEngine._evaluate_step(engine, profile),
                (False, {}, {}),
            )
            self.assertEqual(calls, [])
            engine._rally_hot_profile_armed = True
            self.assertEqual(
                MacroEngine._evaluate_step(engine, profile),
                (True, {}, {}),
            )
            self.assertEqual(calls, ["MisClick Profile"])
        finally:
            hot._ORIGINAL_EVALUATE_STEP = original

    def test_positive_3_of_3_gate_blocks_entry_but_failure_is_fail_open(self):
        engine = self._engine()
        entry = next(
            step
            for step in engine.scenario.steps
            if step.name == "Enter Rally after team probe"
        )
        original = hot._ORIGINAL_EVALUATE_STEP
        try:
            hot._ORIGINAL_EVALUATE_STEP = (
                lambda *_args, **_kwargs: (True, {0: (1, 1)}, {})
            )
            with patch.object(hot, "_all_three_squads_out", return_value=True):
                self.assertEqual(
                    MacroEngine._evaluate_step(engine, entry),
                    (False, {}, {}),
                )
            with patch.object(hot, "_all_three_squads_out", return_value=False):
                self.assertTrue(MacroEngine._evaluate_step(engine, entry)[0])
        finally:
            hot._ORIGINAL_EVALUATE_STEP = original

    @staticmethod
    def _join_context():
        condition = ImageCondition(
            template_path="templates/Join.png",
            confidence=0.85,
            region=[900, 146, 148, 794],
            region_mode="window",
            region_ratio=[
                0.46875,
                0.13518518518518519,
                0.07708333333333334,
                0.7351851851851852,
            ],
            region_window_size=[1920, 1080],
        )
        step = Step(
            name="Joining",
            conditions=[ImageCondition(template_path="mob.png"), condition],
        )
        action = Action(
            type="click_matching_row",
            match_condition_index=0,
            on_condition_index=1,
        )
        return step, action

    def _join_engine(self, matches):
        engine = self._engine()
        engine._resolve_capture_region = lambda _condition: (900, 146, 148, 794)
        engine._get_target_window_rect = lambda: (0, 0, 1920, 1080)
        engine._load_template = lambda _path: np.zeros((12, 12, 3), dtype=np.uint8)
        captures = []

        def grab(region):
            captures.append(region)
            return (
                np.zeros((region[3], region[2], 3), dtype=np.uint8),
                region[0],
                region[1],
            )

        engine._grab = grab
        engine._condition_matching_kwargs = lambda _condition: {}
        engine._find_template_matches_in_frame = (
            lambda *_args, **_kwargs: list(matches)
        )
        return engine, captures

    def test_join_micro_revalidation_uses_tiny_fresh_capture(self):
        step, action = self._join_context()
        engine, captures = self._join_engine([(18, 18, 12, 12, 0.99, 1.0)])

        result = hot.revalidate_join_target(engine, step, action, 950, 300)

        self.assertTrue(result["applies"])
        self.assertTrue(result["valid"])
        self.assertEqual(result["score"], 0.99)
        self.assertEqual(len(captures), 1)
        self.assertLess(captures[0][2], 100)
        self.assertLess(captures[0][3], 100)

    def test_join_micro_revalidation_cancels_stale_plus(self):
        step, action = self._join_context()
        engine, captures = self._join_engine([])

        result = hot.revalidate_join_target(engine, step, action, 950, 300)

        self.assertTrue(result["applies"])
        self.assertFalse(result["valid"])
        self.assertEqual(result["reason"], "join_disappeared")
        self.assertEqual(len(captures), 1)

    def test_join_micro_revalidation_leaves_back_fallback_untouched(self):
        step, action = self._join_context()
        engine, captures = self._join_engine([])

        result = hot.revalidate_join_target(engine, step, action, 700, 1000)

        self.assertFalse(result["applies"])
        self.assertTrue(result["valid"])
        self.assertEqual(captures, [])


if __name__ == "__main__":
    unittest.main()
