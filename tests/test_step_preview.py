import unittest

import numpy as np

from engine import MacroEngine
from models import ImageCondition, Scenario, Step


class StepPreviewTests(unittest.TestCase):
    def test_preview_template_condition_reports_multiple_non_overlapping_matches(self):
        engine = object.__new__(MacroEngine)
        template = np.array(
            [
                [[0, 0, 0], [255, 255, 255], [0, 0, 0]],
                [[255, 255, 255], [0, 0, 0], [255, 255, 255]],
                [[0, 0, 0], [255, 255, 255], [0, 0, 0]],
            ],
            dtype=np.uint8,
        )
        frame = np.zeros((20, 40, 3), dtype=np.uint8)
        frame[2:5, 3:6] = template
        frame[12:15, 25:28] = template
        engine._load_template = lambda path: template
        cond = ImageCondition(template_path="templates/Mob.png", confidence=0.99)

        ok, matches, _ = engine._preview_template_condition(0, cond, frame, 100, 200, None)

        self.assertTrue(ok)
        self.assertEqual(len(matches), 2)
        self.assertEqual([m["image_box"] for m in matches], [(3, 2, 6, 5), (25, 12, 28, 15)])

    def test_preview_template_condition_matches_slightly_scaled_template(self):
        import cv2

        engine = object.__new__(MacroEngine)
        rng = np.random.default_rng(1)
        template = rng.integers(0, 256, (20, 20, 3), dtype=np.uint8)
        scaled_template = cv2.resize(template, (22, 22), interpolation=cv2.INTER_LINEAR)
        frame = np.zeros((60, 60, 3), dtype=np.uint8)
        frame[15:37, 18:40] = scaled_template
        engine._load_template = lambda path: template
        cond = ImageCondition(template_path="templates/2_3Squad.png", confidence=0.85)

        ok, matches, _ = engine._preview_template_condition(0, cond, frame, 100, 200, None)

        self.assertTrue(ok)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["image_box"], (18, 15, 40, 37))

    def test_preview_step_keeps_each_condition_image_separate(self):
        engine = object.__new__(MacroEngine)
        image_a = object()
        image_b = object()

        def preview_condition(index, cond):
            if index == 0:
                return True, [], image_a, (10, 20, 30, 40)
            return False, [], image_b, (100, 200, 30, 40)

        engine._preview_condition = preview_condition
        step = Step(
            name="mixed-regions",
            conditions=[
                ImageCondition(template_path="templates/a.png"),
                ImageCondition(template_path="templates/b.png"),
            ],
        )

        preview = engine.preview_step(step)

        self.assertIs(preview["image"], image_a)
        self.assertEqual(
            [(p["condition_index"], p["ok"], p["image"], p["capture_box"]) for p in preview["condition_previews"]],
            [(0, True, image_a, (10, 20, 30, 40)), (1, False, image_b, (100, 200, 30, 40))],
        )


if __name__ == "__main__":
    unittest.main()
