import unittest
from unittest.mock import patch

from macro_clicker import rally_hot_path_runtime as hot
from macro_clicker import rally_hot_path_v8_runtime as v8
from macro_clicker.engine import MacroEngine
from macro_clicker.models import Action, ImageCondition, Step, load_scenario


class RallyHotPathV8RuntimeTests(unittest.TestCase):
    def _engine(self, scenario_name="Rally gold mob_ 3 team"):
        engine = object.__new__(MacroEngine)
        engine.scenario = load_scenario(scenario_name)
        engine.log_messages = []
        engine.log = engine.log_messages.append
        engine._rally_hot_path_three_team = scenario_name.endswith("3 team")
        engine._rally_v8_dispatch_wait_key = None
        engine._pending_rally_level = 35
        engine._pending_rally_team_selected = {"level": 35, "team": 3}
        engine._abort_current_step = False
        engine._cleanup_after_abort = False
        engine._retry_current_step = False
        engine._rally_hot_entry_latched = True
        engine._rally_hot_profile_armed = False
        engine._rally_hot_base_armed = False
        engine._rally_join_guard_until = 1.0
        engine._window_rect_lookup_cache = {}
        engine._stop_requested = lambda: False
        return engine

    def test_three_team_entry_wait_matches_mature_two_team_pacing(self):
        engine = self._engine()
        sleeps = []
        engine._sleep_until_stop = lambda seconds: sleeps.append(seconds) or False
        step = Step(name="Enter Rally after team probe")
        action = Action(type="wait", seconds=0.5)

        self.assertTrue(MacroEngine._run_action(engine, step, action, {}, {}))

        self.assertEqual(sleeps, [0.3])
        self.assertTrue(
            any("mob2-paced Rally entry settle" in line for line in engine.log_messages)
        )

    @staticmethod
    def _attack_step():
        selector = Action(type="select_rally_team", on_condition_index=0)
        wait = Action(type="wait", seconds=1.0, seconds_max=1.5)
        click = Action(type="click", on_condition_index=0)
        cleanup = Action(type="set_step", step_name="Attack Confirm", set_enabled=False)
        step = Step(
            name="Attack Confirm",
            conditions=[ImageCondition(template_path="templates/Attack.png")],
            actions=[selector, wait, click, cleanup],
        )
        return step, selector, wait, click

    def test_final_dispatch_waits_once_then_fresh_checks_attack_only(self):
        engine = self._engine()
        step, selector, wait_action, click_action = self._attack_step()
        engine._sleep_until_stop = lambda _seconds: False
        engine._evaluate_condition = lambda *_args, **_kwargs: (
            True,
            [
                {
                    "center": (962, 808),
                    "confidence": 0.99,
                    "scale": 1.0,
                    "scale_x": 1.0,
                    "scale_y": 1.0,
                }
            ],
        )
        base_actions = []

        def base_run(_engine, _step, action, _points, _matches):
            base_actions.append(action)
            return True

        with patch.object(hot, "_ORIGINAL_RUN_ACTION", side_effect=base_run):
            self.assertTrue(
                v8._complete_three_team_dispatch(engine, step, selector, {}, {})
            )

        self.assertEqual(base_actions, [wait_action, click_action])
        self.assertTrue(engine._abort_current_step)
        self.assertTrue(engine._cleanup_after_abort)
        self.assertFalse(engine._retry_current_step)
        self.assertFalse(engine._rally_hot_entry_latched)
        self.assertIsNone(engine._rally_v8_dispatch_wait_key)
        self.assertTrue(
            any("fresh Attack revalidated" in line for line in engine.log_messages)
        )
        self.assertTrue(
            any("dispatch committed" in line for line in engine.log_messages)
        )

    def test_unproven_fresh_attack_never_sends_attack_click(self):
        engine = self._engine()
        step, selector, wait_action, _click_action = self._attack_step()
        engine._sleep_until_stop = lambda _seconds: False
        engine._evaluate_condition = lambda *_args, **_kwargs: (False, [])
        base_actions = []

        def base_run(_engine, _step, action, _points, _matches):
            base_actions.append(action)
            return True

        with (
            patch.object(hot, "_ORIGINAL_RUN_ACTION", side_effect=base_run),
            patch.object(v8, "FRESH_ATTACK_RETRY_WINDOW_SECONDS", 0.0),
        ):
            self.assertFalse(
                v8._complete_three_team_dispatch(engine, step, selector, {}, {})
            )

        self.assertEqual(base_actions, [wait_action])
        self.assertTrue(engine._retry_current_step)
        self.assertFalse(engine._abort_current_step)
        self.assertTrue(engine._rally_hot_entry_latched)
        self.assertTrue(
            any("no Attack click sent" in line for line in engine.log_messages)
        )

    def test_two_team_path_is_not_given_v8_special_handling(self):
        engine = self._engine("Rally gold mob_ 2 team")
        step = Step(name="Enter Rally after team probe")
        action = Action(type="wait", seconds=0.5)
        sentinel = object()

        original = v8._ORIGINAL_RUN_ACTION
        try:
            v8._ORIGINAL_RUN_ACTION = lambda *_args, **_kwargs: sentinel
            result = MacroEngine._run_action(engine, step, action, {}, {})
        finally:
            v8._ORIGINAL_RUN_ACTION = original

        self.assertIs(result, sentinel)


if __name__ == "__main__":
    unittest.main()
