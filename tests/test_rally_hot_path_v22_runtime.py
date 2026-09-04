import unittest
from types import SimpleNamespace
from unittest.mock import patch

from macro_clicker import rally_hot_path_v22_runtime as v22
from macro_clicker.rally_team_policy import RALLY_TEAM_BUSY, RALLY_TEAM_IDLE


class RallyHotPathV22RuntimeTests(unittest.TestCase):
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
            "_rally_v9_last_squad_count": 2,
            "_rally_v22_full_squad_hold_until": 0.0,
            "_rally_v22_last_gate_log": 0.0,
            "_rally_v19_probe_until": 0.0,
            "_rally_v19_probe_cache_captured_at": 0.0,
            "log_messages": [],
        }
        values.update(overrides)
        engine = SimpleNamespace(**values)
        engine.log = engine.log_messages.append
        return engine

    @staticmethod
    def _row_action():
        return SimpleNamespace(type="click_matching_row")

    def test_exact_all_busy_cache_blocks_stale_formation_probe(self):
        engine = self._engine()

        with patch.object(
            v22,
            "_ORIGINAL_AVAILABLE_LEVEL_CAP",
            side_effect=AssertionError("all-busy cache must not delegate to v19/v21"),
        ), patch.object(v22.time, "monotonic", return_value=50.0):
            cap = v22._available_rally_team_level_cap(engine, self._row_action())

        self.assertIsNone(cap)
        self.assertEqual(engine._rally_v19_probe_until, 0.0)
        self.assertTrue(any("no Rally + allowed" in line for line in engine.log_messages))

    def test_one_known_idle_team_keeps_v21_refresh_behavior(self):
        engine = self._engine(
            _rally_v9_team_states={
                1: RALLY_TEAM_BUSY,
                2: RALLY_TEAM_IDLE,
                3: RALLY_TEAM_BUSY,
            }
        )

        with patch.object(
            v22,
            "_ORIGINAL_AVAILABLE_LEVEL_CAP",
            new=lambda _engine, _action: 80,
        ):
            cap = v22._available_rally_team_level_cap(engine, self._row_action())

        self.assertEqual(cap, 80)

    def test_stable_world_map_three_of_three_blocks_plus_even_without_exact_cache(self):
        engine = self._engine(
            _rally_v9_team_cache_valid=False,
            _rally_v9_team_states=None,
            _rally_v9_last_squad_count=3,
        )

        with patch.object(
            v22,
            "_ORIGINAL_AVAILABLE_LEVEL_CAP",
            side_effect=AssertionError("stable 3/3 must hard-block Rally +"),
        ), patch.object(v22.time, "monotonic", return_value=50.0):
            cap = v22._available_rally_team_level_cap(engine, self._row_action())

        self.assertIsNone(cap)

    def test_exact_all_busy_blocks_world_map_rally_entry(self):
        engine = self._engine()
        step = SimpleNamespace(name="Enter Rally after team probe")

        with patch.object(
            v22,
            "_ORIGINAL_EVALUATE_STEP",
            side_effect=AssertionError("all-busy entry must not delegate"),
        ), patch.object(v22.time, "monotonic", return_value=50.0):
            result = v22._evaluate_step(engine, step)

        self.assertEqual(result, (False, {}, {}))
        self.assertTrue(
            any("Rally entry hard-blocked" in line for line in engine.log_messages)
        )

    def test_recent_broad_three_of_three_hold_blocks_entry(self):
        engine = self._engine(
            _rally_v9_team_cache_valid=False,
            _rally_v9_team_states=None,
            _rally_v9_last_squad_count=2,
            _rally_v22_full_squad_hold_until=52.0,
        )
        step = SimpleNamespace(name="Enter Rally after team probe")

        with patch.object(
            v22,
            "_ORIGINAL_EVALUATE_STEP",
            side_effect=AssertionError("held 3/3 entry must not delegate"),
        ), patch.object(v22.time, "monotonic", return_value=51.0):
            result = v22._evaluate_step(engine, step)

        self.assertEqual(result, (False, {}, {}))

    def test_positive_broad_detection_arms_two_second_hold(self):
        engine = self._engine(
            _rally_v9_team_cache_valid=False,
            _rally_v9_team_states=None,
        )

        with patch.object(
            v22, "_ORIGINAL_BROAD_THREE_OF_THREE", return_value=True
        ), patch.object(v22.time, "monotonic", return_value=100.0):
            self.assertTrue(v22._sticky_broad_three_of_three(engine))

        self.assertEqual(engine._rally_v22_full_squad_hold_until, 102.0)

    def test_two_team_path_is_unchanged(self):
        engine = self._engine(_rally_hot_path_three_team=False)

        with patch.object(
            v22,
            "_ORIGINAL_AVAILABLE_LEVEL_CAP",
            new=lambda _engine, _action: 55,
        ):
            cap = v22._available_rally_team_level_cap(engine, self._row_action())

        self.assertEqual(cap, 55)


if __name__ == "__main__":
    unittest.main()
