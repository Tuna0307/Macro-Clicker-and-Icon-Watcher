import queue
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from macro_clicker import app as app_module
from macro_clicker.hotkeys import (
    canonical_hotkey,
    find_hotkey_conflicts,
    hotkeys_conflict,
    permissive_single_key_conflict,
)
from macro_clicker.models import (
    Action,
    ImageCondition,
    Scenario,
    Step,
    validate_scenario,
)


class HotkeySafetyTests(unittest.TestCase):
    def test_aliases_resolve_to_the_same_physical_hotkey(self):
        self.assertEqual(
            canonical_hotkey("ctrl+f8"),
            canonical_hotkey("control+f8"),
        )
        self.assertTrue(hotkeys_conflict("ctrl+f8", "control+f8"))

    def test_modifier_order_resolves_to_the_same_physical_hotkey(self):
        self.assertEqual(
            canonical_hotkey("ctrl+shift+f8"),
            canonical_hotkey("shift+ctrl+f8"),
        )

    def test_generic_modifier_conflicts_with_accepted_sided_modifier(self):
        self.assertTrue(hotkeys_conflict("ctrl+f8", "left ctrl+f8"))
        self.assertTrue(hotkeys_conflict("shift+f8, f9", "right shift+f8"))

    def test_extra_modifier_creates_a_distinct_exact_keyboard_chord(self):
        self.assertFalse(hotkeys_conflict("f8", "ctrl+f8"))
        self.assertFalse(hotkeys_conflict("ctrl+f8", "shift+f8"))

    def test_complete_hotkey_cannot_prefix_another_registered_sequence(self):
        self.assertTrue(hotkeys_conflict("f8", "f8, f9"))
        self.assertTrue(hotkeys_conflict("control+f8, f9", "ctrl+f8"))
        self.assertFalse(hotkeys_conflict("f8, f9", "f8, f10"))

    def test_duplicate_key_inside_chord_is_rejected_but_sequence_is_valid(self):
        with self.assertRaisesRegex(ValueError, "cannot repeat"):
            canonical_hotkey("f12+f12")

        self.assertEqual(len(canonical_hotkey("f12, f12")), 2)

    def test_distinct_sided_modifiers_survive_duplicate_parser_expansion(self):
        canonical = canonical_hotkey("left ctrl+right ctrl")

        self.assertEqual(len(canonical), 1)
        self.assertTrue(canonical[0])
        self.assertTrue(all(len(combination) == 2 for combination in canonical[0]))

    def test_permissive_single_key_stop_overlaps_modified_binding(self):
        self.assertTrue(permissive_single_key_conflict("f12", "ctrl+f12"))
        self.assertTrue(permissive_single_key_conflict("f12", "f8, shift+f12"))
        self.assertFalse(permissive_single_key_conflict("f12", "ctrl+f8"))

    def test_scenario_rejects_impossible_duplicate_kill_switch(self):
        with self.assertRaisesRegex(ValueError, "cannot repeat"):
            validate_scenario(
                Scenario(
                    name="Impossible stop",
                    start_hotkey="f8",
                    kill_switch="f12+f12",
                )
            )

    def test_single_key_stop_conflicts_with_modified_start(self):
        with self.assertRaisesRegex(ValueError, "overlapping physical key sequences"):
            validate_scenario(
                Scenario(
                    name="Permissive stop",
                    start_hotkey="ctrl+f12",
                    kill_switch="f12",
                )
            )

    def test_scenario_rejects_physical_start_stop_collision(self):
        for start, stop in (
            ("ctrl+f8", "control+f8"),
            ("ctrl+shift+f8", "shift+ctrl+f8"),
            ("f8", "f8, f9"),
        ):
            with self.subTest(start=start, stop=stop):
                with self.assertRaisesRegex(
                    ValueError, "overlapping physical key sequences"
                ):
                    validate_scenario(
                        Scenario(
                            name="Conflicting keys",
                            start_hotkey=start,
                            kill_switch=stop,
                        )
                    )

    def test_conflict_helper_can_combine_independent_hotkey_owners(self):
        conflicts = find_hotkey_conflicts(
            (
                ("Macro start", "ctrl+f8"),
                ("Alert toggle", "control+f8, f9"),
                ("Test alert", "ctrl+f9"),
            )
        )

        self.assertEqual(conflicts, [("Macro start", "Alert toggle")])


class JsonSchemaSafetyTests(unittest.TestCase):
    def test_misspelled_condition_field_is_rejected_with_suggestion(self):
        with self.assertRaisesRegex(ValueError, r"unknown field: 'negated'.*'negate'"):
            ImageCondition.from_dict(
                {"template_path": "templates/icon.png", "negated": True}
            )

    def test_misspelled_action_field_is_rejected_with_suggestion(self):
        with self.assertRaisesRegex(
            ValueError, r"unknown field: 'set_enable'.*'set_enabled'"
        ):
            Action.from_dict(
                {
                    "type": "set_step",
                    "step_name": "Next",
                    "set_enable": False,
                }
            )

    def test_unknown_step_and_scenario_fields_are_rejected(self):
        with self.assertRaisesRegex(ValueError, r"step has unknown field"):
            Step.from_dict({"name": "Step", "conditons": []})
        with self.assertRaisesRegex(ValueError, r"scenario has unknown field"):
            Scenario.from_dict({"name": "Scenario", "step": []})

    def test_click_target_cannot_reference_a_negated_condition(self):
        scenario = Scenario(
            name="Negated target",
            steps=[
                Step(
                    name="Click",
                    conditions=[
                        ImageCondition(
                            template_path="templates/absent.png",
                            negate=True,
                        )
                    ],
                    actions=[Action(type="click", on_condition_index=0)],
                )
            ],
        )

        with self.assertRaisesRegex(
            ValueError,
            r"on_condition_index references negated condition #1",
        ):
            validate_scenario(scenario)

    def test_matching_row_fallback_cannot_reference_a_negated_condition(self):
        scenario = Scenario(
            name="Negated fallback",
            steps=[
                Step(
                    name="Rows",
                    conditions=[
                        ImageCondition(template_path="templates/reference.png"),
                        ImageCondition(template_path="templates/target.png"),
                        ImageCondition(
                            template_path="templates/absent.png",
                            negate=True,
                        ),
                    ],
                    actions=[
                        Action(
                            type="click_matching_row",
                            match_condition_index=0,
                            on_condition_index=1,
                            no_match_condition_index=2,
                        )
                    ],
                )
            ],
        )

        with self.assertRaisesRegex(
            ValueError,
            r"no_match_condition_index references negated condition #3",
        ):
            validate_scenario(scenario)


class AppStartupAndSettingsSafetyTests(unittest.TestCase):
    @staticmethod
    def _app_with_alert_hotkeys(toggle="ctrl+shift+f8", test="ctrl+shift+f9"):
        ui = object.__new__(app_module.App)
        ui.alert_tab = SimpleNamespace(
            settings=SimpleNamespace(
                start_stop_hotkey=toggle,
                test_alert_hotkey=test,
            )
        )
        return ui

    def test_macro_and_alert_physical_hotkey_collision_is_reported(self):
        ui = self._app_with_alert_hotkeys(toggle="control+f8")

        conflict = ui._macro_alert_hotkey_conflict("ctrl+f8", "f12")

        self.assertIsNotNone(conflict)
        self.assertIn("Macro start and Icon Alerts start/stop", conflict)

    def test_settings_validation_uses_permissive_single_key_stop(self):
        self.assertTrue(app_module._start_stop_hotkeys_conflict("ctrl+f12", "f12"))
        self.assertFalse(app_module._start_stop_hotkeys_conflict("ctrl+f8", "f12"))

    def test_start_registration_check_can_ignore_stop_only_collision(self):
        ui = self._app_with_alert_hotkeys(toggle="control+f12")

        conflict = ui._macro_alert_hotkey_conflict(
            "f8",
            "ctrl+f12",
            include_stop=False,
        )

        self.assertIsNone(conflict)

    def test_initial_window_enumeration_failure_is_queued_before_log_widget_exists(
        self,
    ):
        ui = object.__new__(app_module.App)
        ui.target_window_combo = {}
        ui.log_queue = queue.Queue()

        with patch.object(
            app_module,
            "visible_window_titles",
            side_effect=OSError("enumeration unavailable"),
        ):
            ui._refresh_window_list()

        self.assertIn("could not list windows", ui.log_queue.get_nowait())

    def test_lock_access_error_is_not_reported_as_another_running_copy(self):
        notice = Mock()

        with (
            patch.object(
                app_module.SingleInstanceLock,
                "acquire",
                side_effect=PermissionError("lock directory denied"),
            ),
            patch.object(app_module.tk, "Tk", return_value=notice),
            patch.object(app_module.messagebox, "showwarning") as warning,
            patch.object(app_module.messagebox, "showerror") as error,
        ):
            result = app_module.main()

        self.assertEqual(result, 1)
        warning.assert_not_called()
        self.assertEqual(error.call_args.args[0], "PC Macro Builder could not start")
        self.assertIn("PermissionError: lock directory denied", error.call_args.args[1])
        notice.destroy.assert_called_once_with()

    def test_scenario_settings_are_blocked_while_macro_is_running(self):
        ui = object.__new__(app_module.App)
        ui.root = object()
        ui.engine = type("RunningEngine", (), {"is_running": True})()

        with (
            patch.object(app_module.messagebox, "showwarning") as warning,
            patch.object(app_module.tk, "Toplevel") as toplevel,
        ):
            ui._open_scenario_settings()

        toplevel.assert_not_called()
        self.assertIn(
            "Stop the macro before changing scenario settings",
            warning.call_args.args[1],
        )

    def test_failed_hotkey_replacement_invalidates_the_old_callback(self):
        ui = object.__new__(app_module.App)
        ui.scenario = Scenario(name="Replacement", start_hotkey="ctrl+f9")
        ui.control_queue = queue.Queue()
        ui._start_hotkey_handle = "old-handle"
        old_token = object()
        ui._start_hotkey_registration_token = old_token
        ui._registered_start_hotkey = "f8"
        ui._queue_log = Mock()

        with (
            patch.object(app_module.keyboard, "remove_hotkey"),
            patch.object(
                app_module.keyboard,
                "add_hotkey",
                side_effect=OSError("registration failed"),
            ),
        ):
            self.assertFalse(ui._register_start_hotkey())

        self.assertIsNone(ui._start_hotkey_handle)
        self.assertIsNone(ui._start_hotkey_registration_token)
        ui._request_start_from_hotkey(old_token)
        self.assertTrue(ui.control_queue.empty())

    def test_start_queued_before_hotkey_replacement_is_ignored_on_ui_thread(self):
        ui = object.__new__(app_module.App)
        ui.control_queue = queue.Queue()
        ui.log_queue = queue.Queue()
        ui.engine = None
        ui._engine_ui_active = False
        old_token = object()
        ui._start_hotkey_registration_token = old_token
        ui._registered_start_hotkey = "f8"
        ui.root = SimpleNamespace(after=lambda *_args: None)
        ui._start_engine_from_hotkey = Mock()

        ui._request_start_from_hotkey(old_token)
        ui._start_hotkey_registration_token = object()
        ui._registered_start_hotkey = "ctrl+f9"
        ui._poll_log_queue()

        ui._start_engine_from_hotkey.assert_not_called()

    def test_current_hotkey_token_starts_when_ui_consumes_queued_command(self):
        ui = object.__new__(app_module.App)
        ui.control_queue = queue.Queue()
        ui.log_queue = queue.Queue()
        ui.engine = None
        ui._engine_ui_active = False
        current_token = object()
        ui._start_hotkey_registration_token = current_token
        ui._registered_start_hotkey = "f8"
        ui.root = SimpleNamespace(after=lambda *_args: None)
        ui._start_engine_from_hotkey = Mock()

        ui._request_start_from_hotkey(current_token)
        ui._poll_log_queue()

        ui._start_engine_from_hotkey.assert_called_once_with()

    def test_start_pressed_while_running_cannot_restart_after_stop(self):
        ui = object.__new__(app_module.App)
        ui.control_queue = queue.Queue()
        ui.log_queue = queue.Queue()
        ui.root = SimpleNamespace(after=lambda *_args: None)
        ui.engine = SimpleNamespace(is_running=True, stop=Mock())
        ui._engine_ui_active = True
        ui._set_engine_stopped_ui = Mock()
        current_token = object()
        ui._start_hotkey_registration_token = current_token
        ui._registered_start_hotkey = "f8"
        ui._start_engine_from_hotkey = Mock()

        ui._request_start_from_hotkey(current_token)
        self.assertTrue(ui.control_queue.empty())

        # Model F12 completing before the Tk thread polls its control queue.
        ui.engine.is_running = False
        ui._poll_log_queue()

        ui._start_engine_from_hotkey.assert_not_called()

    def test_start_pressed_during_stopping_transition_is_ignored(self):
        ui = object.__new__(app_module.App)
        ui.control_queue = queue.Queue()
        ui.engine = SimpleNamespace(is_running=False)
        ui._engine_ui_active = True
        current_token = object()
        ui._start_hotkey_registration_token = current_token

        ui._request_start_from_hotkey(current_token)

        self.assertTrue(ui.control_queue.empty())

    def test_duplicate_queued_start_commands_only_start_once(self):
        ui = object.__new__(app_module.App)
        ui.control_queue = queue.Queue()
        ui.log_queue = queue.Queue()
        ui.root = SimpleNamespace(
            after=lambda *_args: None,
            grab_current=lambda: None,
        )
        ui.engine = None
        ui._engine_ui_active = False
        current_token = object()
        ui._start_hotkey_registration_token = current_token
        ui._registered_start_hotkey = "f8"

        def mark_started():
            ui.engine = SimpleNamespace(is_running=True, is_ready=False)
            ui._engine_ui_active = True

        ui._start_engine = Mock(side_effect=mark_started)
        ui._request_start_from_hotkey(current_token)
        ui._request_start_from_hotkey(current_token)

        ui._poll_log_queue()

        ui._start_engine.assert_called_once_with()

    def test_failed_start_attempt_still_coalesces_queued_hotkeys(self):
        ui = object.__new__(app_module.App)
        ui.control_queue = queue.Queue()
        ui.log_queue = queue.Queue()
        ui.root = SimpleNamespace(
            after=lambda *_args: None,
            grab_current=lambda: None,
        )
        ui.engine = None
        ui._engine_ui_active = False
        current_token = object()
        ui._start_hotkey_registration_token = current_token
        ui._registered_start_hotkey = "f8"
        ui._start_engine = Mock()

        ui._request_start_from_hotkey(current_token)
        ui._request_start_from_hotkey(current_token)
        ui._poll_log_queue()

        ui._start_engine.assert_called_once_with()

    def test_hotkey_callback_during_failed_start_does_not_chain_retries(self):
        ui = object.__new__(app_module.App)
        ui.control_queue = queue.Queue()
        ui.log_queue = queue.Queue()
        ui.root = SimpleNamespace(
            after=lambda *_args: None,
            grab_current=lambda: None,
        )
        ui.engine = None
        ui._engine_ui_active = False
        current_token = object()
        ui._start_hotkey_registration_token = current_token
        ui._registered_start_hotkey = "f8"
        attempts = []

        def fail_while_key_repeats():
            attempts.append(True)
            ui._request_start_from_hotkey(current_token)

        ui._start_engine = Mock(side_effect=fail_while_key_repeats)
        ui._request_start_from_hotkey(current_token)

        ui._poll_log_queue()

        self.assertEqual(attempts, [True])
        self.assertTrue(ui.control_queue.empty())
        self.assertFalse(ui._start_request_in_progress)

    def test_hotkey_callback_during_manual_start_does_not_queue_retry(self):
        ui = object.__new__(app_module.App)
        ui.control_queue = queue.Queue()
        current_token = object()
        ui._start_hotkey_registration_token = current_token
        ui._start_request_generation = 0
        ui._pending_start_request = None
        ui._start_request_in_progress = False

        def attempt_while_key_repeats():
            ui._request_start_from_hotkey(current_token)

        ui._start_engine_attempt = Mock(side_effect=attempt_while_key_repeats)

        ui._start_engine()

        ui._start_engine_attempt.assert_called_once_with()
        self.assertTrue(ui.control_queue.empty())
        self.assertFalse(ui._start_request_in_progress)

    def test_manual_run_stop_invalidates_older_queued_start(self):
        ui = object.__new__(app_module.App)
        ui.control_queue = queue.Queue()
        ui.log_queue = queue.Queue()
        ui.root = SimpleNamespace(after=lambda *_args: None)
        ui.engine = None
        ui._engine_ui_active = False
        current_token = object()
        ui._start_hotkey_registration_token = current_token
        ui._registered_start_hotkey = "f8"
        ui._start_engine_from_hotkey = Mock()

        ui._request_start_from_hotkey(current_token)
        # Both a manual Run attempt and the following Stop invalidate work
        # queued for the older stopped generation.
        ui._invalidate_queued_start_requests()
        ui._invalidate_queued_start_requests()
        ui._poll_log_queue()

        ui._start_engine_from_hotkey.assert_not_called()


if __name__ == "__main__":
    unittest.main()
