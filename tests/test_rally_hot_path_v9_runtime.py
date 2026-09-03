import unittest
from unittest.mock import patch

from macro_clicker import rally_hot_path_v9_runtime as v9
from macro_clicker import rally_matching as rm
from macro_clicker.engine import MacroEngine
from macro_clicker.models import load_scenario
from macro_clicker.rally_team_policy import RALLY_TEAM_BUSY, RALLY_TEAM_IDLE


class RallyHotPathV9RuntimeTests(unittest.TestCase):
    def _engine(self, scenario_name="Rally gold mob_ 3 team"):
        engine = object.__new__(MacroEngine)
        engine.scenario = load_scenario(scenario_name)
        engine.log_messages = []
        engine.log = engine.log_messages.append
        engine._rally_hot_path_three_team = scenario_name.endswith("3 team")
        engine._rally_hot_entry_latched = False
        engine._rally_hot_profile_armed = False
        engine._rally_hot_base_armed = False
        engine._rally_hot_base_not_before = 0.0
        engine._rally_join_guard_until = 0.0
        engine._pending_rally_level = None
        engine._pending_rally_team_selected = None
        engine._pending_rally_team_availability = None
        engine._rally_v8_dispatch_wait_key = None
        engine._rally_v9_expect_rally_since = None
        engine._rally_v9_base_arm_expires = 0.0
        engine._rally_v9_team_cache_valid = False
        engine._rally_v9_team_states = None
        engine._rally_v9_team_cache_captured_at = 0.0
        engine._rally_v9_last_cached_cap_log = object()
        engine._rally_v9_last_squad_count = None
        engine._rally_v9_expected_squad_count = None
        engine._rally_v9_expected_count_since = 0.0
        engine._rally_v9_last_count_poll = 0.0
        engine._reset_three_team_rally_state = lambda reason=None: None
        return engine

    @staticmethod
    def _joining_action(engine):
        step = next(step for step in engine.scenario.steps if step.name == "Joining")
        return next(action for action in step.actions if action.type == "click_matching_row")

    @staticmethod
    def _selector(engine):
        return next(
            action
            for step in engine.scenario.steps
            for action in step.actions
            if action.type == "select_rally_team"
        )

    def test_known_busy_team_reduces_rally_page_level_ceiling(self):
        engine = self._engine()
        selector = self._selector(engine)
        engine._rally_v9_team_cache_valid = True
        engine._rally_v9_team_states = {
            1: RALLY_TEAM_BUSY,
            2: RALLY_TEAM_IDLE,
            3: RALLY_TEAM_IDLE,
        }

        cap = engine._available_rally_team_level_cap(self._joining_action(engine))

        expected = max(selector.team2_max_level, selector.team3_max_level)
        self.assertEqual(cap, expected)
        self.assertLess(cap, selector.team1_max_level)
        self.assertTrue(
            any("Rally-row ceiling" in line for line in engine.log_messages)
        )

    def test_confirmed_dispatch_marks_selected_team_busy_immediately(self):
        engine = self._engine()
        engine._rally_v9_team_cache_valid = True
        engine._rally_v9_team_states = {
            1: RALLY_TEAM_IDLE,
            2: RALLY_TEAM_IDLE,
            3: RALLY_TEAM_IDLE,
        }
        engine._rally_v9_last_squad_count = 1

        v9._mark_dispatched_team_busy(engine, 1, now=10.0)

        self.assertEqual(engine._rally_v9_team_states[1], RALLY_TEAM_BUSY)
        self.assertEqual(engine._rally_v9_expected_squad_count, 2)
        self.assertEqual(v9._cached_level_cap(engine), 55)

    def test_world_map_count_change_invalidates_exact_team_identity(self):
        engine = self._engine()
        engine._rally_v9_team_cache_valid = True
        engine._rally_v9_team_states = {
            1: RALLY_TEAM_BUSY,
            2: RALLY_TEAM_BUSY,
            3: RALLY_TEAM_IDLE,
        }
        engine._rally_v9_last_squad_count = 2

        changed = v9._observe_squad_count(engine, 1, now=20.0)

        self.assertTrue(changed)
        self.assertFalse(engine._rally_v9_team_cache_valid)
        self.assertIsNone(engine._rally_v9_team_states)
        self.assertTrue(any("2/3 -> 1/3" in line for line in engine.log_messages))

    def test_expected_dispatch_count_increment_preserves_cache(self):
        engine = self._engine()
        engine._rally_v9_team_cache_valid = True
        engine._rally_v9_team_states = {
            1: RALLY_TEAM_IDLE,
            2: RALLY_TEAM_IDLE,
            3: RALLY_TEAM_IDLE,
        }
        engine._rally_v9_last_squad_count = 1
        v9._mark_dispatched_team_busy(engine, 1, now=30.0)

        changed = v9._observe_squad_count(engine, 2, now=30.2)

        self.assertFalse(changed)
        self.assertTrue(engine._rally_v9_team_cache_valid)
        self.assertEqual(engine._rally_v9_last_squad_count, 2)
        self.assertIsNone(engine._rally_v9_expected_squad_count)

    def test_rally_icon_reappearing_fast_releases_deadloop_without_click(self):
        engine = self._engine()
        engine._rally_hot_entry_latched = True
        engine._rally_v9_expect_rally_since = 40.0
        engine._pending_rally_level = 55
        engine._click_point = lambda *_args, **_kwargs: self.fail("blind click sent")
        for step in engine.scenario.steps:
            if step.name in {
                "Joining",
                "Attack Confirm",
                "Back if wrong mob",
                "Back if no slot",
            }:
                step.enabled = True

        def visible(_engine, basenames):
            return "RallyIcon.png" in basenames

        with patch.object(v9, "_condition_visible", side_effect=visible):
            recovered = v9._watch_entry_progress(engine, now=40.6)

        self.assertTrue(recovered)
        self.assertFalse(engine._rally_hot_entry_latched)
        self.assertIsNone(engine._pending_rally_level)
        self.assertFalse(engine._rally_hot_base_armed)
        self.assertTrue(
            all(
                not step.enabled
                for step in engine.scenario.steps
                if step.name
                in {"Joining", "Attack Confirm", "Back if wrong mob", "Back if no slot"}
            )
        )
        self.assertTrue(any("no blind click" in line for line in engine.log_messages))

    def test_deadloop_timeout_clears_latch_without_unrecognized_click(self):
        engine = self._engine()
        engine._rally_hot_entry_latched = True
        engine._rally_v9_expect_rally_since = 50.0
        engine._click_point = lambda *_args, **_kwargs: self.fail("blind click sent")

        with patch.object(v9, "_condition_visible", return_value=False):
            recovered = v9._watch_entry_progress(engine, now=53.0)

        self.assertTrue(recovered)
        self.assertFalse(engine._rally_hot_entry_latched)
        self.assertTrue(engine._rally_hot_base_armed)
        self.assertGreater(engine._rally_v9_base_arm_expires, 0.0)
        self.assertTrue(any("2.50" in line or "3.00" in line for line in engine.log_messages))

    def test_two_team_level_cap_still_delegates_to_existing_runtime(self):
        engine = self._engine("Rally gold mob_ 2 team")
        action = self._joining_action(engine)
        sentinel = object()

        with patch.object(
            v9,
            "_ORIGINAL_AVAILABLE_LEVEL_CAP",
            new=lambda *_args, **_kwargs: sentinel,
        ):
            result = engine._available_rally_team_level_cap(action)

        self.assertIs(result, sentinel)
        self.assertIs(v9._cached_level_cap(engine), rm._TEAM_LEVEL_CAP_UNSET)


if __name__ == "__main__":
    unittest.main()
