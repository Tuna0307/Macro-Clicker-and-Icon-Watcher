import copy
import hashlib
import unittest
from pathlib import Path

from macro_clicker.engine import MacroEngine
from macro_clicker.models import Action, load_scenario, project_path, validate_scenario
from macro_clicker.rally_team_policy import (
    RALLY_TEAM_BUSY,
    RALLY_TEAM_IDLE,
    RALLY_TEAM_UNKNOWN,
)


class RallyThreeTeamWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.scenario = load_scenario("Rally gold mob_ 3 team")
        self.selector = next(
            action
            for step in self.scenario.steps
            for action in step.actions
            if action.type == "select_rally_team"
        )
        self.probe_action = next(
            action
            for step in self.scenario.steps
            for action in step.actions
            if action.type == "capture_rally_team_status"
            and action.rally_team_status_mode == "capture"
        )
        self.entry_step = next(
            step
            for step in self.scenario.steps
            if step.name == "Enter Rally after team probe"
        )
        self.joining_step = next(
            step for step in self.scenario.steps if step.name == "Joining"
        )

    @staticmethod
    def _result(states, *, valid=True):
        return {
            "screen_valid": valid,
            "error": None if valid else "screen_anchor_not_found",
            "states": dict(states),
            "capture_region": (0, 0, 1920, 1080),
            "anchor_region": (900, 480, 130, 145),
            "frame_size": (1920, 1080),
        }

    def _engine(self, result):
        engine = object.__new__(MacroEngine)
        engine.scenario = copy.deepcopy(self.scenario)
        engine.log = lambda _message: None
        engine._pending_rally_level = None
        engine._pending_rally_team_selected = None
        engine._pending_rally_team_availability = None
        engine._last_rally_team_availability = {}
        engine._three_team_rally_snapshot = None
        engine._three_team_probe_generation = 0
        engine._abort_current_step = False
        engine._cleanup_after_abort = False
        engine._retry_current_step = False
        engine._capture_fixed_rally_team_status = lambda: copy.deepcopy(result)
        clicks = []
        engine._click_point = lambda x, y, button: clicks.append((x, y, button)) or True
        return engine, clicks

    @staticmethod
    def _context(anchor=(962, 808), scale_x=1.0, scale_y=1.0):
        return (
            {0: anchor},
            {
                0: [
                    {
                        "center": anchor,
                        "scale_x": scale_x,
                        "scale_y": scale_y,
                    }
                ]
            },
        )

    def _final_select(self, level, states, *, limits=(65, 60, 45), **context):
        engine, clicks = self._engine(self._result(states))
        engine._pending_rally_level = level
        selector = copy.deepcopy(self.selector)
        selector.rally_team_dry_run = False
        selector.team1_max_level, selector.team2_max_level, selector.team3_max_level = (
            limits
        )
        points, matches = self._context(**context)
        result = engine._run_select_rally_team_action(selector, points, matches)
        return engine, clicks, result

    def test_scenario_enters_rally_first_and_live_dispatch_is_enabled(self):
        validate_scenario(self.scenario, require_files=True)
        self.assertFalse(self.scenario.require_target_foreground)
        self.assertEqual(self.selector.team_priority, [3, 2, 1])
        configured_limits = (
            self.selector.team1_max_level,
            self.selector.team2_max_level,
            self.selector.team3_max_level,
        )
        self.assertTrue(all(limit is not None for limit in configured_limits))
        self.assertTrue(all(limit >= 0 for limit in configured_limits))
        self.assertFalse(self.selector.rally_team_dry_run)

        self.assertTrue(self.entry_step.enabled)
        self.assertEqual(len(self.entry_step.conditions), 1)
        self.assertEqual(
            self.entry_step.conditions[0].template_path,
            "templates/RallyIcon.png",
        )
        self.assertFalse(
            any(
                condition.template_path == "templates/AddSquad.png"
                for condition in self.entry_step.conditions
            )
        )

        probe_step = next(
            step
            for step in self.scenario.steps
            if step.name == "Probe fixed three-team status"
        )
        self.assertFalse(probe_step.enabled)

        row_action = next(
            action
            for action in self.joining_step.actions
            if action.type == "click_matching_row"
        )
        self.assertEqual(row_action.min_level, 0)
        self.assertIsNone(row_action.max_level)
        self.assertIsNone(row_action.team_status_region)
        self.assertFalse(row_action.team1_busy_template_path)
        self.assertFalse(row_action.team3_busy_template_path)

        for step_name in ("Back if wrong mob", "Back if no slot"):
            step = next(step for step in self.scenario.steps if step.name == step_name)
            self.assertTrue(
                any(
                    action.type == "capture_rally_team_status"
                    and action.rally_team_status_mode == "clear"
                    for action in step.actions
                )
            )

    def test_new_action_fields_round_trip(self):
        restored = Action.from_dict(self.selector.to_dict())
        self.assertEqual(restored.team2_click_offset, [-63, 168])
        self.assertEqual(restored.team_priority, [3, 2, 1])
        self.assertFalse(restored.rally_team_dry_run)
        restored_probe = Action.from_dict(self.probe_action.to_dict())
        self.assertEqual(restored_probe.rally_team_status_mode, "capture")
        self.assertEqual(restored_probe.rally_team_snapshot_ttl, 3.0)

    def test_stop_or_f12_request_clears_transient_snapshot(self):
        engine, _clicks = self._engine(self._result({}))
        engine._thread = None
        engine._hotkey_handle = None
        engine._ever_started = True
        engine._ready_event = None
        engine._remove_hotkey = lambda: None
        engine._stop_event = type("Stop", (), {"set": lambda self: None})()
        engine._three_team_rally_snapshot = {"generation": 1}
        engine._pending_rally_team_availability = {"level_cap": 65}

        self.assertTrue(engine.request_stop())
        self.assertIsNone(engine._three_team_rally_snapshot)
        self.assertIsNone(engine._pending_rally_team_availability)

    def test_legacy_scenario_bytes_and_effective_path_are_unchanged(self):
        raw = Path(project_path("scenarios/Rally gold mob_ 2 team.json")).read_bytes()
        self.assertEqual(
            hashlib.sha256(raw).hexdigest(),
            "5e8cbdcc1bf5705cfecd9368b9518beadb7a6ec0db4cd97e8df14b2df3328684",
        )
        legacy = load_scenario("Rally gold mob_ 2 team")
        selector = next(
            action
            for step in legacy.steps
            for action in step.actions
            if action.type == "select_rally_team"
        )
        self.assertIsNone(selector.team_priority)
        self.assertIsNone(selector.team2_click_offset)
        self.assertEqual((selector.team1_max_level, selector.team3_max_level), (70, 60))
        self.assertFalse(
            any(
                action.type == "capture_rally_team_status"
                for step in legacy.steps
                for action in step.actions
            )
        )

    def test_entry_no_longer_requires_preentry_fixed_team_snapshot(self):
        engine, _clicks = self._engine(self._result({}))
        self.assertIsNone(engine._three_team_rally_snapshot)
        self.assertTrue(engine._prepare_rally_team_availability_for_entry(self.entry_step))
        self.assertIsNone(engine._pending_rally_team_availability)

    def test_isolated_probe_still_fails_closed_for_all_busy_or_unknown(self):
        all_busy = {1: RALLY_TEAM_BUSY, 2: RALLY_TEAM_BUSY, 3: RALLY_TEAM_BUSY}
        engine, _clicks = self._engine(self._result(all_busy))
        engine._run_capture_rally_team_status_action(self.probe_action)
        self.assertIsNone(engine._three_team_rally_snapshot)

        unknown = {1: RALLY_TEAM_IDLE, 2: RALLY_TEAM_UNKNOWN, 3: RALLY_TEAM_IDLE}
        engine, _clicks = self._engine(self._result(unknown))
        engine._run_capture_rally_team_status_action(self.probe_action)
        self.assertIsNone(engine._three_team_rally_snapshot)

    def test_standard_limits_choose_team3_team2_then_team1(self):
        idle = {1: RALLY_TEAM_IDLE, 2: RALLY_TEAM_IDLE, 3: RALLY_TEAM_IDLE}
        for level, expected_click, expected_team in (
            (40, (1025, 976, "left"), 3),
            (55, (899, 976, "left"), 2),
            (65, (773, 976, "left"), 1),
        ):
            with self.subTest(level=level):
                engine, clicks, result = self._final_select(level, idle)
                self.assertTrue(result)
                self.assertEqual(clicks, [expected_click])
                self.assertEqual(
                    engine._pending_rally_team_selected["team"], expected_team
                )

    def test_final_state_changes_are_recomputed_not_carried(self):
        final_t3_busy = {
            1: RALLY_TEAM_IDLE,
            2: RALLY_TEAM_IDLE,
            3: RALLY_TEAM_BUSY,
        }
        engine, clicks, _result = self._final_select(40, final_t3_busy)
        self.assertEqual(clicks, [(899, 976, "left")])
        self.assertEqual(engine._pending_rally_team_selected["team"], 2)

        final_t2_returned = {
            1: RALLY_TEAM_IDLE,
            2: RALLY_TEAM_IDLE,
            3: RALLY_TEAM_BUSY,
        }
        engine, clicks = self._engine(self._result(final_t2_returned))
        engine._pending_rally_level = 55
        engine._pending_rally_team_availability = {
            "states": {1: RALLY_TEAM_IDLE, 2: RALLY_TEAM_BUSY, 3: RALLY_TEAM_IDLE}
        }
        selector = copy.deepcopy(self.selector)
        selector.rally_team_dry_run = False
        selector.team2_max_level = 60
        points, matches = self._context()
        engine._run_select_rally_team_action(selector, points, matches)
        self.assertEqual(clicks, [(899, 976, "left")])

    def test_unknown_or_all_busy_final_state_never_clicks_a_team_card(self):
        for states in (
            {1: RALLY_TEAM_IDLE, 2: RALLY_TEAM_UNKNOWN, 3: RALLY_TEAM_IDLE},
            {1: RALLY_TEAM_BUSY, 2: RALLY_TEAM_BUSY, 3: RALLY_TEAM_BUSY},
        ):
            with self.subTest(states=states):
                engine, clicks, result = self._final_select(40, states)
                self.assertTrue(result)
                self.assertEqual(clicks, [(965, 152, "left")])
                self.assertIsNone(engine._pending_rally_team_selected)
                self.assertTrue(engine._abort_current_step)

    def test_dry_run_logs_selection_without_clicking_team_or_attack(self):
        idle = {1: RALLY_TEAM_IDLE, 2: RALLY_TEAM_IDLE, 3: RALLY_TEAM_IDLE}
        engine, clicks = self._engine(self._result(idle))
        engine._pending_rally_level = 40
        selector = copy.deepcopy(self.selector)
        selector.rally_team_dry_run = True
        points, matches = self._context()
        engine._run_select_rally_team_action(selector, points, matches)
        self.assertEqual(clicks, [(965, 152, "left")])
        self.assertTrue(engine._abort_current_step)
        self.assertTrue(engine._cleanup_after_abort)

    def test_fixed_card_geometry_scales_and_supports_negative_desktop_origin(self):
        idle = {1: RALLY_TEAM_IDLE, 2: RALLY_TEAM_IDLE, 3: RALLY_TEAM_IDLE}
        engine, clicks, _result = self._final_select(
            55,
            idle,
            anchor=(-1198, 606),
            scale_x=0.75,
            scale_y=0.75,
        )
        self.assertEqual(clicks, [(-1245, 732, "left")])
        self.assertEqual(engine._pending_rally_team_selected["team"], 2)


if __name__ == "__main__":
    unittest.main()
