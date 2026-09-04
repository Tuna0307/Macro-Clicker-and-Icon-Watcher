import unittest
from types import SimpleNamespace
from unittest.mock import patch

from macro_clicker import rally_hot_path_v30_runtime as v30
from macro_clicker.rally_team_policy import RALLY_TEAM_BUSY


class RallyHotPathV30RuntimeTests(unittest.TestCase):
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
            "_rally_hot_entry_latched": False,
            "_rally_join_guard_until": 0.0,
            "_rally_v30_last_count_sample_at": 90.0,
            "_rally_v30_last_sample_value": 3,
            "_rally_v30_last_fallback_poll": 0.0,
            "_rally_v30_last_fallback_log": 0.0,
            "log_messages": [],
        }
        values.update(overrides)
        engine = SimpleNamespace(**values)
        engine.log = engine.log_messages.append
        return engine

    def test_fallback_reads_tiny_roi_after_normal_poll_silence(self):
        engine = self._engine()

        with patch.object(v30, "_ORIGINAL_POLL_SQUAD_COUNT", return_value=False), patch.object(
            v30, "_ORIGINAL_READ_WORLD_SQUAD_COUNT", return_value=1
        ) as direct_read, patch.object(
            v30._v12, "_observe_squad_count", return_value=True
        ) as observe:
            result = v30._poll_squad_count(engine, now=100.0)

        self.assertTrue(result)
        direct_read.assert_called_once_with(engine)
        observe.assert_called_once_with(engine, 1, now=100.0)
        self.assertEqual(engine._rally_v30_last_count_sample_at, 100.0)
        self.assertEqual(engine._rally_v30_last_sample_value, 1)
        self.assertTrue(any("direct tiny-ROI fallback observed 1/3" in line for line in engine.log_messages))

    def test_recent_normal_sample_prevents_fallback(self):
        engine = self._engine(_rally_v30_last_count_sample_at=99.0)

        with patch.object(v30, "_ORIGINAL_POLL_SQUAD_COUNT", return_value=False), patch.object(
            v30,
            "_ORIGINAL_READ_WORLD_SQUAD_COUNT",
            side_effect=AssertionError("fallback must not run before silence horizon"),
        ):
            self.assertFalse(v30._poll_squad_count(engine, now=100.0))

    def test_active_rally_transition_prevents_fallback(self):
        engine = self._engine(_rally_hot_entry_latched=True)

        with patch.object(v30, "_ORIGINAL_POLL_SQUAD_COUNT", return_value=False), patch.object(
            v30,
            "_ORIGINAL_READ_WORLD_SQUAD_COUNT",
            side_effect=AssertionError("fallback must not read during active Rally"),
        ):
            self.assertFalse(v30._poll_squad_count(engine, now=100.0))

    def test_non_all_busy_cache_prevents_fallback(self):
        engine = self._engine(
            _rally_v9_team_states={1: RALLY_TEAM_BUSY, 2: "IDLE", 3: RALLY_TEAM_BUSY}
        )

        with patch.object(v30, "_ORIGINAL_POLL_SQUAD_COUNT", return_value=False), patch.object(
            v30,
            "_ORIGINAL_READ_WORLD_SQUAD_COUNT",
            side_effect=AssertionError("fallback is all-busy-only"),
        ):
            self.assertFalse(v30._poll_squad_count(engine, now=100.0))

    def test_normal_reader_records_successful_sample_without_changing_value(self):
        engine = self._engine()

        with patch.object(v30, "_ORIGINAL_READ_WORLD_SQUAD_COUNT", return_value=2), patch.object(
            v30.time, "monotonic", return_value=55.0
        ):
            result = v30._read_world_squad_count(engine)

        self.assertEqual(result, 2)
        self.assertEqual(engine._rally_v30_last_count_sample_at, 55.0)
        self.assertEqual(engine._rally_v30_last_sample_value, 2)

    def test_two_team_path_delegates_without_fallback(self):
        engine = self._engine(_rally_hot_path_three_team=False)

        with patch.object(v30, "_ORIGINAL_POLL_SQUAD_COUNT", return_value=True) as poll, patch.object(
            v30,
            "_ORIGINAL_READ_WORLD_SQUAD_COUNT",
            side_effect=AssertionError("three-team fallback must not run"),
        ):
            result = v30._poll_squad_count(engine, now=100.0)

        self.assertTrue(result)
        poll.assert_called_once_with(engine, now=100.0)


if __name__ == "__main__":
    unittest.main()
