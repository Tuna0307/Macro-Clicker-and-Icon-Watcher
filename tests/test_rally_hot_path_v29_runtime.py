import unittest
from types import SimpleNamespace
from unittest.mock import patch

from macro_clicker import rally_hot_path_v29_runtime as v29


class RallyHotPathV29RuntimeTests(unittest.TestCase):
    @staticmethod
    def _engine(**overrides):
        values = {
            "_rally_hot_path_three_team": True,
            "_rally_v9_team_cache_valid": True,
            "_rally_v9_team_states": {1: "BUSY", 2: "BUSY", 3: "BUSY"},
            "_rally_v9_last_squad_count": 2,
            "_rally_v9_expected_squad_count": None,
            "_rally_v9_expected_count_since": 0.0,
            "_rally_v12_pending_squad_count": None,
            "_rally_v12_pending_squad_since": 0.0,
            "_rally_v26_zero_candidate_since": 0.0,
            "_rally_v26_zero_candidate_samples": 0,
            "_rally_v26_last_zero_log": 0.0,
            "_rally_v26_last_broad_check": 0.0,
            "_rally_v29_zero_released": False,
            "_rally_v29_last_release_log": 0.0,
            "log_messages": [],
        }
        values.update(overrides)
        engine = SimpleNamespace(**values)
        engine.log = engine.log_messages.append
        return engine

    @staticmethod
    def _fake_v12_observer(engine, count, now=None):
        now = float(now)
        candidate = getattr(engine, "_rally_v12_pending_squad_count", None)
        since = float(getattr(engine, "_rally_v12_pending_squad_since", 0.0))
        if candidate != count:
            engine._rally_v12_pending_squad_count = count
            engine._rally_v12_pending_squad_since = now
            return False
        if now - since < 2.0:
            return False
        engine._rally_v9_last_squad_count = count
        engine._rally_v9_team_cache_valid = False
        engine._rally_v9_expected_squad_count = None
        engine._rally_v9_expected_count_since = 0.0
        engine._rally_v12_pending_squad_count = None
        engine._rally_v12_pending_squad_since = 0.0
        return True

    def test_zero_candidate_survives_after_three_second_guard(self):
        engine = self._engine()

        with patch.object(
            v29._v26, "_broad_three_of_three_rate_limited", return_value=False
        ), patch.object(
            v29, "_UNDERLYING_OBSERVE", side_effect=self._fake_v12_observer
        ):
            self.assertFalse(v29._observe_squad_count(engine, 0, now=100.0))
            self.assertFalse(v29._observe_squad_count(engine, 0, now=102.9))
            self.assertIsNone(engine._rally_v12_pending_squad_count)

            self.assertFalse(v29._observe_squad_count(engine, 0, now=103.1))
            self.assertEqual(engine._rally_v12_pending_squad_count, 0)
            self.assertEqual(engine._rally_v12_pending_squad_since, 103.1)

            self.assertFalse(v29._observe_squad_count(engine, 0, now=104.0))
            self.assertEqual(
                engine._rally_v12_pending_squad_since,
                103.1,
                "post-guard samples must not restart v12's stable-zero timer",
            )

            self.assertTrue(v29._observe_squad_count(engine, 0, now=105.2))
            self.assertFalse(engine._rally_v9_team_cache_valid)

    def test_persistent_zero_reuses_original_time_after_stale_expected_horizon(self):
        engine = self._engine(
            _rally_v9_expected_squad_count=3,
            _rally_v9_expected_count_since=100.0,
            _rally_v26_zero_candidate_since=101.0,
            _rally_v26_zero_candidate_samples=150,
        )

        with patch.object(
            v29._v26, "_broad_three_of_three_rate_limited", return_value=False
        ), patch.object(
            v29, "_UNDERLYING_OBSERVE", side_effect=self._fake_v12_observer
        ):
            changed = v29._observe_squad_count(engine, 0, now=130.1)

        self.assertTrue(changed)
        self.assertFalse(engine._rally_v9_team_cache_valid)
        self.assertTrue(
            any(
                "reusing its original observation time" in line
                for line in engine.log_messages
            )
        )

    def test_positive_broad_three_cancels_zero_candidate(self):
        engine = self._engine(
            _rally_v26_zero_candidate_since=100.0,
            _rally_v12_pending_squad_count=0,
            _rally_v12_pending_squad_since=103.0,
            _rally_v29_zero_released=True,
        )

        with patch.object(
            v29._v26, "_broad_three_of_three_rate_limited", return_value=True
        ), patch.object(
            v29,
            "_UNDERLYING_OBSERVE",
            side_effect=AssertionError("positive 3/3 must stop zero hand-off"),
        ):
            changed = v29._observe_squad_count(engine, 0, now=104.0)

        self.assertFalse(changed)
        self.assertTrue(engine._rally_v9_team_cache_valid)
        self.assertIsNone(engine._rally_v12_pending_squad_count)
        self.assertFalse(engine._rally_v29_zero_released)

    def test_non_three_team_path_delegates_unchanged(self):
        engine = self._engine(_rally_hot_path_three_team=False)
        expected = object()

        with patch.object(
            v29, "_UNDERLYING_OBSERVE", return_value=expected
        ) as delegated:
            result = v29._observe_squad_count(engine, 0, now=123.0)

        self.assertIs(result, expected)
        delegated.assert_called_once_with(engine, 0, now=123.0)


if __name__ == "__main__":
    unittest.main()
