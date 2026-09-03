import unittest
from types import SimpleNamespace
from unittest.mock import patch

from macro_clicker import rally_hot_path_v12_runtime as v12
from macro_clicker.rally_team_policy import RALLY_TEAM_BUSY, RALLY_TEAM_IDLE


class RallyHotPathV12RuntimeTests(unittest.TestCase):
    @staticmethod
    def _engine(**overrides):
        values = {
            "_rally_hot_path_three_team": True,
            "_rally_hot_entry_latched": False,
            "_rally_join_guard_until": 0.0,
            "_rally_v9_team_cache_valid": True,
            "_rally_v9_team_states": {
                1: RALLY_TEAM_BUSY,
                2: RALLY_TEAM_IDLE,
                3: RALLY_TEAM_IDLE,
            },
            "_rally_v9_team_cache_captured_at": 10.0,
            "_rally_v9_last_cached_cap_log": object(),
            "_rally_v9_last_squad_count": 0,
            "_rally_v9_expected_squad_count": 1,
            "_rally_v9_expected_count_since": 10.0,
            "_rally_v9_last_count_poll": 0.0,
            "_rally_v12_pending_squad_count": None,
            "_rally_v12_pending_squad_since": 0.0,
            "_rally_v12_last_expected_lag_log": None,
            "log_messages": [],
        }
        values.update(overrides)
        engine = SimpleNamespace(**values)
        engine.log = engine.log_messages.append
        return engine

    def test_lagging_expected_dispatch_count_preserves_exact_cache(self):
        engine = self._engine()

        changed = v12._observe_squad_count(engine, 0, now=11.0)

        self.assertFalse(changed)
        self.assertTrue(engine._rally_v9_team_cache_valid)
        self.assertEqual(engine._rally_v9_expected_squad_count, 1)
        self.assertTrue(any("preserving exact fixed-team cache" in line for line in engine.log_messages))

    def test_expected_increment_can_arrive_late_without_invalidating_cache(self):
        engine = self._engine()

        changed = v12._observe_squad_count(engine, 1, now=25.0)

        self.assertFalse(changed)
        self.assertTrue(engine._rally_v9_team_cache_valid)
        self.assertEqual(engine._rally_v9_last_squad_count, 1)
        self.assertIsNone(engine._rally_v9_expected_squad_count)
        self.assertTrue(any("matches our confirmed dispatch" in line for line in engine.log_messages))

    def test_derived_zero_requires_long_stable_confirmation(self):
        engine = self._engine(
            _rally_v9_last_squad_count=1,
            _rally_v9_expected_squad_count=None,
            _rally_v9_expected_count_since=0.0,
        )

        self.assertFalse(v12._observe_squad_count(engine, 0, now=20.0))
        self.assertTrue(engine._rally_v9_team_cache_valid)
        self.assertEqual(engine._rally_v12_pending_squad_count, 0)

        self.assertFalse(v12._observe_squad_count(engine, 0, now=21.9))
        self.assertTrue(engine._rally_v9_team_cache_valid)

        self.assertTrue(v12._observe_squad_count(engine, 0, now=22.1))
        self.assertFalse(engine._rally_v9_team_cache_valid)
        self.assertIsNone(engine._rally_v9_team_states)

    def test_positive_count_change_uses_short_confirmation(self):
        engine = self._engine(
            _rally_v9_last_squad_count=2,
            _rally_v9_expected_squad_count=None,
            _rally_v9_expected_count_since=0.0,
        )

        self.assertFalse(v12._observe_squad_count(engine, 1, now=30.0))
        self.assertTrue(engine._rally_v9_team_cache_valid)
        self.assertTrue(v12._observe_squad_count(engine, 1, now=30.5))
        self.assertFalse(engine._rally_v9_team_cache_valid)

    def test_poll_is_suppressed_while_rally_entry_is_latched(self):
        engine = self._engine(_rally_hot_entry_latched=True)

        with patch.object(
            v12._v9,
            "_read_world_squad_count",
            side_effect=AssertionError("count capture must not run during Rally transition"),
        ):
            changed = v12._poll_squad_count(engine, now=40.0)

        self.assertFalse(changed)

    def test_poll_requires_world_map_rally_icon_proof(self):
        engine = self._engine()

        with patch.object(v12._v9, "_condition_visible", return_value=False), patch.object(
            v12._v9,
            "_read_world_squad_count",
            side_effect=AssertionError("count capture must wait for world-map proof"),
        ):
            changed = v12._poll_squad_count(engine, now=50.0)

        self.assertFalse(changed)


if __name__ == "__main__":
    unittest.main()
