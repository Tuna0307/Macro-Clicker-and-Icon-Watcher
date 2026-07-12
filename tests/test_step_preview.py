import unittest

import numpy as np

from engine import MacroEngine
from models import ImageCondition, Step


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

    def test_competing_template_condition_accepts_only_the_better_template(self):
        engine = object.__new__(MacroEngine)
        rng = np.random.default_rng(7)
        target = rng.integers(0, 256, (12, 12, 3), dtype=np.uint8)
        rival = target.copy()
        rival[2:7, 2:7] = 255 - rival[2:7, 2:7]
        templates = {"target.png": target, "rival.png": rival}
        engine._load_template = templates.__getitem__
        cond = ImageCondition(
            template_path="target.png",
            confidence=0.5,
            comparison_template_path="rival.png",
            comparison_margin=0.03,
        )

        target_frame = np.zeros((30, 30, 3), dtype=np.uint8)
        target_frame[8:20, 9:21] = target
        target_ok, target_matches, _ = engine._preview_template_condition(
            0, cond, target_frame, 0, 0, None
        )

        rival_frame = np.zeros((30, 30, 3), dtype=np.uint8)
        rival_frame[8:20, 9:21] = rival
        rival_ok, rival_matches, _ = engine._preview_template_condition(
            0, cond, rival_frame, 0, 0, None
        )

        self.assertTrue(target_ok)
        self.assertEqual(len(target_matches), 1)
        self.assertGreaterEqual(target_matches[0]["score_margin"], 0.03)
        self.assertFalse(rival_ok)
        self.assertEqual(rival_matches, [])

    def test_competing_template_condition_supports_twenty_percent_ui_scaling(self):
        import cv2

        engine = object.__new__(MacroEngine)
        rng = np.random.default_rng(11)
        target = rng.integers(0, 256, (20, 20, 3), dtype=np.uint8)
        rival = target.copy()
        rival[4:12, 4:12] = 255 - rival[4:12, 4:12]
        scaled_target = cv2.resize(target, (24, 24), interpolation=cv2.INTER_LINEAR)
        frame = np.zeros((50, 50, 3), dtype=np.uint8)
        frame[12:36, 14:38] = scaled_target
        templates = {"target.png": target, "rival.png": rival}
        engine._load_template = templates.__getitem__
        cond = ImageCondition(
            template_path="target.png",
            confidence=0.85,
            comparison_template_path="rival.png",
            comparison_margin=0.03,
        )

        ok, matches, _ = engine._preview_template_condition(0, cond, frame, 0, 0, None)

        self.assertTrue(ok)
        self.assertEqual(matches[0]["scale"], 1.2)
        self.assertGreaterEqual(matches[0]["score_margin"], 0.03)

    def test_competing_template_condition_collects_every_winning_location(self):
        engine = object.__new__(MacroEngine)
        rng = np.random.default_rng(19)
        target = rng.integers(0, 256, (12, 12, 3), dtype=np.uint8)
        rival = target.copy()
        rival[2:7, 2:7] = 255 - rival[2:7, 2:7]
        templates = {"target.png": target, "rival.png": rival}
        engine._load_template = templates.__getitem__
        frame = np.zeros((82, 32, 3), dtype=np.uint8)
        frame[5:17, 8:20] = target
        frame[35:47, 8:20] = target
        frame[65:77, 8:20] = rival
        cond = ImageCondition(
            template_path="target.png",
            confidence=0.5,
            comparison_template_path="rival.png",
            comparison_margin=0.03,
        )

        ok, matches = engine._evaluate_template_condition(
            0, cond, frame, 0, 0, collect_all=True
        )

        self.assertTrue(ok)
        self.assertEqual([match["image_box"][1] for match in matches], [5, 35])

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
