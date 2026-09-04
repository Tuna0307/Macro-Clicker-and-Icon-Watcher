import unittest
from types import SimpleNamespace

from macro_clicker import rally_hot_path_v18_runtime as v18
from macro_clicker.rally_team_policy import RALLY_TEAM_BUSY, RALLY_TEAM_IDLE


class RallyHotPathV18RuntimeTests(unittest.TestCase):
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
            "_rally_v9_last_squad_count": 0,
            "_rally_v9_expected_squad_count": 1,
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

    def test_expected_increment_inside_stale_horizon_still_preserves_cache(self):
        engine = self._engine()

        changed = v18._observe_squad_count(engine, 1, now=129.0)

        self.assertFalse(changed)
        self.assertTrue(engine._rally_v9_team_cache_valid)
        self.assertEqual(engine._rally_v9_last_squad_count, 1)
        self.assertIsNone(engine._rally_v9_expected_squad_count)
        self.assertIsNone(engine._rally_v12_pending_squad_count)
        self.assertTrue(
            any("matches our confirmed dispatch" in line for line in engine.log_messages)
        )
        self.assertFalse(any("rally-v18" in line for line in engine.log_messages))

    def test_overage_expected_increment_becomes_ordinary_change_candidate(self):
        engine = self._engine()

        changed = v18._observe_squad_count(engine, 1, now=131.0)

        self.assertFalse(changed)
        self.assertTrue(engine._rally_v9_team_cache_valid)
        self.assertEqual(engine._rally_v9_last_squad_count, 0)
        self.assertIsNone(engine._rally_v9_expected_squad_count)
        self.assertEqual(engine._rally_v12_pending_squad_count, 1)
        self.assertEqual(engine._rally_v12_pending_squad_since, 131.0)
        self.assertTrue(
            any(
                "treating it as ordinary count-change evidence" in line
                for line in engine.log_messages
            )
        )
        self.assertTrue(
            any("require 0.45s stable confirmation" in line for line in engine.log_messages)
        )

    def test_stable_overage_expected_increment_invalidates_only_stale_identity(self):
        engine = self._engine()

        self.assertFalse(v18._observe_squad_count(engine, 1, now=131.0))
        changed = v18._observe_squad_count(engine, 1, now=131.5)

        self.assertTrue(changed)
        self.assertFalse(engine._rally_v9_team_cache_valid)
        self.assertIsNone(engine._rally_v9_team_states)
        self.assertEqual(engine._rally_v9_last_squad_count, 1)
        self.assertIsNone(engine._rally_v9_expected_squad_count)
        self.assertTrue(
            any(
                "stable world-map squad count changed 0/3 -> 1/3" in line
                for line in engine.log_messages
            )
        )

    def test_exact_stale_horizon_is_no_longer_dispatch_proof(self):
        engine = self._engine()

        changed = v18._observe_squad_count(engine, 1, now=130.0)

        self.assertFalse(changed)
        self.assertTrue(engine._rally_v9_team_cache_valid)
        self.assertEqual(engine._rally_v12_pending_squad_count, 1)
        self.assertIsNone(engine._rally_v9_expected_squad_count)

    def test_non_expected_backtrack_still_uses_v14_guard_inside_horizon(self):
        engine = self._engine(
            _rally_v9_last_squad_count=1,
            _rally_v9_expected_squad_count=2,
        )

        changed = v18._observe_squad_count(engine, 0, now=101.0)

        self.assertFalse(changed)
        self.assertTrue(engine._rally_v9_team_cache_valid)
        self.assertEqual(engine._rally_v9_last_squad_count, 1)
        self.assertEqual(engine._rally_v9_expected_squad_count, 2)
        self.assertIsNone(engine._rally_v12_pending_squad_count)
        self.assertTrue(
            any("confirmed dispatch expects 2/3" in line for line in engine.log_messages)
        )

    def test_invalid_cache_does_not_activate_v18_special_case(self):
        engine = self._engine(_rally_v9_team_cache_valid=False)

        changed = v18._observe_squad_count(engine, 1, now=131.0)

        self.assertFalse(changed)
        self.assertEqual(engine._rally_v9_last_squad_count, 1)
        self.assertIsNone(engine._rally_v9_expected_squad_count)
        self.assertFalse(any("rally-v18" in line for line in engine.log_messages))

    def test_two_team_path_delegates_without_v18_reclassification(self):
        engine = self._engine(_rally_hot_path_three_team=False)

        changed = v18._observe_squad_count(engine, 1, now=131.0)

        self.assertFalse(changed)
        self.assertEqual(engine._rally_v9_last_squad_count, 1)
        self.assertIsNone(engine._rally_v9_expected_squad_count)
        self.assertFalse(any("rally-v18" in line for line in engine.log_messages))


if __name__ == "__main__":
    unittest.main()
