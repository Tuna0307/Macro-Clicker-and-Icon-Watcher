import unittest
from types import SimpleNamespace
from unittest.mock import patch

from macro_clicker import rally_hot_path_v24_runtime as v24
from macro_clicker.rally_team_policy import RALLY_TEAM_BUSY, RALLY_TEAM_IDLE


class RallyHotPathV24RuntimeTests(unittest.TestCase):
    @staticmethod
    def _engine(**overrides):
        values = {
            "_rally_hot_path_three_team": True,
            "_rally_hot_entry_latched": False,
            "_pending_rally_level": None,
            "_pending_rally_team_selected": None,
            "_rally_v9_team_cache_valid": True,
            "_rally_v9_team_states": {
                1: RALLY_TEAM_BUSY,
                2: RALLY_TEAM_IDLE,
                3: RALLY_TEAM_BUSY,
            },
            "_rally_v9_team_cache_captured_at": 90.0,
            "_rally_v9_last_squad_count": 2,
            "_rally_v9_expected_squad_count": None,
            "_rally_v9_expected_count_since": 0.0,
            "_rally_v9_expect_rally_since": None,
            "_rally_v12_pending_squad_count": None,
            "_rally_v12_pending_squad_since": 0.0,
            "_rally_join_guard_until": 0.0,
            "_rally_v19_probe_until": 0.0,
            "_rally_v22_full_squad_hold_until": 0.0,
            "_rally_v23_entry_not_before": 0.0,
            "_rally_hot_base_armed": False,
            "_rally_hot_profile_armed": False,
            "_abort_current_step": False,
            "_retry_current_step": False,
            "_cleanup_after_abort": False,
            "_rally_v24_last_state_key": None,
            "_rally_v24_last_heartbeat": 0.0,
            "log_messages": [],
        }
        values.update(overrides)
        engine = SimpleNamespace(**values)
        engine.log = engine.log_messages.append
        return engine

    def test_state_text_contains_core_reconciliation_fields(self):
        engine = self._engine(
            _pending_rally_level=75,
            _rally_v9_expected_squad_count=2,
            _rally_v9_expected_count_since=95.0,
            _rally_v12_pending_squad_count=1,
            _rally_v12_pending_squad_since=98.0,
            _rally_join_guard_until=101.0,
            _rally_v23_entry_not_before=100.5,
        )

        text = v24._format_state(engine, now=100.0)

        self.assertIn("pending_level=75", text)
        self.assertIn("T1=BUSY T2=IDLE T3=BUSY busy=2", text)
        self.assertIn("sidebar=2/3", text)
        self.assertIn("expected=2/3", text)
        self.assertIn("candidate=1/3", text)
        self.assertIn("join_guard_rem=1.00s", text)
        self.assertIn("reentry_rem=0.50s", text)

    def test_state_trace_logs_change_then_throttles_duplicate(self):
        engine = self._engine()

        self.assertTrue(v24._trace_state(engine, "first", now=100.0))
        self.assertFalse(v24._trace_state(engine, "duplicate", now=100.5))

        engine._pending_rally_level = 65
        self.assertTrue(v24._trace_state(engine, "changed", now=100.6))

        self.assertEqual(len(engine.log_messages), 2)
        self.assertIn("state:change:first", engine.log_messages[0])
        self.assertIn("state:change:changed", engine.log_messages[1])

    def test_count_trace_delegates_without_changing_result(self):
        engine = self._engine()

        def original(target, count, now=None):
            self.assertIs(target, engine)
            self.assertEqual(count, 1)
            self.assertEqual(now, 123.0)
            target._rally_v9_last_squad_count = count
            return True

        with patch.object(v24, "_ORIGINAL_OBSERVE_SQUAD_COUNT", new=original):
            result = v24._observe_squad_count(engine, 1, now=123.0)

        self.assertTrue(result)
        self.assertEqual(engine._rally_v9_last_squad_count, 1)
        self.assertTrue(any("[rally-v24][count]" in line for line in engine.log_messages))

    def test_dispatch_trace_delegates_without_rewriting_state(self):
        engine = self._engine()
        calls = []

        def original(target, team_number, now=None):
            calls.append((target, team_number, now))
            target._rally_v9_team_states = {
                1: RALLY_TEAM_BUSY,
                2: RALLY_TEAM_BUSY,
                3: RALLY_TEAM_BUSY,
            }
            return "sentinel"

        with patch.object(v24, "_ORIGINAL_MARK_DISPATCHED_TEAM_BUSY", new=original):
            result = v24._mark_dispatched_team_busy(engine, 2, now=200.0)

        self.assertEqual(result, "sentinel")
        self.assertEqual(calls, [(engine, 2, 200.0)])
        self.assertEqual(engine._rally_v9_team_states[2], RALLY_TEAM_BUSY)
        self.assertTrue(
            any("dispatch-cache:before" in line for line in engine.log_messages)
        )
        self.assertTrue(
            any("dispatch-cache:after" in line for line in engine.log_messages)
        )

    def test_formation_text_includes_each_idle_score(self):
        text = v24._formation_text(
            {
                "screen_valid": True,
                "error": None,
                "states": {
                    1: RALLY_TEAM_BUSY,
                    2: RALLY_TEAM_IDLE,
                    3: RALLY_TEAM_BUSY,
                },
                "idle_scores": {1: 0.1, 2: 0.99, 3: 0.2},
            }
        )

        self.assertIn("screen_valid=1", text)
        self.assertIn("T1=BUSY idle_score=0.100", text)
        self.assertIn("T2=IDLE idle_score=0.990", text)
        self.assertIn("T3=BUSY idle_score=0.200", text)

    def test_two_team_trace_is_silent(self):
        engine = self._engine(_rally_hot_path_three_team=False)

        self.assertFalse(v24._trace_state(engine, "two-team", force=True, now=100.0))
        self.assertEqual(engine.log_messages, [])


if __name__ == "__main__":
    unittest.main()
