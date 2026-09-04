import unittest
from types import SimpleNamespace
from unittest.mock import patch

from macro_clicker import rally_hot_path_v17_runtime as v17


class RallyHotPathV17RuntimeTests(unittest.TestCase):
    def setUp(self):
        self._dismiss = v17._ORIGINAL_DISMISS_FIXED_PANEL
        self._evaluate = v17._ORIGINAL_EVALUATE_STEP
        self._run_action = v17._ORIGINAL_RUN_ACTION

    def tearDown(self):
        v17._ORIGINAL_DISMISS_FIXED_PANEL = self._dismiss
        v17._ORIGINAL_EVALUATE_STEP = self._evaluate
        v17._ORIGINAL_RUN_ACTION = self._run_action

    @staticmethod
    def _engine(*, three_team=True, profile_armed=False):
        engine = SimpleNamespace(
            _rally_hot_path_three_team=three_team,
            _rally_hot_profile_armed=profile_armed,
            _rally_v17_profile_recovery_until=0.0,
            _rally_v17_profile_recovery_owned=False,
            log_messages=[],
        )
        engine.log = engine.log_messages.append
        return engine

    def test_three_team_dismiss_arms_profile_before_click_and_keeps_it_armed(self):
        engine = self._engine()
        observed = []

        def dismiss(inner, result, button):
            observed.append(
                (
                    inner._rally_hot_profile_armed,
                    inner._rally_v17_profile_recovery_owned,
                    button,
                    result,
                )
            )
            return True

        v17._ORIGINAL_DISMISS_FIXED_PANEL = dismiss
        with patch.object(v17.time, "monotonic", return_value=100.0):
            clicked = v17._dismiss_fixed_rally_team_panel(
                engine, {"screen_valid": True}, "left"
            )

        self.assertTrue(clicked)
        self.assertEqual(observed[0][0], True)
        self.assertEqual(observed[0][1], True)
        self.assertTrue(engine._rally_hot_profile_armed)
        self.assertEqual(engine._rally_v17_profile_recovery_until, 103.0)
        self.assertTrue(
            any("fixed-panel outside dismissal" in line for line in engine.log_messages)
        )

    def test_failed_dismiss_restores_previous_disarmed_state(self):
        engine = self._engine(profile_armed=False)
        v17._ORIGINAL_DISMISS_FIXED_PANEL = lambda *_args, **_kwargs: False

        with patch.object(v17.time, "monotonic", return_value=10.0):
            clicked = v17._dismiss_fixed_rally_team_panel(engine, {}, "left")

        self.assertFalse(clicked)
        self.assertFalse(engine._rally_hot_profile_armed)
        self.assertFalse(engine._rally_v17_profile_recovery_owned)
        self.assertEqual(engine._rally_v17_profile_recovery_until, 0.0)

    def test_failed_dismiss_does_not_erase_preexisting_profile_gate(self):
        engine = self._engine(profile_armed=True)
        v17._ORIGINAL_DISMISS_FIXED_PANEL = lambda *_args, **_kwargs: False

        with patch.object(v17.time, "monotonic", return_value=10.0):
            clicked = v17._dismiss_fixed_rally_team_panel(engine, {}, "left")

        self.assertFalse(clicked)
        self.assertTrue(engine._rally_hot_profile_armed)
        self.assertFalse(engine._rally_v17_profile_recovery_owned)
        self.assertEqual(engine._rally_v17_profile_recovery_until, 0.0)

    def test_two_team_dismiss_is_unchanged(self):
        engine = self._engine(three_team=False)
        calls = []

        def dismiss(inner, result, button):
            calls.append((inner, result, button))
            return True

        v17._ORIGINAL_DISMISS_FIXED_PANEL = dismiss
        clicked = v17._dismiss_fixed_rally_team_panel(engine, {"x": 1}, "right")

        self.assertTrue(clicked)
        self.assertEqual(len(calls), 1)
        self.assertFalse(engine._rally_hot_profile_armed)
        self.assertEqual(engine._rally_v17_profile_recovery_until, 0.0)

    def test_owned_profile_gate_expires_before_profile_evaluation(self):
        engine = self._engine(profile_armed=True)
        engine._rally_v17_profile_recovery_owned = True
        engine._rally_v17_profile_recovery_until = 12.0
        seen = []

        def evaluate(inner, step, frame_cache=None):
            seen.append(inner._rally_hot_profile_armed)
            return False, {}, {}

        v17._ORIGINAL_EVALUATE_STEP = evaluate
        step = SimpleNamespace(name="MisClick Profile")

        # Exercise the installed wrapper logic through a local equivalent of the
        # closure's expiry boundary: v17 owns the temporary gate, so expiry is
        # allowed to turn it off before the existing evaluator runs.
        with patch.object(v17.time, "monotonic", return_value=13.0):
            owned = bool(engine._rally_v17_profile_recovery_owned)
            deadline = float(engine._rally_v17_profile_recovery_until)
            if owned and deadline > 0.0 and v17.time.monotonic() > deadline:
                engine._rally_hot_profile_armed = False
                v17._clear_owned_profile_window(engine)
            result = v17._ORIGINAL_EVALUATE_STEP(engine, step, frame_cache=None)

        self.assertEqual(result, (False, {}, {}))
        self.assertEqual(seen, [False])
        self.assertFalse(engine._rally_v17_profile_recovery_owned)
        self.assertEqual(engine._rally_v17_profile_recovery_until, 0.0)

    def test_successful_profile_click_clears_v17_window(self):
        engine = self._engine(profile_armed=True)
        engine._rally_v17_profile_recovery_owned = True
        engine._rally_v17_profile_recovery_until = 99.0
        v17._clear_owned_profile_window(engine)

        self.assertFalse(engine._rally_v17_profile_recovery_owned)
        self.assertEqual(engine._rally_v17_profile_recovery_until, 0.0)


if __name__ == "__main__":
    unittest.main()
