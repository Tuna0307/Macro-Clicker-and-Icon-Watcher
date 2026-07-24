import tempfile
import unittest
from pathlib import Path

from macro_clicker.app_helpers import (
    duplicate_scenario,
    duplicate_step,
    duplicate_template_file,
)
from macro_clicker.models import ImageCondition, Scenario, Step


class DuplicateTests(unittest.TestCase):
    def test_duplicate_template_file_copies_png_with_safe_name(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "mob.png"
            source.write_bytes(b"image-data")

            copied = duplicate_template_file(str(source), "Mob Copy")

            self.assertEqual(Path(copied).name, "MobCopy.png")
            self.assertEqual(Path(copied).read_bytes(), b"image-data")

    def test_duplicate_step_deep_copies_and_uses_unique_name(self):
        step = Step(
            name="Join",
            conditions=[ImageCondition(template_path="templates/mob.png")],
        )

        copied = duplicate_step(step, {"Join", "Join_copy"})

        self.assertEqual(copied.name, "Join_copy_2")
        self.assertIsNot(copied.conditions[0], step.conditions[0])
        self.assertEqual(copied.conditions[0].template_path, "templates/mob.png")

    def test_duplicate_step_name_is_unique_without_case_sensitivity(self):
        step = Step(name="Join")

        copied = duplicate_step(step, {"Join", "join_copy"})

        self.assertEqual(copied.name, "Join_copy_2")

    def test_duplicate_scenario_deep_copies_and_uses_new_name(self):
        scenario = Scenario(
            name="Auto Rally",
            steps=[
                Step(
                    name="Join",
                    conditions=[ImageCondition(template_path="templates/mob.png")],
                )
            ],
        )

        copied = duplicate_scenario(scenario, "Auto Rally copy")

        self.assertEqual(copied.name, "Auto Rally copy")
        self.assertIsNot(copied.steps[0], scenario.steps[0])
        self.assertEqual(copied.steps[0].name, "Join")


if __name__ == "__main__":
    unittest.main()
