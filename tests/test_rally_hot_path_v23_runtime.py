import unittest
from types import SimpleNamespace
from unittest.mock import patch

from macro_clicker import rally_hot_path_v23_runtime as v23
from macro_clicker.rally_team_policy import RALLY_TEAM_BUSY, RALLY_TEAM_IDLE


class RallyHotPathV23RuntimeTests(unittest.TestCase):
    @staticmethod
    def _engine(**overrides):
        values = {
            "_rally_hot_path_three_team": True,
            "_rally_v9_team_cache_valid": True,
            "_rally_v9_team_states": {
                1: RALLY_TEAM_BUSY,
                2: RALLY_TEAM_IDLE,
                3: RALLY_TEAM_IDLE,
            },
            "_rally_v9_last_squad_count": 0,
            "_rally_v9_expected_squad_count": None,
            "_rally_v9_expected_count_since": 0.0,
            "_rally_v12_pending_squad_count": None,
            "_rally_v12_pending_squad_since": 0.0,
            "_rally_v12_last_expected_lag_log": None,
            "_rally_v14_last_backtrack_log": None,
            "_rally_v23_entry_not_before": 0.0,
            "_retry_current_step": False,
            "log_messages": [],
        }
        values.update(overrides)
        engine = SimpleNamespace(**values)
        engine.log = engine.log_messages.append
        return engine

    def test_dispatch_expectation_uses_exact_busy_cardinality_not_old_sidebar_plus_one(self):
        engine = self._engine(
            _rally_v9_team_states={
                1: RALLY_TEAM_BUSY,
                2: RALLY_TEAM_IDLE,
                3: RALLY_TEAM_BUSY,
            },
            _rally_v9_last_squad_count=0,
        )

        def old_mark(target, team_number, now=None):
            states = dict(target._rally_v9_team_states)
            states[team_number] = RALLY_TEAM_BUSY
            target._rally_v9_team_states = states
            target._rally_v9_expected_squad_count = 1
            target._rally_v9_expected_count_since = float(now)
            return None

        with patch.object(v23, "_ORIGINAL_MARK_DISPATCHED_TEAM_BUSY", new=old_mark):
            v23._mark_dispatched_team_busy(engine, 2, now=50.0)

        self.assertEqual(engine._rally_v9_expected_squad_count, 3)
        self.assertEqual(engine._rally_v9_expected_count_since, 50.0)
        self.assertTrue(
            any(
                "dispatch expectation aligned to exact BUSY count: 1/3 -> 3/3"
                in line
                for line in engine.log_messages
            )
        )

    def test_world_count_equal_to_exact_busy_count_corroborates_cache(self):
        engine = self._engine(
            _rally_v9_last_squad_count=0,
            _rally_v9_expected_squad_count=None,
        )

        with patch.object(
            v23,
            "_ORIGINAL_OBSERVE_SQUAD_COUNT",
            side_effect=AssertionError("corroborating count must not invalidate/delegate"),
        ):
            result = v23._observe_squad_count(engine, 1, now=20.0)

        self.assertFalse(result)
        self.assertTrue(engine._rally_v9_team_cache_valid)
        self.assertEqual(engine._rally_v9_last_squad_count, 1)
        self.assertIsNone(engine._rally_v9_expected_squad_count)
        self.assertTrue(
            any(
                "corroborates exact fixed-Team BUSY count 1/3" in line
                for line in engine.log_messages
            )
        )

    def test_all_busy_three_of_three_clears_wrong_one_of_three_expectation(self):
        engine = self._engine(
            _rally_v9_team_states={
                1: RALLY_TEAM_BUSY,
                2: RALLY_TEAM_BUSY,
                3: RALLY_TEAM_BUSY,
            },
            _rally_v9_last_squad_count=0,
            _rally_v9_expected_squad_count=1,
            _rally_v9_expected_count_since=10.0,
            _rally_v12_pending_squad_count=2,
            _rally_v12_pending_squad_since=11.0,
        )

        with patch.object(
            v23,
            "_ORIGINAL_OBSERVE_SQUAD_COUNT",
            side_effect=AssertionError("3/3 must corroborate exact all-busy cache"),
        ):
            result = v23._observe_squad_count(engine, 3, now=12.0)

        self.assertFalse(result)
        self.assertEqual(engine._rally_v9_last_squad_count, 3)
        self.assertIsNone(engine._rally_v9_expected_squad_count)
        self.assertIsNone(engine._rally_v12_pending_squad_count)
        self.assertTrue(engine._rally_v9_team_cache_valid)

    def test_count_different_from_exact_busy_count_still_delegates_return_policy(self):
        engine = self._engine(_rally_v9_last_squad_count=1)

        sentinel = object()
        with patch.object(
            v23,
            "_ORIGINAL_OBSERVE_SQUAD_COUNT",
            new=lambda _engine, _count, now=None: sentinel,
        ):
            result = v23._observe_squad_count(engine, 0, now=30.0)

        self.assertIs(result, sentinel)

    def test_no_match_back_arms_short_reentry_debounce(self):
        engine = self._engine()
        step = SimpleNamespace(name="Joining")
        action = SimpleNamespace(type="click_matching_row")

        with patch.object(v23, "_ORIGINAL_NO_MATCH_FALLBACK", return_value=True), patch.object(
            v23._v13, "_uses_back_fallback", return_value=True
        ), patch.object(v23.time, "monotonic", return_value=100.0):
            result = v23._run_no_match_fallback(engine, step, action, {})

        self.assertTrue(result)
        self.assertEqual(engine._rally_v23_entry_not_before, 101.0)

    def test_entry_step_is_blocked_only_during_debounce(self):
        engine = self._engine(_rally_v23_entry_not_before=101.0)
        step = SimpleNamespace(name="Enter Rally after team probe")

        with patch.object(v23.time, "monotonic", return_value=100.5), patch.object(
            v23,
            "_ORIGINAL_EVALUATE_STEP",
            side_effect=AssertionError("entry should not delegate during debounce"),
        ):
            self.assertEqual(v23._evaluate_step(engine, step), (False, {}, {}))

        expected = (True, {"x": 1}, {"y": 2})
        with patch.object(v23.time, "monotonic", return_value=101.1), patch.object(
            v23,
            "_ORIGINAL_EVALUATE_STEP",
            return_value=expected,
        ):
            self.assertEqual(v23._evaluate_step(engine, step), expected)

    def test_two_team_observer_is_unchanged(self):
        engine = self._engine(_rally_hot_path_three_team=False)
        sentinel = object()

        with patch.object(
            v23,
            "_ORIGINAL_OBSERVE_SQUAD_COUNT",
            new=lambda _engine, _count, now=None: sentinel,
        ):
            self.assertIs(v23._observe_squad_count(engine, 1, now=10.0), sentinel)


if __name__ == "__main__":
    unittest.main()
