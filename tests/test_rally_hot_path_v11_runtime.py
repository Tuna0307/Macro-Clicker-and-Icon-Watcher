import unittest
from types import SimpleNamespace
from unittest.mock import patch

from macro_clicker import rally_hot_path_v11_runtime as v11


class RallyHotPathV11RuntimeTests(unittest.TestCase):
    @staticmethod
    def _engine(**overrides):
        values = {
            "_rally_hot_path_three_team": True,
            "_rally_v9_expect_rally_since": 10.0,
            "_pending_rally_level": None,
            "_rally_join_guard_until": 0.0,
            "log_messages": [],
        }
        values.update(overrides)
        engine = SimpleNamespace(**values)
        engine.log = engine.log_messages.append
        return engine

    def test_pending_rally_level_disarms_entry_watch_before_formation(self):
        engine = self._engine(_pending_rally_level=65)

        with patch.object(
            v11,
            "_ORIGINAL_V9_WATCH",
            side_effect=AssertionError("v9 entry recovery must not run"),
        ):
            recovered = v11._guarded_entry_watch(engine, now=11.5)

        self.assertFalse(recovered)
        self.assertIsNone(engine._rally_v9_expect_rally_since)
        self.assertTrue(any("entry watchdog disarmed" in line for line in engine.log_messages))

    def test_active_attack_open_guard_disarms_entry_watch(self):
        engine = self._engine(_rally_join_guard_until=12.5)

        with patch.object(
            v11,
            "_ORIGINAL_V9_WATCH",
            side_effect=AssertionError("v9 entry recovery must not run"),
        ):
            recovered = v11._guarded_entry_watch(engine, now=11.5)

        self.assertFalse(recovered)
        self.assertIsNone(engine._rally_v9_expect_rally_since)

    def test_true_entry_phase_still_delegates_to_v9_recovery(self):
        engine = self._engine()
        calls = []

        def original(candidate, now=None):
            calls.append((candidate, now))
            return True

        with patch.object(v11, "_ORIGINAL_V9_WATCH", new=original):
            recovered = v11._guarded_entry_watch(engine, now=11.5)

        self.assertTrue(recovered)
        self.assertEqual(calls, [(engine, 11.5)])
        self.assertEqual(engine._rally_v9_expect_rally_since, 10.0)

    def test_clear_entry_watch_is_idempotent(self):
        engine = self._engine()

        self.assertTrue(v11._clear_entry_watch(engine, "Rally page proven"))
        self.assertFalse(v11._clear_entry_watch(engine, "Rally page proven"))
        self.assertEqual(len(engine.log_messages), 1)


if __name__ == "__main__":
    unittest.main()
