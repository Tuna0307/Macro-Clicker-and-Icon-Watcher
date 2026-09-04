import unittest
from types import SimpleNamespace
from unittest.mock import patch

from macro_clicker import rally_hot_path_v11_runtime as v11
from macro_clicker import rally_hot_path_v20_runtime as v20


class RallyHotPathV20RuntimeTests(unittest.TestCase):
    @staticmethod
    def _engine(*, three_team=True, guard_until=15.0):
        engine = SimpleNamespace(
            _rally_hot_path_three_team=three_team,
            _rally_join_guard_until=guard_until,
            _rally_v9_expect_rally_since=None,
            _pending_rally_level=None,
            log_messages=[],
        )
        engine.log = engine.log_messages.append
        return engine

    def test_three_team_final_abort_clears_previous_transition_guard(self):
        engine = self._engine()

        def original(candidate, action, result, reason):
            self.assertIs(candidate, engine)
            self.assertEqual(candidate._rally_join_guard_until, 15.0)
            return "aborted"

        with patch.object(
            v20,
            "_ORIGINAL_ABORT_THREE_TEAM_DISPATCH",
            new=original,
        ):
            outcome = v20._abort_three_team_dispatch(
                engine,
                SimpleNamespace(),
                {},
                "no capable idle Team",
            )

        self.assertEqual(outcome, "aborted")
        self.assertEqual(engine._rally_join_guard_until, 0.0)
        self.assertTrue(
            any("[rally-v20]" in line for line in engine.log_messages)
        )

    def test_legacy_non_three_team_path_keeps_existing_guard(self):
        engine = self._engine(three_team=False)

        with patch.object(
            v20,
            "_ORIGINAL_ABORT_THREE_TEAM_DISPATCH",
            return_value="legacy",
        ):
            outcome = v20._abort_three_team_dispatch(
                engine,
                SimpleNamespace(),
                {},
                "legacy",
            )

        self.assertEqual(outcome, "legacy")
        self.assertEqual(engine._rally_join_guard_until, 15.0)
        self.assertFalse(engine.log_messages)

    def test_next_entry_watch_is_not_disarmed_by_previous_workflow_guard(self):
        engine = self._engine()
        engine._rally_v9_expect_rally_since = 20.0

        with patch.object(
            v20,
            "_ORIGINAL_ABORT_THREE_TEAM_DISPATCH",
            return_value=True,
        ):
            v20._abort_three_team_dispatch(
                engine,
                SimpleNamespace(),
                {},
                "no capable idle Team",
            )

        calls = []

        def original_watch(candidate, now=None):
            calls.append((candidate, now))
            return True

        with patch.object(v11, "_ORIGINAL_V9_WATCH", new=original_watch):
            recovered = v11._guarded_entry_watch(engine, now=20.5)

        self.assertTrue(recovered)
        self.assertEqual(calls, [(engine, 20.5)])
        self.assertEqual(engine._rally_v9_expect_rally_since, 20.0)


if __name__ == "__main__":
    unittest.main()
