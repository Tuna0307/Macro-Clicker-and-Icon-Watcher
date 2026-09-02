import unittest

import cv2
import numpy as np

from macro_clicker.engine import MacroEngine
from macro_clicker.models import (
    Scenario,
    load_scenario,
    project_path,
    validate_scenario,
)
from macro_clicker.window_locator import resolve_window_region


SQUAD_REGION = [154, 205, 51, 28]
SQUAD_REGION_RATIO = [
    154 / 1920,
    205 / 1080,
    51 / 1920,
    28 / 1080,
]


class _NeverStop:
    def is_set(self):
        return False


class RallySquadStateDetectionTests(unittest.TestCase):
    def setUp(self):
        self.scenario = load_scenario("Rally gold mob_ 2 team")
        self.steps = {step.name: step for step in self.scenario.steps}
        self.one_third = self.steps["Click Rally Icon 1/3"].conditions[1]
        self.two_thirds = self.steps["Click Rally Icon 2/3"].conditions[1]

    @staticmethod
    def _template(path):
        image = cv2.imread(project_path(path))
        if image is None:
            raise AssertionError(f"template could not be loaded: {path}")
        return image

    def _engine(self):
        engine = object.__new__(MacroEngine)
        engine._stop_event = _NeverStop()
        engine._prepared_template_cache = {}
        engine.low_variance_threshold = 1.0
        engine.scenario = Scenario(name="squad-state test")
        engine._load_template = self._template
        return engine

    def _frame_with_template(self, template_path, *, size=(51, 28)):
        width, height = size
        frame = np.zeros((height, width, 3), dtype=np.uint8)
        template = self._template(template_path)
        template_height, template_width = template.shape[:2]
        left = (width - template_width) // 2
        top = (height - template_height) // 2
        frame[top : top + template_height, left : left + template_width] = template
        return frame

    def _gate_matches(self, condition, frame):
        ok, matches, _image = self._engine()._preview_template_condition(
            0,
            condition,
            frame,
            0,
            0,
            None,
            collect_all=False,
        )
        return ok, matches

    def test_one_third_template_passes_only_one_third_gate(self):
        frame = self._frame_with_template("templates/1_3Squad.png")

        one_ok, one_matches = self._gate_matches(self.one_third, frame)
        two_ok, _two_matches = self._gate_matches(self.two_thirds, frame)

        self.assertTrue(one_ok)
        self.assertEqual(len(one_matches), 1)
        self.assertGreaterEqual(one_matches[0]["confidence"], self.one_third.confidence)
        self.assertGreaterEqual(
            one_matches[0]["score_margin"], self.one_third.comparison_margin
        )
        self.assertFalse(two_ok)

    def test_two_thirds_template_passes_only_two_thirds_gate(self):
        frame = self._frame_with_template("templates/2_3Squad.png")

        one_ok, _one_matches = self._gate_matches(self.one_third, frame)
        two_ok, two_matches = self._gate_matches(self.two_thirds, frame)

        self.assertFalse(one_ok)
        self.assertTrue(two_ok)
        self.assertEqual(len(two_matches), 1)
        self.assertGreaterEqual(
            two_matches[0]["confidence"], self.two_thirds.confidence
        )
        self.assertGreaterEqual(
            two_matches[0]["score_margin"], self.two_thirds.comparison_margin
        )

    def test_full_three_of_three_is_rejected_by_both_entry_gates(self):
        frame = self._frame_with_template("templates/FullSquad3_3.png")

        one_ok, _one_matches = self._gate_matches(self.one_third, frame)
        two_ok, _two_matches = self._gate_matches(self.two_thirds, frame)

        self.assertFalse(one_ok)
        self.assertFalse(two_ok)

    def test_unrelated_ui_does_not_match_squad_state_gates(self):
        frame = self._frame_with_template("templates/RallyIcon.png")

        one_ok, _one_matches = self._gate_matches(self.one_third, frame)
        two_ok, _two_matches = self._gate_matches(self.two_thirds, frame)

        self.assertFalse(one_ok)
        self.assertFalse(two_ok)

    def test_supported_rally_scenarios_share_current_squad_roi(self):
        for scenario_name in ("Rally Gold Mob", "Rally gold mob_ 2 team"):
            with self.subTest(scenario=scenario_name):
                scenario = load_scenario(scenario_name)
                validate_scenario(scenario, require_files=True)
                squad_conditions = [
                    condition
                    for step in scenario.steps
                    for condition in step.conditions
                    if condition.template_path
                    in {"templates/1_3Squad.png", "templates/2_3Squad.png"}
                ]

                self.assertTrue(squad_conditions)
                for condition in squad_conditions:
                    self.assertEqual(condition.region, SQUAD_REGION)
                    self.assertEqual(condition.region_ratio, SQUAD_REGION_RATIO)
                    self.assertEqual(condition.region_window_size, [1920, 1080])
                    self.assertEqual(
                        condition.comparison_template_path,
                        "templates/FullSquad3_3.png",
                    )

    def test_squad_roi_translates_normally_on_left_monitor(self):
        self.assertEqual(
            resolve_window_region(
                self.one_third.region,
                (-1920, 0, 1920, 1080),
                self.one_third.region_ratio,
                self.one_third.region_window_size,
            ),
            (-1766, 205, 51, 28),
        )

    def test_squad_roi_follows_window_to_other_secondary_monitor(self):
        self.assertEqual(
            resolve_window_region(
                self.one_third.region,
                (1920, 0, 1920, 1080),
                self.one_third.region_ratio,
                self.one_third.region_window_size,
            ),
            (2074, 205, 51, 28),
        )

    def test_rally_icon_condition_is_unchanged_and_still_matchable(self):
        rally_icon = self.steps["Click Rally Icon 1/3"].conditions[0]
        self.assertEqual(rally_icon.region, [1824, 633, 85, 90])
        self.assertEqual(
            rally_icon.region_ratio,
            [1824 / 1920, 633 / 1080, 85 / 1920, 90 / 1080],
        )

        frame = self._frame_with_template(
            "templates/RallyIcon.png",
            size=(rally_icon.region[2], rally_icon.region[3]),
        )
        ok, matches = self._gate_matches(rally_icon, frame)

        self.assertTrue(ok)
        self.assertEqual(len(matches), 1)
        self.assertGreaterEqual(matches[0]["confidence"], rally_icon.confidence)


if __name__ == "__main__":
    unittest.main()
