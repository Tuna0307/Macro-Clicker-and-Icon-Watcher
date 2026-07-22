import json
import os
import tempfile
import unittest

from macro_clicker.detection_core import MATCH_MODE_STATIC, MATCH_MODE_TEXT
from macro_clicker.models import (
    Action,
    ImageCondition,
    Scenario,
    Step,
    load_scenario,
    save_scenario,
    scenario_name_exists,
    validate_scenario,
)


class ModelValidationTests(unittest.TestCase):
    def test_detection_profile_round_trips_and_legacy_defaults_remain_static(self):
        condition = ImageCondition(
            template_path="templates/chat.png",
            comparison_template_path="templates/rival.png",
            comparison_template_reference_size=[1608, 940],
            match_mode=MATCH_MODE_TEXT,
            use_grayscale=True,
            template_reference_size=[1920, 1080],
        )

        restored = ImageCondition.from_dict(condition.to_dict())
        legacy = ImageCondition.from_dict({"template_path": "templates/icon.png"})

        self.assertEqual(restored, condition)
        self.assertEqual(legacy.match_mode, MATCH_MODE_STATIC)
        self.assertFalse(legacy.use_grayscale)
        self.assertIsNone(legacy.template_reference_size)

    def test_invalid_detection_profile_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "match_mode"):
            ImageCondition.from_dict({
                "template_path": "templates/icon.png",
                "match_mode": "spinning_text",
            })
        with self.assertRaisesRegex(ValueError, "template_reference_size"):
            ImageCondition.from_dict({
                "template_path": "templates/icon.png",
                "template_reference_size": [0, 1080],
            })
        with self.assertRaisesRegex(ValueError, "without a comparison template"):
            validate_scenario(Scenario(
                name="Invalid rival metadata",
                steps=[Step(
                    name="One",
                    conditions=[ImageCondition(
                        template_path="templates/icon.png",
                        comparison_template_reference_size=[1920, 1080],
                    )],
                )],
            ))

    def test_monitor_relative_region_round_trips_and_validates(self):
        condition = ImageCondition(
            template_path="templates/icon.png",
            region=[100, 50, 200, 100],
            region_mode="monitor",
            region_ratio=[100 / 1920, 50 / 1080, 200 / 1920, 100 / 1080],
            region_window_size=[1920, 1080],
        )
        scenario = Scenario(
            name="Portable monitor region",
            steps=[Step(name="One", conditions=[condition])],
        )

        restored = Scenario.from_dict(scenario.to_dict())
        validate_scenario(restored)

        self.assertEqual(restored.steps[0].conditions[0], condition)

    def test_scenario_start_hotkey_round_trips_and_legacy_files_default_to_f8(self):
        scenario = Scenario(
            name="Custom start",
            start_hotkey="ctrl+f8",
            kill_switch="f12",
        )

        restored = Scenario.from_dict(scenario.to_dict())
        legacy = Scenario.from_dict({"name": "Legacy", "steps": []})

        self.assertEqual(restored.start_hotkey, "ctrl+f8")
        self.assertEqual(legacy.start_hotkey, "f8")

    def test_scenario_start_and_stop_hotkeys_must_be_different(self):
        with self.assertRaisesRegex(ValueError, "different keys"):
            validate_scenario(
                Scenario(name="Conflicting keys", start_hotkey="F12", kill_switch="f12")
            )

    def test_invalid_hotkeys_and_key_actions_fail_preflight(self):
        with self.assertRaisesRegex(ValueError, "start_hotkey is invalid"):
            validate_scenario(
                Scenario(name="Bad start", start_hotkey="not-a-real-hotkey")
            )

        scenario = Scenario(
            name="Bad key action",
            steps=[Step(
                name="One",
                actions=[Action(type="key", key="not-a-real-key")],
            )],
        )
        with self.assertRaisesRegex(ValueError, "invalid key name"):
            validate_scenario(scenario)

    def test_region_ratio_rejects_text_values_before_runtime_coordinate_math(self):
        with self.assertRaisesRegex(ValueError, "region_ratio"):
            ImageCondition.from_dict({
                "template_path": "templates/icon.png",
                "region": [10, 20, 30, 40],
                "region_mode": "monitor",
                "region_ratio": ["0.1", "0.2", "0.3", "0.4"],
                "region_window_size": [100, 100],
            })

        scenario = Scenario(
            name="Invalid ratio",
            steps=[Step(
                name="One",
                conditions=[ImageCondition(
                    template_path="templates/icon.png",
                    region=[10, 20, 30, 40],
                    region_mode="monitor",
                    region_ratio=["0.1", "0.2", "0.3", "0.4"],
                    region_window_size=[100, 100],
                )],
            )],
        )
        with self.assertRaisesRegex(ValueError, "region_ratio"):
            validate_scenario(scenario)

    def test_unknown_action_type_is_rejected_instead_of_becoming_a_click(self):
        with self.assertRaisesRegex(ValueError, "unsupported action type"):
            Action.from_dict({"type": "wa1t", "seconds": 1})

    def test_matching_row_pre_click_delay_round_trips_and_rejects_invalid_values(self):
        action = Action(type="click_matching_row", pre_click_delay=1.5)

        self.assertEqual(Action.from_dict(action.to_dict()).pre_click_delay, 1.5)
        for value in (-0.1, float("nan"), float("inf"), True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Action.from_dict({"type": "click_matching_row", "pre_click_delay": value})

    def test_rally_team_maximums_default_to_unlimited_and_round_trip_null(self):
        defaults = Action(type="select_rally_team")
        restored = Action.from_dict(
            {
                "type": "select_rally_team",
                "team1_max_level": None,
                "team3_max_level": None,
            }
        )

        self.assertIsNone(defaults.team1_max_level)
        self.assertIsNone(defaults.team3_max_level)
        self.assertIsNone(restored.team1_max_level)
        self.assertIsNone(restored.team3_max_level)

    def test_smart_row_parses_old_limits_but_serializes_them_as_null(self):
        action = Action.from_dict(
            {
                "type": "click_matching_row",
                "max_level": 12,
                "team1_max_level": 11,
                "team3_max_level": 10,
                "team_status_region": [0, 0, 100, 100],
                "team_status_reference_size": [1920, 1080],
                "team1_busy_template_path": "templates/Team1Busy.png",
                "team3_busy_template_path": "templates/Team3Busy.png",
            }
        )

        self.assertEqual(action.max_level, 12)
        self.assertEqual(action.team1_max_level, 11)
        self.assertEqual(action.team3_max_level, 10)
        serialized = action.to_dict()
        self.assertIsNone(serialized["max_level"])
        self.assertIsNone(serialized["team1_max_level"])
        self.assertIsNone(serialized["team3_max_level"])

    def test_smart_row_summary_suppresses_legacy_maximum(self):
        smart = Action.from_dict(
            {
                "type": "click_matching_row",
                "min_level": 20,
                "max_level": 65,
                "team_status_region": [0, 0, 100, 100],
                "team_status_reference_size": [1920, 1080],
                "team1_busy_template_path": "team1-busy.png",
                "team3_busy_template_path": "team3-busy.png",
            }
        )
        ordinary = Action(type="click_matching_row", max_level=65)

        self.assertIn(">= 20", smart.summary())
        self.assertNotIn("<= 65", smart.summary())
        self.assertIn("<= 65", ordinary.summary())

    def test_blank_team_limits_have_clear_action_summary(self):
        summary = Action(type="select_rally_team").summary()

        self.assertIn("Team 3 (unlimited)", summary)
        self.assertIn("Team 1 (unlimited)", summary)
        self.assertNotIn("None", summary)

    def test_nested_null_values_are_reported_as_load_errors(self):
        with tempfile.TemporaryDirectory() as folder:
            for name, data in (
                ("NullStep", {"name": "NullStep", "steps": [None]}),
                (
                    "NullCondition",
                    {"name": "NullCondition", "steps": [{"name": "Step", "conditions": [None]}]},
                ),
            ):
                with open(os.path.join(folder, f"{name}.json"), "w", encoding="utf-8") as handle:
                    json.dump(data, handle)
                with self.subTest(name=name), self.assertRaisesRegex(ValueError, "Could not load scenario"):
                    load_scenario(name, folder=folder)

    def test_non_finite_and_too_fast_poll_intervals_are_rejected(self):
        for value in (float("nan"), float("inf"), -1.0, 0.0, 0.001):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_scenario(Scenario(name="Invalid", poll_interval=value))

    def test_invalid_condition_and_step_references_are_rejected(self):
        scenario = Scenario(
            name="InvalidRefs",
            steps=[
                Step(
                    name="One",
                    conditions=[ImageCondition(template_path="templates/icon.png")],
                    actions=[Action(type="click", on_condition_index=2)],
                )
            ],
        )

        with self.assertRaisesRegex(ValueError, "invalid on_condition_index"):
            validate_scenario(scenario)

        scenario.steps[0].actions = [Action(type="set_step", step_name="Missing")]
        with self.assertRaisesRegex(ValueError, "missing step"):
            validate_scenario(scenario)

    def test_set_step_enabled_flag_must_be_boolean(self):
        scenario = Scenario(
            name="Invalid set flag",
            steps=[Step(
                name="One",
                actions=[Action(
                    type="set_step",
                    step_name="One",
                    set_enabled="false",
                )],
            )],
        )

        with self.assertRaisesRegex(ValueError, "set_enabled must be a boolean"):
            validate_scenario(scenario)

    def test_click_cannot_mix_fixed_and_condition_targets(self):
        scenario = Scenario(
            name="AmbiguousClick",
            steps=[
                Step(
                    name="One",
                    conditions=[ImageCondition(template_path="templates/icon.png")],
                    actions=[Action(type="click", on_condition_index=0, x=1, y=2)],
                )
            ],
        )

        with self.assertRaisesRegex(ValueError, "both a fixed point and a condition"):
            validate_scenario(scenario)

    def test_case_insensitive_name_collision_is_detected(self):
        with tempfile.TemporaryDirectory() as folder:
            save_scenario(Scenario(name="Rally"), folder=folder)

            self.assertTrue(scenario_name_exists("rAlLy", folder=folder))
            with self.assertRaises(FileExistsError):
                save_scenario(Scenario(name="rally"), folder=folder, overwrite=False)

    def test_require_files_preflight_reports_missing_template(self):
        scenario = Scenario(
            name="MissingTemplate",
            steps=[
                Step(
                    name="One",
                    conditions=[ImageCondition(template_path="templates/does-not-exist.png")],
                )
            ],
        )

        with self.assertRaisesRegex(ValueError, "does not exist"):
            validate_scenario(scenario, require_files=True)

    def test_step_names_are_case_insensitively_unique(self):
        with self.assertRaisesRegex(ValueError, "duplicate step name"):
            Scenario.from_dict({
                "name": "rally",
                "steps": [{"name": "Join"}, {"name": "join"}],
            })

    def test_file_name_must_match_scenario_name(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "Rally.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"name": "Different", "steps": []}, handle)

            with self.assertRaisesRegex(ValueError, "does not match filename"):
                load_scenario("Rally", folder=folder)


if __name__ == "__main__":
    unittest.main()
