import unittest
from types import SimpleNamespace
from unittest.mock import patch

from macro_clicker import rally_hot_path_v26_runtime as v26
from macro_clicker.rally_team_policy import RALLY_TEAM_BUSY, RALLY_TEAM_IDLE


class RallyHotPathV26RuntimeTests(unittest.TestCase):
    @staticmethod
    def _engine(**overrides):
        values = {
            "_rally_hot_path_three_team": True,
            "_rally_v9_team_cache_valid": True,
            "_rally_v9_team_states": {
                1: RALLY_TEAM_BUSY,
                2: RALLY_TEAM_BUSY,
                3: RALLY_TEAM_BUSY,
            },
            "_rally_v12_pending_squad_count": None,
            "_rally_v12_pending_squad_since": 0.0,
            "_rally_v26_zero_candidate_since": 0.0,
            "_rally_v26_zero_candidate_samples": 0,
            "_rally_v26_last_zero_log": 0.0,
            "_rally_v26_last_broad_check": 0.0,
            "log_messages": [],
        }
        values.update(overrides)
        engine = SimpleNamespace(**values)
        engine.log = engine.log_messages.append
        return engine

    def test_first_derived_zero_is_held_while_exact_cache_is_all_busy(self):
        engine = self._engine()

        with patch.object(v26._v7, "_broad_three_of_three", return_value=False), \
             patch.object(
                 v26,
                 "_ORIGINAL_OBSERVE",
                 side_effect=AssertionError("derived zero must be guarded first"),
             ):
            result = v26._observe_squad_count(engine, 0, now=10.0)

        self.assertFalse(result)
        self.assertEqual(engine._rally_v26_zero_candidate_since, 10.0)
        self.assertTrue(
            any("starting extra return-evidence guard" in line
                for line in engine.log_messages)
        )

    def test_persistent_zero_is_not_released_before_preconfirm_horizon(self):
        engine = self._engine(
            _rally_v26_zero_candidate_since=10.0,
            _rally_v26_zero_candidate_samples=3,
            _rally_v26_last_broad_check=10.0,
        )

        with patch.object(
            v26,
            "_ORIGINAL_OBSERVE",
            side_effect=AssertionError("must remain guarded"),
        ):
            result = v26._observe_squad_count(engine, 0, now=12.9)

        self.assertFalse(result)

    def test_zero_releases_to_existing_v12_logic_after_guard_horizon(self):
        engine = self._engine(
            _rally_v26_zero_candidate_since=10.0,
            _rally_v26_zero_candidate_samples=10,
            _rally_v26_last_broad_check=12.5,
        )

        with patch.object(v26, "_ORIGINAL_OBSERVE", return_value=False) as original:
            result = v26._observe_squad_count(engine, 0, now=13.1)

        self.assertFalse(result)
        original.assert_called_once_with(engine, 0, now=13.1)

    def test_positive_broad_three_cancels_derived_zero_candidate(self):
        engine = self._engine(
            _rally_v26_zero_candidate_since=10.0,
            _rally_v26_zero_candidate_samples=4,
        )

        with patch.object(v26._v7, "_broad_three_of_three", return_value=True), \
             patch.object(
                 v26,
                 "_ORIGINAL_OBSERVE",
                 side_effect=AssertionError("broad 3/3 must preserve cache"),
             ):
            result = v26._observe_squad_count(engine, 0, now=11.0)

        self.assertFalse(result)
        self.assertEqual(engine._rally_v26_zero_candidate_since, 0.0)
        self.assertTrue(
            any("positive broad 3/3" in line for line in engine.log_messages)
        )

    def test_explicit_nonzero_return_evidence_delegates_immediately(self):
        engine = self._engine()

        with patch.object(v26, "_ORIGINAL_OBSERVE", return_value=True) as original:
            result = v26._observe_squad_count(engine, 2, now=10.0)

        self.assertTrue(result)
        original.assert_called_once_with(engine, 2, now=10.0)

    def test_zero_delegates_when_exact_cache_not_all_busy(self):
        engine = self._engine(
            _rally_v9_team_states={
                1: RALLY_TEAM_BUSY,
                2: RALLY_TEAM_IDLE,
                3: RALLY_TEAM_BUSY,
            }
        )

        with patch.object(v26, "_ORIGINAL_OBSERVE", return_value=True) as original:
            result = v26._observe_squad_count(engine, 0, now=10.0)

        self.assertTrue(result)
        original.assert_called_once_with(engine, 0, now=10.0)

    def test_two_team_path_is_unchanged(self):
        engine = self._engine(_rally_hot_path_three_team=False)

        with patch.object(v26, "_ORIGINAL_OBSERVE", return_value=True) as original:
            result = v26._observe_squad_count(engine, 0, now=10.0)

        self.assertTrue(result)
        original.assert_called_once_with(engine, 0, now=10.0)


if __name__ == "__main__":
    unittest.main()
