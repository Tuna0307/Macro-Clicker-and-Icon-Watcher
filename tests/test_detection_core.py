import threading
import unittest

import cv2
import numpy as np

from macro_clicker import detection_core as core
from macro_clicker.window_locator import absolute_region_from_window_ratio


class ResolutionScalingTests(unittest.TestCase):
    def test_exact_four_thirds_scale_is_first_candidate(self):
        scales = core.resolution_scale_candidates(
            (1920, 1080), (2560, 1440), (1.0, 1.3, 1.4)
        )

        self.assertAlmostEqual(scales[0], 4 / 3)
        self.assertEqual(len(scales), len(set(round(item, 6) for item in scales)))

    def test_macro_legacy_fallback_remains_bounded_but_exact_scale_is_added(self):
        self.assertEqual(
            core.MACRO_DEFAULT_SCALES,
            (1.0, 0.95, 1.05, 0.9, 1.1, 0.85, 1.15, 0.8, 1.2),
        )
        scales = core.resolution_scale_candidates(
            (1920, 1080),
            (2560, 1440),
            core.MACRO_DEFAULT_SCALES,
        )

        self.assertAlmostEqual(scales[0], 4 / 3)
        self.assertIn(1.0, scales)

    def test_missing_reference_size_preserves_legacy_scale_order(self):
        legacy = (1.0, 0.9, 1.1)

        self.assertEqual(
            core.resolution_scale_candidates(None, (2560, 1440), legacy),
            legacy,
        )

    def test_aspect_mismatch_keeps_uniform_width_and_height_candidates(self):
        scales = core.resolution_scale_candidates((1608, 940), (2560, 1440), (1.0,))

        self.assertTrue(any(abs(item - 2560 / 1608) < 1e-6 for item in scales))
        self.assertTrue(any(abs(item - 1440 / 940) < 1e-6 for item in scales))

    def test_aspect_mismatch_uses_exact_independent_width_and_height_scale(self):
        rng = np.random.default_rng(127)
        template = rng.integers(0, 256, (31, 47, 3), dtype=np.uint8)
        scale_x, scale_y = 2560 / 1608, 1440 / 940
        rendered = cv2.resize(
            template,
            (round(47 * scale_x), round(31 * scale_y)),
            interpolation=cv2.INTER_LINEAR,
        )
        frame = np.zeros((180, 260, 3), dtype=np.uint8)
        frame[70 : 70 + rendered.shape[0], 90 : 90 + rendered.shape[1]] = rendered

        match = core.find_template_matches(
            frame,
            template,
            0.99,
            match_mode=core.MATCH_MODE_STATIC,
            reference_size=(1608, 940),
            current_size=(2560, 1440),
        )[0]

        self.assertEqual((match.x, match.y), (90, 70))
        self.assertEqual(
            (match.width, match.height), (rendered.shape[1], rendered.shape[0])
        )
        self.assertAlmostEqual(match.scale_x, scale_x)
        self.assertAlmostEqual(match.scale_y, scale_y)

    def test_exact_1440p_scale_finds_exact_pixel_and_size(self):
        rng = np.random.default_rng(131)
        template = rng.integers(0, 256, (30, 45, 3), dtype=np.uint8)
        scaled = cv2.resize(template, (60, 40), interpolation=cv2.INTER_LINEAR)
        frame = np.zeros((220, 360, 3), dtype=np.uint8)
        frame[91:131, 173:233] = scaled

        matches = core.find_template_matches(
            frame,
            template,
            0.99,
            match_mode=core.MATCH_MODE_STATIC,
            reference_size=(1920, 1080),
            current_size=(2560, 1440),
        )

        self.assertEqual(len(matches), 1)
        self.assertEqual((matches[0].x, matches[0].y), (173, 91))
        self.assertEqual((matches[0].width, matches[0].height), (60, 40))
        self.assertAlmostEqual(matches[0].scale, 4 / 3)

    def test_region_and_template_scale_share_the_same_resolution_ratio(self):
        region = absolute_region_from_window_ratio(
            (0.1, 0.2, 0.25, 0.1),
            (0, 0, 2560, 1440),
        )
        scale = core.preferred_resolution_scale((1920, 1080), (2560, 1440))

        self.assertEqual(region, (256, 288, 640, 144))
        self.assertAlmostEqual(scale, 4 / 3)


class SharedMatcherTests(unittest.TestCase):
    @staticmethod
    def _text_tile(text, background, foreground=(255, 235, 71)):
        image = np.full((32, 130, 3), background, dtype=np.uint8)
        cv2.putText(
            image,
            text,
            (3, 24),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            foreground,
            2,
            cv2.LINE_AA,
        )
        return image

    def test_modes_share_one_variant_preparer(self):
        template = self._text_tile("#2212", (54, 111, 99))

        text = core.prepare_template_variants(
            template, scales=(1.0,), match_mode=core.MATCH_MODE_TEXT
        )
        static = core.prepare_template_variants(
            template, scales=(1.0,), match_mode=core.MATCH_MODE_STATIC
        )
        animated = core.prepare_template_variants(
            template, scales=(1.0,), match_mode=core.MATCH_MODE_ANIMATED
        )

        self.assertEqual({item["angle"] for item in text}, {0.0})
        self.assertEqual({item["angle"] for item in static}, {0.0})
        self.assertEqual(
            {item["angle"] for item in animated},
            {float(item) for item in core.DEFAULT_ROTATIONS},
        )

    def test_cached_variants_keep_their_grayscale_contract(self):
        rng = np.random.default_rng(125)
        template = rng.integers(0, 256, (20, 24, 3), dtype=np.uint8)
        frame = np.zeros((80, 100, 3), dtype=np.uint8)
        frame[31:51, 47:71] = template
        variants = core.prepare_template_variants(
            template,
            scales=(1.0,),
            use_grayscale=True,
            match_mode=core.MATCH_MODE_STATIC,
        )

        score, location, _scale = core.match_template_multiscale(
            frame,
            template,
            variants=variants,
            match_mode=core.MATCH_MODE_STATIC,
        )

        self.assertGreaterEqual(score, 0.99)
        self.assertEqual(location, (47, 31))

    def test_colored_text_rejects_similar_digits_on_changed_background(self):
        template = self._text_tile("#2212", (54, 111, 99))

        def score(text):
            frame = np.full((80, 260, 3), (120, 60, 40), dtype=np.uint8)
            frame[25:57, 70:200] = self._text_tile(text, (120, 60, 40))
            return core.match_template_multiscale(
                frame,
                template,
                scales=(1.0,),
                match_mode=core.MATCH_MODE_TEXT,
            )[0]

        self.assertGreaterEqual(score("#2212"), 0.95)
        self.assertLess(score("#2217"), 0.9)

    def test_colored_text_rejects_one_wrong_glyph_even_when_rest_is_exact(self):
        template = self._text_tile("#2212", (54, 111, 99))

        def score(text):
            frame = np.full((80, 260, 3), (54, 111, 99), dtype=np.uint8)
            frame[25:57, 70:200] = self._text_tile(text, (54, 111, 99))
            return core.match_template_multiscale(
                frame,
                template,
                scales=(1.0,),
                match_mode=core.MATCH_MODE_TEXT,
            )[0]

        self.assertGreaterEqual(score("#2212"), 0.95)
        self.assertLess(score("#2210"), 0.9)

    def test_collect_all_colored_text_uses_the_same_wrong_glyph_rejection(self):
        template = self._text_tile("#2212", (54, 111, 99))
        frame = np.full((80, 260, 3), (54, 111, 99), dtype=np.uint8)
        frame[25:57, 70:200] = self._text_tile("#2210", (54, 111, 99))

        matches = core.find_template_matches(
            frame,
            template,
            0.85,
            collect_all=True,
            scales=(1.0,),
            match_mode=core.MATCH_MODE_TEXT,
        )

        self.assertEqual(matches, [])

    def test_collect_all_keeps_targets_at_different_scales(self):
        rng = np.random.default_rng(137)
        template = rng.integers(0, 256, (20, 20, 3), dtype=np.uint8)
        scaled = cv2.resize(template, (24, 24), interpolation=cv2.INTER_LINEAR)
        frame = np.zeros((90, 140, 3), dtype=np.uint8)
        frame[8:28, 10:30] = template
        frame[48:72, 90:114] = scaled

        matches = core.find_template_matches(
            frame,
            template,
            0.85,
            collect_all=True,
            match_mode=core.MATCH_MODE_STATIC,
            scales=(1.0, 1.2),
        )

        positions = {(item.x, item.y, item.scale) for item in matches}
        self.assertIn((10, 8, 1.0), positions)
        self.assertIn((90, 48, 1.2), positions)

    def test_large_collect_all_coarse_search_refines_full_resolution_targets(self):
        rng = np.random.default_rng(139)
        template = rng.integers(0, 256, (32, 44, 3), dtype=np.uint8)
        frame = np.zeros((800, 1000, 3), dtype=np.uint8)
        frame[123:155, 217:261] = template
        frame[611:643, 809:853] = template

        matches = core.find_template_matches(
            frame,
            template,
            0.99,
            collect_all=True,
            allow_coarse=True,
            match_mode=core.MATCH_MODE_STATIC,
            scales=(1.0, 0.95, 1.05, 0.9, 1.1),
        )

        self.assertEqual(
            {(item.x, item.y) for item in matches},
            {(217, 123), (809, 611)},
        )

    def test_collect_all_falls_back_when_coarse_checkerboard_collapses(self):
        template = np.indices((32, 32)).sum(axis=0) % 2
        template = np.repeat(
            (template * 255).astype(np.uint8)[:, :, None],
            3,
            axis=2,
        )
        frame = np.full((600, 1000, 3), 127, dtype=np.uint8)
        frame[220:252, 410:442] = template

        matches = core.find_template_matches(
            frame,
            template,
            0.99,
            collect_all=True,
            allow_coarse=True,
            match_mode=core.MATCH_MODE_STATIC,
            scales=(1.0, 0.95, 1.05, 0.9, 1.1),
        )

        self.assertIn((410, 220), {(item.x, item.y) for item in matches})

    def test_flat_template_on_flat_screen_has_no_defensible_match(self):
        template = np.zeros((8, 8, 3), dtype=np.uint8)
        frame = np.zeros((60, 70, 3), dtype=np.uint8)

        self.assertEqual(
            core.find_template_matches(
                frame,
                template,
                0.99,
                collect_all=True,
                match_mode=core.MATCH_MODE_STATIC,
                scales=(1.0,),
            ),
            [],
        )

    def test_nearly_flat_brightness_shift_is_not_a_perfect_false_match(self):
        template = np.full((16, 16, 3), 50, dtype=np.uint8)
        template[4:6, 5:7] = 51
        candidate = np.full((16, 16, 3), 200, dtype=np.uint8)
        candidate[4:6, 5:7] = 201
        frame = np.full((40, 40, 3), 255, dtype=np.uint8)
        frame[10:26, 12:28] = candidate

        safe_score = core.match_template_multiscale(
            frame,
            template,
            scales=(1.0,),
            match_mode=core.MATCH_MODE_STATIC,
            allow_coarse=False,
        )[0]
        unsafe_score = core.match_template_multiscale(
            frame,
            template,
            scales=(1.0,),
            match_mode=core.MATCH_MODE_STATIC,
            allow_coarse=False,
            low_variance_threshold=1e-6,
        )[0]

        self.assertLess(safe_score, 0.9)
        self.assertGreaterEqual(unsafe_score, 0.99)

    def test_nearly_flat_template_rejects_unrelated_one_level_noise(self):
        rng = np.random.default_rng(141)
        template = rng.integers(100, 102, (4, 4, 3), dtype=np.uint8)
        frame = rng.integers(100, 102, (700, 1000, 3), dtype=np.uint8)

        score = core.match_template_multiscale(
            frame,
            template,
            scales=(1.0,),
            match_mode=core.MATCH_MODE_STATIC,
            allow_coarse=False,
        )[0]

        self.assertLess(score, 0.85)

    def test_variant_budget_rejects_oversized_detection_profile(self):
        with self.assertRaisesRegex(ValueError, "capture a tighter region"):
            core.prepare_template_variants(
                np.zeros((20, 20, 3), dtype=np.uint8),
                scales=(1.0,),
                match_mode=core.MATCH_MODE_ANIMATED,
                max_variant_pixels=100,
            )

    def test_cancellation_stops_before_matching(self):
        stopped = threading.Event()
        stopped.set()

        result = core.find_template_matches(
            np.zeros((30, 30, 3), dtype=np.uint8),
            np.zeros((5, 5, 3), dtype=np.uint8),
            0.8,
            cancel_event=stopped,
        )

        self.assertEqual(result, [])


class SharedCaptureTests(unittest.TestCase):
    def test_capture_uses_bgr_contract(self):
        class FakeCapture:
            def grab(self, _target):
                return np.array([[[11, 22, 33, 255]]], dtype=np.uint8)

        frame = core.capture_bgr(
            FakeCapture(), {"left": 0, "top": 0, "width": 1, "height": 1}
        )

        np.testing.assert_array_equal(frame, np.array([[[11, 22, 33]]], dtype=np.uint8))

    def test_monitor_selection_and_negative_coordinates(self):
        monitors = [
            {"left": -1920, "top": 0, "width": 4480, "height": 1440},
            {"left": -1920, "top": 0, "width": 1920, "height": 1080},
            {"left": 0, "top": 0, "width": 2560, "height": 1440},
        ]

        self.assertEqual(
            core.monitor_index_for_rect(monitors, (-1900, 10, 1800, 1000)), 1
        )
        self.assertEqual(core.monitor_index_for_rect(monitors, (10, 10, 2500, 1400)), 2)
        self.assertEqual(
            core.intersect_region_with_monitor(monitors[1], (-1800, 100, 400, 300)),
            (120, 100, 400, 300),
        )


if __name__ == "__main__":
    unittest.main()
