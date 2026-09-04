import unittest
from types import SimpleNamespace
from unittest.mock import patch

from macro_clicker import rally_hot_path_v31_runtime as v31


class RallyHotPathV31RuntimeTests(unittest.TestCase):
    @staticmethod
    def _engine(**overrides):
        values = {
            "_rally_hot_path_three_team": True,
            "_rally_v17_profile_recovery_owned": True,
            "_rally_hot_profile_armed": True,
            "_rally_v17_profile_recovery_until": 103.0,
            "_rally_v31_last_profile_trace": 0.0,
            "log_messages": [],
        }
        values.update(overrides)
        engine = SimpleNamespace(**values)
        engine.log = engine.log_messages.append
        return engine

    def test_active_profile_window_blocks_new_rally_entry(self):
        engine = self._engine()
        step = SimpleNamespace(name="Enter Rally after team probe")

        with patch.object(v31.time, "monotonic", return_value=100.0), patch.object(
            v31,
            "_ORIGINAL_EVALUATE_STEP",
            side_effect=AssertionError("Rally entry must not evaluate while profile recovery owns input"),
        ):
            result = v31._evaluate_step(engine, step)

        self.assertEqual(result, (False, {}, {}))
        self.assertTrue(any("new Rally entry blocked" in line for line in engine.log_messages))

    def test_profile_step_still_delegates_during_owned_window(self):
        engine = self._engine()
        step = SimpleNamespace(name="MisClick Profile")
        expected = (True, {0: (1, 2)}, {0: []})

        with patch.object(v31.time, "monotonic", return_value=100.0), patch.object(
            v31, "_ORIGINAL_EVALUATE_STEP", return_value=expected
        ) as delegated:
            result = v31._evaluate_step(engine, step, frame_cache={"frame": 1})

        self.assertIs(result, expected)
        delegated.assert_called_once()
        self.assertTrue(any("profile recovery probe READY" in line for line in engine.log_messages))

    def test_expired_profile_window_does_not_block_entry(self):
        engine = self._engine(_rally_v17_profile_recovery_until=99.0)
        step = SimpleNamespace(name="Enter Rally after team probe")
        expected = (True, {}, {})

        with patch.object(v31.time, "monotonic", return_value=100.0), patch.object(
            v31, "_ORIGINAL_EVALUATE_STEP", return_value=expected
        ) as delegated:
            result = v31._evaluate_step(engine, step)

        self.assertIs(result, expected)
        delegated.assert_called_once()

    def test_stale_entry_click_is_suppressed_while_profile_window_active(self):
        engine = self._engine()
        step = SimpleNamespace(name="Enter Rally after team probe")
        action = SimpleNamespace(type="click")

        with patch.object(v31.time, "monotonic", return_value=100.0), patch.object(
            v31,
            "_ORIGINAL_RUN_ACTION",
            side_effect=AssertionError("stale Rally click must be suppressed"),
        ):
            result = v31._run_action(engine, step, action, {}, {})

        self.assertFalse(result)
        self.assertTrue(engine._rally_hot_profile_armed)

    def test_two_team_path_delegates_unchanged(self):
        engine = self._engine(_rally_hot_path_three_team=False)
        step = SimpleNamespace(name="Enter Rally after team probe")
        expected = (True, {}, {})

        with patch.object(v31, "_ORIGINAL_EVALUATE_STEP", return_value=expected) as delegated:
            result = v31._evaluate_step(engine, step)

        self.assertIs(result, expected)
        delegated.assert_called_once()


if __name__ == "__main__":
    unittest.main()
