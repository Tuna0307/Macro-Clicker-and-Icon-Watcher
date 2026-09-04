import unittest
from types import SimpleNamespace
from unittest.mock import patch

from macro_clicker import rally_hot_path_v27_runtime as v27


class RallyHotPathV27RuntimeTests(unittest.TestCase):
    @staticmethod
    def _engine(**overrides):
        values = {
            "_rally_hot_path_three_team": True,
            "_rally_hot_entry_latched": False,
            "_rally_v27_entry_click_at": None,
            "_rally_v27_first_join_scan_logged": False,
            "_rally_v27_probe_skip_logged": False,
            "log_messages": [],
        }
        values.update(overrides)
        engine = SimpleNamespace(**values)
        engine.log = engine.log_messages.append
        return engine

    def test_latched_three_team_probe_is_skipped_without_delegating(self):
        engine = self._engine(_rally_hot_entry_latched=True)
        step = SimpleNamespace(name=v27.PROBE_STEP_NAME)

        with patch.object(
            v27,
            "_ORIGINAL_EVALUATE_STEP",
            side_effect=AssertionError("latched world-map probe must be bypassed"),
        ):
            result = v27._evaluate_step(engine, step)

        self.assertEqual(result, (False, {}, {}))
        self.assertTrue(engine._rally_v27_probe_skip_logged)
        self.assertTrue(
            any(
                "skipping world-map fixed-team probe" in line
                for line in engine.log_messages
            )
        )

    def test_unlatched_probe_delegates_unchanged(self):
        engine = self._engine()
        step = SimpleNamespace(name=v27.PROBE_STEP_NAME)
        expected = (True, {0: (1, 2)}, {0: []})

        with patch.object(
            v27,
            "_ORIGINAL_EVALUATE_STEP",
            return_value=expected,
        ) as delegated:
            result = v27._evaluate_step(engine, step, frame_cache={"x": 1})

        self.assertIs(result, expected)
        delegated.assert_called_once()

    def test_two_team_path_is_unchanged(self):
        engine = self._engine(
            _rally_hot_path_three_team=False,
            _rally_hot_entry_latched=True,
        )
        step = SimpleNamespace(name=v27.PROBE_STEP_NAME)
        expected = (True, {}, {})

        with patch.object(
            v27,
            "_ORIGINAL_EVALUATE_STEP",
            return_value=expected,
        ) as delegated:
            result = v27._evaluate_step(engine, step)

        self.assertIs(result, expected)
        delegated.assert_called_once()

    def test_successful_entry_click_records_latency_origin(self):
        engine = self._engine()
        step = SimpleNamespace(name=v27.ENTRY_STEP_NAME)
        action = SimpleNamespace(type="click")

        with patch.object(v27, "_ORIGINAL_RUN_ACTION", return_value=True), patch.object(
            v27.time, "monotonic", return_value=100.0
        ):
            result = v27._run_action(engine, step, action, {}, {})

        self.assertTrue(result)
        self.assertEqual(engine._rally_v27_entry_click_at, 100.0)
        self.assertFalse(engine._rally_v27_first_join_scan_logged)

    def test_first_join_scan_logs_latency_but_preserves_result(self):
        engine = self._engine(
            _rally_hot_entry_latched=True,
            _rally_v27_entry_click_at=100.0,
        )
        step = SimpleNamespace(name=v27.JOINING_STEP_NAME)
        expected = (False, {0: (1, 2)}, {0: []})

        with patch.object(v27.time, "monotonic", return_value=100.42), patch.object(
            v27, "_ORIGINAL_EVALUATE_STEP", return_value=expected
        ):
            result = v27._evaluate_step(engine, step)

        self.assertIs(result, expected)
        self.assertTrue(engine._rally_v27_first_join_scan_logged)
        self.assertTrue(
            any(
                "0.420s after Rally-icon click" in line
                for line in engine.log_messages
            )
        )


if __name__ == "__main__":
    unittest.main()
