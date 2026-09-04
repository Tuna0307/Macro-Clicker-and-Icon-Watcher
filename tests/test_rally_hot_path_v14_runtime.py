import unittest
from types import SimpleNamespace

from macro_clicker import rally_hot_path_v14_runtime as v14
from macro_clicker.rally_team_policy import RALLY_TEAM_BUSY, RALLY_TEAM_IDLE


class RallyHotPathV14RuntimeTests(unittest.TestCase):
    @staticmethod
    def _engine(**overrides):
        values = {
            "_rally_hot_path_three_team": True,
            "_rally_v9_team_cache_valid": True,
            "_rally_v9_team_states": {
                1: RALLY_TEAM_BUSY,
                2: RALLY_TEAM_IDLE,
                3: RALLY_TEAM_BUSY,
            },
            "_rally_v9_team_cache_captured_at": 10.0,
            "_rally_v9_last_cached_cap_log": object(),
            "_rally_v9_last_squad_count": 1,
            "_rally_v9_expected_squad_count": 2,
            "_rally_v9_expected_count_since": 100.0,
            "_rally_v12_pending_squad_count": None,
            "_rally_v12_pending_squad_since": 0.0,
            "_rally_v12_last_expected_lag_log": None,
            "_rally_v14_last_backtrack_log": None,
            "log_messages": [],
        }
        values.update(overrides)
        engine = SimpleNamespace(**values)
        engine.log = engine.log_messages.append
        return engine

    def test_backward_zero_during_expected_dispatch_preserves_exact_cache(self):
        engine = self._engine()

        changed = v14._observe_squad_count(engine, 0, now=101.0)

        self.assertFalse(changed)
        self.assertTrue(engine._rally_v9_team_cache_valid)
        self.assertEqual(engine._rally_v9_last_squad_count, 1)
        self.assertEqual(engine._rally_v9_expected_squad_count, 2)
        self.assertIsNone(engine._rally_v12_pending_squad_count)
        self.assertTrue(
            any("confirmed dispatch expects 2/3" in line for line in engine.log_messages)
        )

    def test_repeated_backward_zero_cannot_become_stable_invalidation_candidate(self):
        engine = self._engine()

        self.assertFalse(v14._observe_squad_count(engine, 0, now=101.0))
        self.assertFalse(v14._observe_squad_count(engine, 0, now=104.0))

        self.assertTrue(engine._rally_v9_team_cache_valid)
        self.assertEqual(engine._rally_v9_last_squad_count, 1)
        self.assertIsNone(engine._rally_v12_pending_squad_count)

    def test_late_expected_increment_still_resolves_normally_after_backtrack(self):
        engine = self._engine()

        self.assertFalse(v14._observe_squad_count(engine, 0, now=101.0))
        self.assertFalse(v14._observe_squad_count(engine, 2, now=105.0))

        self.assertTrue(engine._rally_v9_team_cache_valid)
        self.assertEqual(engine._rally_v9_last_squad_count, 2)
        self.assertIsNone(engine._rally_v9_expected_squad_count)
        self.assertTrue(
            any("matches our confirmed dispatch" in line for line in engine.log_messages)
        )

    def test_after_expected_stale_horizon_v12_stable_change_policy_resumes(self):
        engine = self._engine()

        self.assertFalse(v14._observe_squad_count(engine, 0, now=131.0))
        self.assertEqual(engine._rally_v12_pending_squad_count, 0)
        self.assertTrue(engine._rally_v9_team_cache_valid)

        self.assertTrue(v14._observe_squad_count(engine, 0, now=133.1))
        self.assertFalse(engine._rally_v9_team_cache_valid)

    def test_without_expected_dispatch_existing_v12_behavior_is_unchanged(self):
        engine = self._engine(
            _rally_v9_expected_squad_count=None,
            _rally_v9_expected_count_since=0.0,
        )

        self.assertFalse(v14._observe_squad_count(engine, 0, now=200.0))
        self.assertEqual(engine._rally_v12_pending_squad_count, 0)
        self.assertTrue(v14._observe_squad_count(engine, 0, now=202.1))
        self.assertFalse(engine._rally_v9_team_cache_valid)


if __name__ == "__main__":
    unittest.main()
