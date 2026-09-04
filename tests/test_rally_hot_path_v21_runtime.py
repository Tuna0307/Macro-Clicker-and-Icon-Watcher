import unittest
from types import SimpleNamespace
from unittest.mock import patch

from macro_clicker import rally_hot_path_v19_runtime as v19
from macro_clicker import rally_hot_path_v21_runtime as v21


class RallyHotPathV21RuntimeTests(unittest.TestCase):
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
            return v21._available_rally_team_level_cap(engine, self._row_action())

    def test_live_lv80_case_probes_after_about_twelve_seconds(self):
        engine = self._engine()

        cap = self._cap(engine, now=111.6)

        self.assertEqual(cap, 80)
        self.assertTrue(
            any(
                "allowing one formation refresh probe" in line
                for line in engine.log_messages
            )
        )

    def test_cache_younger_than_ten_seconds_stays_restrictive(self):
        engine = self._engine()

        cap = self._cap(engine, now=109.9)

        self.assertEqual(cap, 60)
        self.assertFalse(engine.log_messages)

    def test_existing_v19_retry_cooldown_still_applies(self):
        engine = self._engine()

        self.assertEqual(self._cap(engine, now=111.0), 80)
        self.assertEqual(self._cap(engine, now=116.0), 60)
        self.assertEqual(self._cap(engine, now=121.1), 80)

    def test_v19_global_age_is_restored_after_each_call(self):
        engine = self._engine()
        original = v19.STALE_CACHE_PROBE_AGE_SECONDS

        self._cap(engine, now=111.6)

        self.assertEqual(v19.STALE_CACHE_PROBE_AGE_SECONDS, original)

    def test_two_team_path_is_unchanged(self):
        engine = self._engine(_rally_hot_path_three_team=False)

        with patch.object(
            v21,
            "_ORIGINAL_AVAILABLE_LEVEL_CAP",
            new=lambda _engine, _action: 55,
        ):
            cap = v21._available_rally_team_level_cap(engine, self._row_action())

        self.assertEqual(cap, 55)


if __name__ == "__main__":
    unittest.main()
