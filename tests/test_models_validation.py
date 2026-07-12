import json
import os
import tempfile
import unittest

from models import (
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
    def test_unknown_action_type_is_rejected_instead_of_becoming_a_click(self):
        with self.assertRaisesRegex(ValueError, "unsupported action type"):
            Action.from_dict({"type": "wa1t", "seconds": 1})

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
