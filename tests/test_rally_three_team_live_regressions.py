import unittest
from pathlib import Path

import cv2

from macro_clicker.engine import MacroEngine
from macro_clicker.models import load_scenario, project_path
from macro_clicker.rally_matching import (
    RALLY_FIXED_TEAM_IDLE_TEMPLATE,
    RALLY_FIXED_TEAM_SCREEN_ANCHOR_TEMPLATE,
)
from macro_clicker.rally_team_policy import (
    RALLY_TEAM_BUSY,
    RALLY_TEAM_IDLE,
    RALLY_TEAM_UNKNOWN,
)
from macro_clicker.rally_three_team_runtime import (
    LIVE_FIXED_TEAM_SCREEN_ANCHOR_REGION,
    detect_live_fixed_rally_team_status,
)


class RallyThreeTeamLiveRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.anchor_template = cv2.imread(
            project_path(RALLY_FIXED_TEAM_SCREEN_ANCHOR_TEMPLATE),
            cv2.IMREAD_COLOR,
        )
        cls.idle_template = cv2.imread(
            project_path(RALLY_FIXED_TEAM_IDLE_TEMPLATE),
            cv2.IMREAD_COLOR,
        )
        if cls.anchor_template is None or cls.idle_template is None:
            raise AssertionError("three-team detector templates are unavailable")

    @staticmethod
    def _fixture(name):
        path = Path(project_path("tests/Test Picture")) / name
        frame = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if frame is None:
            raise AssertionError(f"could not load live screenshot fixture: {path}")
        return frame

    def _detect(self, name):
        return detect_live_fixed_rally_team_status(
            self._fixture(name),
            self.anchor_template,
            self.idle_template,
        )

    def test_live_anchor_region_covers_lower_attack_panel_variants(self):
        self.assertEqual(LIVE_FIXED_TEAM_SCREEN_ANCHOR_REGION, (740, 448, 466, 299))

        cases = {
            "Screenshot 2026-09-02 204523.png": {
                1: RALLY_TEAM_IDLE,
                2: RALLY_TEAM_IDLE,
                3: RALLY_TEAM_IDLE,
            },
            "Screenshot 2026-09-02 203451.png": {
                1: RALLY_TEAM_IDLE,
                2: RALLY_TEAM_BUSY,
                3: RALLY_TEAM_IDLE,
            },
            "Screenshot 2026-09-02 211656.png": {
                1: RALLY_TEAM_BUSY,
                2: RALLY_TEAM_IDLE,
                3: RALLY_TEAM_IDLE,
            },
            "Screenshot 2026-09-02 211753.png": {
                1: RALLY_TEAM_BUSY,
                2: RALLY_TEAM_IDLE,
                3: RALLY_TEAM_BUSY,
            },
            "Screenshot 2026-09-02 211946.png": {
                1: RALLY_TEAM_IDLE,
                2: RALLY_TEAM_BUSY,
                3: RALLY_TEAM_BUSY,
            },
            "Screenshot 2026-09-02 212029.png": {
                1: RALLY_TEAM_BUSY,
                2: RALLY_TEAM_BUSY,
                3: RALLY_TEAM_BUSY,
            },
            "Screenshot 2026-09-02 212218.png": {
                1: RALLY_TEAM_IDLE,
                2: RALLY_TEAM_IDLE,
                3: RALLY_TEAM_BUSY,
            },
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                result = self._detect(name)
                self.assertTrue(result["screen_valid"], result)
                self.assertEqual(result["states"], expected)

    def test_world_map_only_fixture_remains_fail_closed(self):
        result = self._detect("Screenshot 2026-09-02 211912.png")

        self.assertFalse(result["screen_valid"])
        self.assertEqual(result["error"], "screen_anchor_not_found")
        self.assertEqual(
            result["states"],
            {1: RALLY_TEAM_UNKNOWN, 2: RALLY_TEAM_UNKNOWN, 3: RALLY_TEAM_UNKNOWN},
        )

    def test_joining_uses_highest_configured_three_team_limit_as_ceiling(self):
        scenario = load_scenario("Rally gold mob_ 3 team")
        selector = next(
            action
            for step in scenario.steps
            for action in step.actions
            if action.type == "select_rally_team"
        )
        configured_limits = (
            selector.team1_max_level,
            selector.team2_max_level,
            selector.team3_max_level,
        )
        self.assertTrue(all(limit is not None for limit in configured_limits))
        expected_cap = max(limit for limit in configured_limits if limit is not None)

        row_action = next(
            action
            for step in scenario.steps
            if step.name == "Joining"
            for action in step.actions
            if action.type == "click_matching_row"
        )

        engine = object.__new__(MacroEngine)
        engine.scenario = scenario
        engine.log = lambda _message: None
        engine._pending_rally_team_availability = None
        cap = engine._available_rally_team_level_cap(row_action)

        self.assertEqual(cap, expected_cap)

        engine._stop_requested = lambda: False
        engine._read_level_for_row = lambda _action, _reference: expected_cap + 1
        status, level = engine._row_level_status(
            row_action,
            {"center": (0, 0)},
            max_level_override=cap,
        )
        self.assertEqual((status, level), ("ineligible", expected_cap + 1))

        engine._read_level_for_row = lambda _action, _reference: expected_cap
        status, level = engine._row_level_status(
            row_action,
            {"center": (0, 0)},
            max_level_override=cap,
        )
        self.assertEqual((status, level), ("eligible", expected_cap))

    def test_over_available_team_limit_selects_no_plus_and_uses_back_fallback(self):
        scenario = load_scenario("Rally gold mob_ 3 team")
        joining_step = next(step for step in scenario.steps if step.name == "Joining")
        row_action = next(
            action
            for action in joining_step.actions
            if action.type == "click_matching_row"
        )
        selector = next(
            action
            for step in scenario.steps
            for action in step.actions
            if action.type == "select_rally_team"
        )
        remaining_cap = max(selector.team2_max_level, selector.team3_max_level)
        rejected_level = remaining_cap + 10

        engine = object.__new__(MacroEngine)
        engine.scenario = scenario
        engine.log_messages = []
        engine.log = engine.log_messages.append
        engine._pending_rally_team_availability = None
        engine._rally_v9_team_cache_valid = True
        engine._rally_v9_team_states = {
            1: RALLY_TEAM_BUSY,
            2: RALLY_TEAM_IDLE,
            3: RALLY_TEAM_IDLE,
        }
        engine._rally_v9_last_cached_cap_log = object()
        engine._begin_level_diagnostic_generation = lambda: None
        engine._stop_requested = lambda: False
        engine._read_level_for_row = lambda _action, _reference: rejected_level

        reference = {"center": (100, 100), "scale_x": 1.0, "scale_y": 1.0}
        join_plus = {"center": (200, 100), "scale_x": 1.0, "scale_y": 1.0}
        matches = {
            row_action.match_condition_index: [reference],
            row_action.on_condition_index: [join_plus],
        }

        selections, had_unreadable_level = engine._find_matching_row_selections(
            row_action,
            matches,
            apply_level_filter=True,
        )

        self.assertEqual(selections, [])
        self.assertFalse(had_unreadable_level)
        self.assertTrue(
            any(
                f"> available-team max {remaining_cap}" in line
                for line in engine.log_messages
            )
        )
        self.assertEqual(row_action.no_match_condition_index, 2)
        self.assertEqual(
            joining_step.conditions[row_action.no_match_condition_index].template_path,
            "templates/BackButton.png",
        )
        self.assertIn("Joining", row_action.no_match_disable_steps)
        self.assertIn("Attack Confirm", row_action.no_match_disable_steps)


if __name__ == "__main__":
    unittest.main()
