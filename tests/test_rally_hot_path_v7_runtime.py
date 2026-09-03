import unittest
from unittest.mock import patch

import cv2
import numpy as np

from macro_clicker import rally_hot_path_v7_runtime as v7
from macro_clicker.engine import MacroEngine
from macro_clicker.models import Action, Scenario, Step, load_scenario, project_path
from macro_clicker.rally_team_policy import RALLY_TEAM_BUSY


class _NeverStop:
    def is_set(self):
        return False


class RallyHotPathV7RuntimeTests(unittest.TestCase):
    def _engine(self):
        engine = object.__new__(MacroEngine)
        engine.scenario = load_scenario("Rally gold mob_ 3 team")
        engine.log_messages = []
        engine.log = engine.log_messages.append
        engine._rally_hot_path_three_team = True
        engine._rally_hot_entry_latched = False
        engine._rally_hot_base_armed = False
        engine._rally_hot_profile_armed = False
        engine._rally_hot_base_not_before = 0.0
        engine._pending_rally_level = None
        engine._pending_rally_team_selected = None
        engine._abort_current_step = False
        return engine

    @staticmethod
    def _template(path):
        image = cv2.imread(project_path(path))
        if image is None:
            raise AssertionError(f"template could not be loaded: {path}")
        return image

    def test_entry_click_latches_until_workflow_exit(self):
        engine = self._engine()
        step = Step(name="Enter Rally after team probe")
        action = Action(type="click")

        original = v7._ORIGINAL_RUN_ACTION
        try:
            v7._ORIGINAL_RUN_ACTION = lambda *_args, **_kwargs: True
            self.assertTrue(MacroEngine._run_action(engine, step, action, {}, {}))
        finally:
            v7._ORIGINAL_RUN_ACTION = original

        self.assertTrue(engine._rally_hot_entry_latched)
        self.assertTrue(
            any("entry latched" in message for message in engine.log_messages)
        )

    def test_latched_entry_is_not_re_evaluated_inside_rally_workflow(self):
        engine = self._engine()
        engine._rally_hot_entry_latched = True
        step = Step(name="Enter Rally after team probe")
        calls = []

        original = v7._ORIGINAL_EVALUATE_STEP
        try:
            v7._ORIGINAL_EVALUATE_STEP = (
                lambda *_args, **_kwargs: calls.append(True) or (True, {}, {})
            )
            result = MacroEngine._evaluate_step(engine, step)
        finally:
            v7._ORIGINAL_EVALUATE_STEP = original

        self.assertEqual(result, (False, {}, {}))
        self.assertEqual(calls, [])

    def test_back_recovery_releases_entry_latch(self):
        engine = self._engine()
        engine._rally_hot_entry_latched = True
        step = Step(name="Back if no slot")
        action = Action(type="click")

        original = v7._ORIGINAL_RUN_ACTION
        try:
            v7._ORIGINAL_RUN_ACTION = lambda *_args, **_kwargs: True
            self.assertTrue(MacroEngine._run_action(engine, step, action, {}, {}))
        finally:
            v7._ORIGINAL_RUN_ACTION = original

        self.assertFalse(engine._rally_hot_entry_latched)

    def test_broad_three_of_three_search_accepts_shifted_counter(self):
        engine = object.__new__(MacroEngine)
        engine._stop_event = _NeverStop()
        engine._prepared_template_cache = {}
        engine.low_variance_threshold = 1.0
        engine.max_matches_per_scale = 128
        engine.max_multiscale_candidates = 512
        engine.scenario = Scenario(name="broad 3/3 test")

        template = self._template("templates/FullSquad3_3.png")
        broad_x, broad_y, broad_w, broad_h = v7.FULL_SQUAD_BROAD_REGION
        frame = np.zeros((broad_h, broad_w, 3), dtype=np.uint8)
        local_x = 205
        local_y = 165
        th, tw = template.shape[:2]
        frame[local_y : local_y + th, local_x : local_x + tw] = template

        engine._get_target_window_rect = lambda: (0, 0, 1920, 1080)
        engine._grab = lambda region: (frame.copy(), region[0], region[1])
        engine._load_template = lambda _path: template

        self.assertTrue(v7._broad_three_of_three(engine))
        absolute_x = broad_x + local_x
        absolute_y = broad_y + local_y
        self.assertFalse(
            154 <= absolute_x < 154 + 51 and 205 <= absolute_y < 205 + 28
        )

    def test_all_busy_fixed_cards_remain_busy_when_formation_anchor_is_absent(self):
        frame = cv2.imread(
            project_path("tests/Test Picture/Screenshot 2026-09-02 212029.png")
        )
        self.assertIsNotNone(frame)
        left, top, width, height = v7.LIVE_FIXED_TEAM_SCREEN_ANCHOR_REGION
        frame[top : top + height, left : left + width] = 0

        result = v7.detect_full_squad_tray(
            frame,
            self._template(v7.TRAY_ANCHOR_TEMPLATE),
            self._template(v7.FORMATION_ANCHOR_TEMPLATE),
            self._template("templates/TeamIdleZZ.png"),
        )

        self.assertTrue(result["tray_valid"])
        self.assertFalse(result["formation_visible"])
        self.assertEqual(
            result["states"],
            {1: RALLY_TEAM_BUSY, 2: RALLY_TEAM_BUSY, 3: RALLY_TEAM_BUSY},
        )

    def test_normal_formation_fixture_is_not_treated_as_tray_recovery(self):
        frame = cv2.imread(
            project_path("tests/Test Picture/Screenshot 2026-09-02 204523.png")
        )
        self.assertIsNotNone(frame)

        result = v7.detect_full_squad_tray(
            frame,
            self._template(v7.TRAY_ANCHOR_TEMPLATE),
            self._template(v7.FORMATION_ANCHOR_TEMPLATE),
            self._template("templates/TeamIdleZZ.png"),
        )

        self.assertTrue(result["tray_valid"])
        self.assertTrue(result["formation_visible"])

    def test_cycle_stops_after_tray_recovery_instead_of_reentering_same_cycle(self):
        engine = self._engine()
        calls = []
        original = v7._ORIGINAL_CYCLE
        try:
            v7._ORIGINAL_CYCLE = lambda _self: calls.append("original") or False
            with patch.object(v7, "_recover_all_busy_tray", return_value=True):
                self.assertTrue(MacroEngine._cycle(engine))
        finally:
            v7._ORIGINAL_CYCLE = original

        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
