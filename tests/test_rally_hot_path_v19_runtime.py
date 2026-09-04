import unittest
from types import SimpleNamespace
from unittest.mock import patch

from macro_clicker import rally_hot_path_v19_runtime as v19
from macro_clicker.models import load_scenario


class RallyHotPathV19RuntimeTests(unittest.TestCase):
    @staticmethod
    def _engine(**overrides):
        values = {
            "_rally_hot_path_three_team": True,
            "_rally_v9_team_cache_valid": True,
            "_rally_v9_team_cache_captured_at": 100.0,
            "_rally_v19_probe_until": 0.0,
            "_rally_v19_probe_cache_captured_at": 0.0,
            "_rally_v19_last_probe_at": 0.0,
            "log_messages": [],
        }
        values.update(overrides)
        engine = SimpleNamespace(**values)
        engine.log = engine.log_messages.append
        return engine

    @staticmethod
    def _row_action():
        return SimpleNamespace(type="click_matching_row")

    def _cap(self, engine, *, now, cached=60, broad=80):
        with patch.object(
            v19,
            "_ORIGINAL_AVAILABLE_LEVEL_CAP",
            new=lambda _engine, _action: cached,
        ), patch.object(
            v19,
            "_configured_selector_ceiling",
            new=lambda _engine: broad,
        ), patch.object(v19.time, "monotonic", return_value=now):
            return v19._available_rally_team_level_cap(engine, self._row_action())

    def test_stale_restrictive_cache_uses_broad_ceiling_for_one_refresh_probe(self):
        engine = self._engine()

        cap = self._cap(engine, now=131.0)

        self.assertEqual(cap, 80)
        self.assertEqual(engine._rally_v19_probe_cache_captured_at, 100.0)
        self.assertEqual(engine._rally_v19_probe_until, 135.0)
        self.assertEqual(engine._rally_v19_last_probe_at, 131.0)
        self.assertTrue(
            any(
                "allowing one formation refresh probe" in line
                for line in engine.log_messages
            )
        )

    def test_probe_window_keeps_broad_ceiling_for_join_revalidation(self):
        engine = self._engine()

        self.assertEqual(self._cap(engine, now=131.0), 80)
        self.assertEqual(self._cap(engine, now=133.0), 80)

    def test_young_exact_cache_keeps_restrictive_ceiling(self):
        engine = self._engine()

        cap = self._cap(engine, now=129.9)

        self.assertEqual(cap, 60)
        self.assertEqual(engine._rally_v19_probe_until, 0.0)
        self.assertFalse(engine.log_messages)

    def test_stale_all_busy_cache_can_probe_configured_ceiling(self):
        engine = self._engine()

        cap = self._cap(engine, now=131.0, cached=None, broad=80)

        self.assertEqual(cap, 80)
        self.assertTrue(any("cached ceiling=none" in line for line in engine.log_messages))

    def test_probe_retry_cooldown_prevents_repeated_plus_races_without_refresh(self):
        engine = self._engine()

        self.assertEqual(self._cap(engine, now=131.0), 80)
        self.assertEqual(self._cap(engine, now=136.0), 60)
        self.assertEqual(self._cap(engine, now=141.1), 80)

    def test_fresh_cache_timestamp_cancels_old_probe_window(self):
        engine = self._engine()

        self.assertEqual(self._cap(engine, now=131.0), 80)
        engine._rally_v9_team_cache_captured_at = 132.0

        cap = self._cap(engine, now=133.0)

        self.assertEqual(cap, 60)
        self.assertEqual(engine._rally_v19_probe_until, 0.0)
        self.assertEqual(engine._rally_v19_probe_cache_captured_at, 0.0)

    def test_no_probe_when_cached_ceiling_is_already_configured_ceiling(self):
        engine = self._engine()

        cap = self._cap(engine, now=200.0, cached=80, broad=80)

        self.assertEqual(cap, 80)
        self.assertEqual(engine._rally_v19_probe_until, 0.0)
        self.assertFalse(engine.log_messages)

    def test_two_team_path_is_unchanged(self):
        engine = self._engine(_rally_hot_path_three_team=False)

        cap = self._cap(engine, now=200.0, cached=55, broad=80)

        self.assertEqual(cap, 55)
        self.assertEqual(engine._rally_v19_probe_until, 0.0)
        self.assertFalse(engine.log_messages)

    def test_configured_selector_ceiling_comes_from_editable_scenario_limits(self):
        scenario = load_scenario("Rally gold mob_ 3 team")
        engine = SimpleNamespace(scenario=scenario, _rally_hot_path_three_team=True)
        selector = next(
            action
            for step in scenario.steps
            for action in step.actions
            if action.type == "select_rally_team"
        )
        expected = max(
            value
            for value in (
                selector.team1_max_level,
                selector.team2_max_level,
                selector.team3_max_level,
            )
            if value is not None
        )

        self.assertEqual(v19._configured_selector_ceiling(engine), expected)


if __name__ == "__main__":
    unittest.main()
