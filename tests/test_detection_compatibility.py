import os
from pathlib import Path
import threading
import unittest

import cv2
import numpy as np

from macro_clicker import alert_watcher, detection_core
from macro_clicker import engine as engine_module
from macro_clicker.engine import MacroEngine
from macro_clicker.models import (
    ImageCondition,
    Scenario,
    Step,
    load_scenario,
    project_path,
)
from macro_clicker.project_paths import PROJECT_ROOT


class DetectionCompatibilityTests(unittest.TestCase):
    @staticmethod
    def _bare_engine():
        engine = object.__new__(MacroEngine)
        engine._stop_event = threading.Event()
        engine.log = lambda _message: None
        engine._prepared_template_cache = {}
        engine.max_matches_per_scale = 128
        engine.max_multiscale_candidates = 512
        return engine

    def test_both_adapters_are_the_shared_core_functions(self):
        self.assertIs(
            alert_watcher.match_template_multiscale,
            detection_core.match_template_multiscale,
        )
        self.assertIs(
            engine_module.find_template_matches,
            detection_core.find_template_matches,
        )

    def test_macro_and_alert_static_detection_agree_at_exact_four_thirds(self):
        rng = np.random.default_rng(149)
        template = rng.integers(0, 256, (24, 30, 3), dtype=np.uint8)
        scaled = cv2.resize(template, (40, 32), interpolation=cv2.INTER_LINEAR)
        frame = np.zeros((150, 240, 3), dtype=np.uint8)
        frame[73:105, 121:161] = scaled
        engine = self._bare_engine()

        macro = engine._find_template_matches_in_frame(
            frame,
            template,
            0.99,
            collect_all=False,
            match_mode=detection_core.MATCH_MODE_STATIC,
            reference_size=(1920, 1080),
            current_size=(2560, 1440),
        )[0]
        alert_score, alert_location, alert_scale = (
            alert_watcher.match_template_multiscale(
                frame,
                template,
                match_mode=detection_core.MATCH_MODE_STATIC,
                reference_size=(1920, 1080),
                current_size=(2560, 1440),
            )
        )

        self.assertEqual(macro[:2], alert_location)
        self.assertAlmostEqual(macro[4], alert_score)
        self.assertAlmostEqual(macro[5], alert_scale)
        self.assertAlmostEqual(macro.scale_x, 4 / 3)
        self.assertAlmostEqual(macro.scale_y, 4 / 3)
        self.assertAlmostEqual(alert_scale, 4 / 3)

    def test_macro_and_alert_colored_text_scores_are_identical(self):
        template = np.full((32, 130, 3), (54, 111, 99), dtype=np.uint8)
        cv2.putText(
            template,
            "#2212",
            (3, 24),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (255, 235, 71),
            2,
            cv2.LINE_AA,
        )
        candidate = np.full((32, 130, 3), (120, 60, 40), dtype=np.uint8)
        cv2.putText(
            candidate,
            "#2212",
            (3, 24),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (255, 235, 71),
            2,
            cv2.LINE_AA,
        )
        frame = np.full((80, 260, 3), (120, 60, 40), dtype=np.uint8)
        frame[25:57, 70:200] = candidate
        engine = self._bare_engine()

        macro = engine._find_template_matches_in_frame(
            frame,
            template,
            0.9,
            collect_all=False,
            match_mode=detection_core.MATCH_MODE_TEXT,
        )[0]
        alert = alert_watcher.match_template_multiscale(
            frame,
            template,
            match_mode=detection_core.MATCH_MODE_TEXT,
        )

        self.assertEqual(macro[:2], alert[1])
        self.assertAlmostEqual(macro[4], alert[0])
        self.assertAlmostEqual(macro[5], alert[2])

    def test_loading_live_data_does_not_rewrite_user_files(self):
        manifest = alert_watcher.MANIFEST_PATH
        scenario = os.path.join(PROJECT_ROOT, "scenarios", "Rally Gold Mob.json")
        before_manifest = Path(manifest).read_bytes()
        before_scenario = Path(scenario).read_bytes()

        alert_watcher.TemplateManager()
        load_scenario("Rally Gold Mob")

        self.assertEqual(Path(manifest).read_bytes(), before_manifest)
        self.assertEqual(Path(scenario).read_bytes(), before_scenario)

    def test_every_rally_condition_keeps_exact_1440p_candidate(self):
        scenario = load_scenario("Rally Gold Mob")
        engine = self._bare_engine()
        engine.scenario = scenario
        engine.low_variance_threshold = 1.0
        engine._get_target_window_rect = lambda: (0, 0, 2560, 1440)

        for step in scenario.steps:
            for index, condition in enumerate(step.conditions):
                with self.subTest(step=step.name, condition=index):
                    template = cv2.imread(
                        project_path(condition.template_path),
                        cv2.IMREAD_COLOR,
                    )
                    self.assertIsNotNone(template)
                    assert template is not None
                    rendered = cv2.resize(
                        template,
                        (
                            round(template.shape[1] * 4 / 3),
                            round(template.shape[0] * 4 / 3),
                        ),
                        interpolation=cv2.INTER_LINEAR,
                    )
                    frame = np.zeros(
                        (rendered.shape[0] + 30, rendered.shape[1] + 40, 3),
                        dtype=np.uint8,
                    )
                    frame[
                        13:13 + rendered.shape[0],
                        17:17 + rendered.shape[1],
                    ] = rendered

                    matches = engine._find_template_matches_in_frame(
                        frame,
                        template,
                        condition.confidence,
                        collect_all=False,
                        **engine._condition_matching_kwargs(condition),
                    )

                    self.assertTrue(matches)
                    self.assertEqual(matches[0][:2], (17, 13))
                    self.assertAlmostEqual(matches[0][5], 4 / 3)

    def test_rival_template_can_have_an_independent_reference_size(self):
        rng = np.random.default_rng(151)
        rival = rng.integers(0, 256, (20, 24, 3), dtype=np.uint8)
        scale_x, scale_y = 2560 / 1608, 1440 / 940
        rendered = cv2.resize(
            rival,
            (round(24 * scale_x), round(20 * scale_y)),
            interpolation=cv2.INTER_LINEAR,
        )
        frame = np.zeros((100, 140, 3), dtype=np.uint8)
        frame[20:20 + rendered.shape[0], 30:30 + rendered.shape[1]] = rendered
        condition = ImageCondition(
            template_path="target.png",
            template_reference_size=[1920, 1080],
            comparison_template_path="rival.png",
            comparison_template_reference_size=[1608, 940],
        )
        engine = self._bare_engine()
        engine.scenario = Scenario(
            name="rival refs",
            target_window_title="Game",
            steps=[Step(name="One", conditions=[condition])],
        )
        engine.low_variance_threshold = 1.0
        engine._get_target_window_rect = lambda: (0, 0, 2560, 1440)

        match = engine._find_best_template_match_near(
            frame,
            rival,
            (30, 20, rendered.shape[1], rendered.shape[0], 1.0, 1.0),
            condition,
        )

        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match[:2], (30, 20))

    def test_scaled_larger_rival_fits_search_near_smaller_target(self):
        rng = np.random.default_rng(157)
        rival = rng.integers(0, 256, (20, 24, 3), dtype=np.uint8)
        scale_x, scale_y = 2560 / 1608, 1440 / 940
        rendered = cv2.resize(
            rival,
            (round(24 * scale_x), round(20 * scale_y)),
            interpolation=cv2.INTER_LINEAR,
        )
        frame = np.zeros((100, 140, 3), dtype=np.uint8)
        frame[20:20 + rendered.shape[0], 30:30 + rendered.shape[1]] = rendered
        condition = ImageCondition(
            template_path="target.png",
            template_reference_size=[1920, 1080],
            comparison_template_path="rival.png",
            comparison_template_reference_size=[1608, 940],
        )
        engine = self._bare_engine()
        engine.scenario = Scenario(
            name="larger scaled rival",
            target_window_title="Game",
            steps=[Step(name="One", conditions=[condition])],
        )
        engine.low_variance_threshold = 1.0
        engine._get_target_window_rect = lambda: (0, 0, 2560, 1440)

        match = engine._find_best_template_match_near(
            frame,
            rival,
            (30, 20, 12, 12, 1.0, 1.0),
            condition,
        )

        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match[:4], (
            30,
            20,
            rendered.shape[1],
            rendered.shape[0],
        ))
        self.assertGreater(match[4], 0.99)


if __name__ == "__main__":
    unittest.main()
