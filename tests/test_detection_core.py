import threading
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from macro_clicker import detection_core as core
from macro_clicker.window_locator import absolute_region_from_window_ratio


class ResolutionScalingTests(unittest.TestCase):
    def test_container_match_mode_falls_back_without_crashing(self):
        self.assertEqual(
            core.normalize_match_mode(["static_picture"]),
            core.LEGACY_ALERT_MATCH_MODE,
        )
        self.assertEqual(
            core.normalize_match_mode({"mode": "static_picture"}, default="fallback"),
            "fallback",
        )

    def test_resize_template_xy_scales_both_axes_and_reuses_cache(self):
        template = np.arange(12 * 20 * 3, dtype=np.uint8).reshape(12, 20, 3)
        cache = {}

        first = core.resize_template_xy(template, 1.5, 0.75, cache=cache)
        second = core.resize_template_xy(template, 1.5, 0.75, cache=cache)

        self.assertEqual(first.shape[:2], (9, 30))
        self.assertIs(second, first)

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

    def test_sequential_exact_ties_use_scale_angle_then_original_order(self):
        rng = np.random.default_rng(126)
        template = rng.integers(0, 256, (20, 24, 3), dtype=np.uint8)
        frame = np.zeros((80, 100, 3), dtype=np.uint8)
        frame[31:51, 47:71] = template
        base = core.prepare_template_variants(
            template,
            scales=(1.0,),
            rotations=(0,),
            match_mode=core.MATCH_MODE_STATIC,
        )[0]

        def variant(label, scale, angle):
            item = dict(base)
            item.update(label=label, scale=scale, angle=angle)
            return item

        variants = (
            variant("farther-scale", 0.8, 0.0),
            variant("larger-angle", 1.0, 5.0),
            variant("preferred-first", 1.0, 0.0),
            variant("preferred-second", 1.0, 0.0),
        )

        score, location, scale, matched_variant = core.match_template_multiscale(
            frame,
            template,
            variants=variants,
            match_mode=core.MATCH_MODE_STATIC,
            allow_coarse=False,
            return_details=True,
        )

        self.assertEqual(score, 1.0)
        self.assertEqual(location, (47, 31))
        self.assertEqual(scale, 1.0)
        self.assertEqual(matched_variant["label"], "preferred-first")

    def test_sequential_threshold_mode_stops_after_nonexact_accepted_match(self):
        rng = np.random.default_rng(127)
        template = rng.integers(0, 256, (20, 24, 3), dtype=np.uint8)
        candidate = template.copy()
        candidate[4, 7, 1] = (int(candidate[4, 7, 1]) + 1) % 256
        frame = np.zeros((80, 100, 3), dtype=np.uint8)
        frame[31:51, 47:71] = candidate
        variants = core.prepare_template_variants(
            template,
            scales=(1.0, 0.95, 1.05),
            rotations=(0,),
            match_mode=core.MATCH_MODE_STATIC,
        )
        original_match = core._best_variant_match

        with patch.object(core, "_best_variant_match", wraps=original_match) as matcher:
            score, location, _scale = core.match_template_multiscale(
                frame,
                template,
                variants=variants,
                match_mode=core.MATCH_MODE_STATIC,
                allow_coarse=False,
                early_exit_score=0.90,
            )

        self.assertGreaterEqual(score, 0.90)
        self.assertEqual(location, (47, 31))
        self.assertEqual(matcher.call_count, 1)

    def test_collect_all_infers_colored_text_mode_from_supplied_variants(self):
        template = self._text_tile("#2212", (54, 111, 99))
        frame = np.full((80, 260, 3), (54, 111, 99), dtype=np.uint8)
        frame[25:57, 70:200] = template
        variants = core.prepare_template_variants(
            template,
            scales=(1.0,),
            match_mode=core.MATCH_MODE_TEXT,
        )

        matches = core.find_template_matches(
            frame,
            template,
            0.90,
            collect_all=True,
            variants=variants,
        )

        self.assertEqual([(match.x, match.y) for match in matches], [(70, 25)])

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

    def test_rally_condition_regions_keep_the_existing_small_search_path(self):
        rng = np.random.default_rng(140)
        cases = (
            ((807, 222), (81, 142)),
            ((794, 384), (39, 36)),
        )
        for frame_size, template_size in cases:
            with self.subTest(frame_size=frame_size, template_size=template_size):
                frame = rng.integers(
                    0,
                    256,
                    (*frame_size, 3),
                    dtype=np.uint8,
                )
                template = rng.integers(
                    0,
                    256,
                    (*template_size, 3),
                    dtype=np.uint8,
                )
                with patch.object(
                    core,
                    "_parallel_full_resolution_multiscale_match",
                    side_effect=AssertionError("large-screen path used"),
                ):
                    core.match_template_multiscale(
                        frame,
                        template,
                        scales=core.MACRO_DEFAULT_SCALES,
                        rotations=(0,),
                        match_mode=core.MATCH_MODE_STATIC,
                        allow_coarse=True,
                    )

    def test_coarse_search_verifies_exact_target_behind_phase_aligned_decoy(self):
        rng = np.random.default_rng(1)
        template = rng.integers(0, 256, (40, 40, 3), dtype=np.uint8)
        decoy = template.copy()
        decoy[3:10, 3:10] = 255 - decoy[3:10, 3:10]
        frame = np.zeros((710, 710, 3), dtype=np.uint8)
        frame[100:140, 100:140] = decoy
        frame[501:541, 501:541] = template

        score, location, scale = core.match_template_multiscale(
            frame,
            template,
            scales=core.MACRO_DEFAULT_SCALES,
            rotations=(0,),
            match_mode=core.MATCH_MODE_STATIC,
            allow_coarse=True,
            early_exit_score=0.90,
        )

        self.assertGreaterEqual(score, 0.99)
        self.assertEqual(location, (501, 501))
        self.assertEqual(scale, 1.0)

    def test_large_search_prefers_exact_pixels_over_float32_rounded_decoy(self):
        rng = np.random.default_rng(0)
        template = rng.integers(0, 256, (40, 40, 3), dtype=np.uint8)
        decoy = template.copy()
        decoy[3, 5, 1] = (int(decoy[3, 5, 1]) + 1) % 256
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        frame[100:140, 100:140] = decoy
        frame[900:940, 1700:1740] = template

        matches = core.find_template_matches(
            frame,
            template,
            0.99,
            collect_all=False,
            scales=core.MACRO_DEFAULT_SCALES,
            rotations=(0,),
            match_mode=core.MATCH_MODE_STATIC,
            allow_coarse=True,
        )

        self.assertEqual(len(matches), 1)
        self.assertEqual((matches[0].x, matches[0].y), (1700, 900))
        self.assertEqual(matches[0].score, 1.0)

    def test_coarse_search_expands_past_many_phase_aligned_decoys(self):
        rng = np.random.default_rng(1)
        template = rng.integers(0, 256, (40, 40, 3), dtype=np.uint8)
        frame = np.zeros((900, 1300, 3), dtype=np.uint8)
        decoy_positions = [
            (100, 100),
            (300, 100),
            (500, 100),
            (700, 100),
            (900, 100),
            (1100, 100),
            (100, 300),
            (300, 300),
        ]
        for index, (x, y) in enumerate(decoy_positions):
            decoy = template.copy()
            top = 3 + index % 3
            decoy[top : top + 7, 3:10] = 255 - decoy[top : top + 7, 3:10]
            frame[y : y + 40, x : x + 40] = decoy
        frame[801:841, 1201:1241] = template

        for confidence in (0.90, 0.95):
            with self.subTest(confidence=confidence):
                matches = core.find_template_matches(
                    frame,
                    template,
                    confidence,
                    collect_all=False,
                    scales=core.MACRO_DEFAULT_SCALES,
                    rotations=(0,),
                    match_mode=core.MATCH_MODE_STATIC,
                    allow_coarse=True,
                    early_exit_score=confidence,
                )

                self.assertEqual(len(matches), 1)
                self.assertEqual((matches[0].x, matches[0].y), (1201, 801))
                self.assertGreaterEqual(matches[0].score, 0.99)

    def test_large_search_exact_target_survives_twenty_four_stronger_proposals(self):
        rng = np.random.default_rng(0)
        template = rng.integers(0, 256, (40, 40, 3), dtype=np.uint8)
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        positions = [
            (30 + 110 * (index % 8), 30 + 130 * (index // 8)) for index in range(24)
        ]
        for index, (x, y) in enumerate(positions):
            decoy = template.copy()
            top = 2 + index % 5
            decoy[top : top + 7, 3:10] = 255 - decoy[top : top + 7, 3:10]
            frame[y : y + 40, x : x + 40] = decoy
        frame[401:441, 901:941] = template

        for confidence in (0.85, 0.95):
            with self.subTest(confidence=confidence):
                matches = core.find_template_matches(
                    frame,
                    template,
                    confidence,
                    collect_all=False,
                    scales=core.MACRO_DEFAULT_SCALES,
                    rotations=(0,),
                    match_mode=core.MATCH_MODE_STATIC,
                    allow_coarse=True,
                )

                self.assertEqual(len(matches), 1)
                self.assertEqual((matches[0].x, matches[0].y), (901, 401))
                self.assertEqual(matches[0].score, 1.0)

    def test_coarse_search_checks_every_rotated_scale(self):
        rng = np.random.default_rng(1)
        template = rng.integers(0, 256, (40, 40, 3), dtype=np.uint8)
        variants = core.prepare_template_variants(
            template,
            scales=core.ALERT_DEFAULT_SCALES,
            rotations=core.DEFAULT_ROTATIONS,
            match_mode=core.MATCH_MODE_ANIMATED,
        )
        large_decoy_variant = next(
            variant
            for variant in variants
            if variant["angle"] == 0.0 and variant["scale"] == 1.5
        )
        exact_variant = next(
            variant
            for variant in variants
            if variant["angle"] == 5.0 and variant["scale"] == 0.5
        )
        decoy = large_decoy_variant["image"].copy()
        decoy[3:13, 3:13] = 255 - decoy[3:13, 3:13]
        exact = exact_variant["image"]
        frame = np.zeros((710, 710, 3), dtype=np.uint8)
        frame[100 : 100 + decoy.shape[0], 100 : 100 + decoy.shape[1]] = decoy
        frame[501 : 501 + exact.shape[0], 501 : 501 + exact.shape[1]] = exact

        score, location, scale = core.match_template_multiscale(
            frame,
            template,
            variants=variants,
            match_mode=core.MATCH_MODE_ANIMATED,
            allow_coarse=True,
        )

        self.assertGreaterEqual(score, 0.99)
        self.assertEqual(location, (501, 501))
        self.assertEqual(scale, 0.5)

    def test_large_threshold_mode_stops_after_nonexact_accepted_batch(self):
        rng = np.random.default_rng(40)
        template = rng.integers(0, 256, (40, 40, 3), dtype=np.uint8)
        candidate = template.copy()
        candidate[3, 5, 1] = (int(candidate[3, 5, 1]) + 1) % 256
        frame = np.zeros((710, 710, 3), dtype=np.uint8)
        frame[401:441, 501:541] = candidate
        variants = core.prepare_template_variants(
            template,
            scales=core.ALERT_DEFAULT_SCALES,
            rotations=(0,),
            match_mode=core.MATCH_MODE_STATIC,
        )
        original_match = core._best_variant_match

        with patch.object(core, "_best_variant_match", wraps=original_match) as matcher:
            score, location, _scale = core.match_template_multiscale(
                frame,
                template,
                variants=variants,
                match_mode=core.MATCH_MODE_STATIC,
                allow_coarse=True,
                early_exit_score=0.90,
            )

        self.assertGreaterEqual(score, 0.90)
        self.assertEqual(location, (501, 401))
        self.assertLessEqual(
            matcher.call_count,
            core.MAX_PARALLEL_VARIANT_WORKERS,
        )

    def test_large_threshold_mode_exhausts_variants_to_prove_absence(self):
        rng = np.random.default_rng(42)
        template = rng.integers(0, 256, (40, 40, 3), dtype=np.uint8)
        frame = rng.integers(0, 256, (710, 710, 3), dtype=np.uint8)
        variants = core.prepare_template_variants(
            template,
            scales=core.ALERT_DEFAULT_SCALES,
            rotations=(0,),
            match_mode=core.MATCH_MODE_STATIC,
        )
        original_match = core._best_variant_match

        with patch.object(core, "_best_variant_match", wraps=original_match) as matcher:
            score, location, _scale = core.match_template_multiscale(
                frame,
                template,
                variants=variants,
                match_mode=core.MATCH_MODE_STATIC,
                allow_coarse=True,
                early_exit_score=0.99,
            )

        self.assertLess(score, 0.99)
        self.assertIsNotNone(location)
        self.assertEqual(matcher.call_count, len(variants))

    def test_coarse_search_uses_each_rotations_own_spatial_proposals(self):
        rng = np.random.default_rng(41)
        template = rng.integers(0, 256, (40, 40, 3), dtype=np.uint8)
        variants = core.prepare_template_variants(
            template,
            scales=core.ALERT_DEFAULT_SCALES,
            rotations=core.DEFAULT_ROTATIONS,
            match_mode=core.MATCH_MODE_ANIMATED,
        )
        zero_variant = next(
            variant
            for variant in variants
            if variant["angle"] == 0.0 and variant["scale"] == 1.0
        )
        decoy_positions = [
            (10 + 100 * column, 10 + 100 * row)
            for row in range(6)
            for column in range(6)
        ][:33]

        for angle in (-8.0, 8.0):
            exact_variant = next(
                variant
                for variant in variants
                if variant["angle"] == angle and variant["scale"] == 1.0
            )
            frame = np.zeros((710, 710, 3), dtype=np.uint8)
            for index, (x, y) in enumerate(decoy_positions):
                decoy = zero_variant["image"].copy()
                top = 2 + index % 5
                decoy[top : top + 8, 4:16] = 255 - decoy[top : top + 8, 4:16]
                frame[y : y + 40, x : x + 40] = decoy
            frame[651:691, 651:691] = exact_variant["image"]

            for confidence in (0.90, 0.95):
                with self.subTest(angle=angle, confidence=confidence):
                    matches = core.find_template_matches(
                        frame,
                        template,
                        confidence,
                        collect_all=False,
                        variants=variants,
                        match_mode=core.MATCH_MODE_ANIMATED,
                        allow_coarse=True,
                        early_exit_score=confidence,
                    )

                    self.assertEqual(len(matches), 1)
                    self.assertEqual((matches[0].x, matches[0].y), (651, 651))
                    self.assertGreaterEqual(matches[0].score, 0.99)
                    self.assertEqual(matches[0].angle, angle)

    def test_rotated_exact_target_beats_near_perfect_rotated_decoy(self):
        rng = np.random.default_rng(0)
        template = rng.integers(0, 256, (40, 40, 3), dtype=np.uint8)
        variants = core.prepare_template_variants(
            template,
            scales=(1.0, 0.95),
            rotations=core.DEFAULT_ROTATIONS,
            match_mode=core.MATCH_MODE_ANIMATED,
        )
        exact_variant = next(
            variant
            for variant in variants
            if variant["angle"] == -8.0 and variant["scale"] == 1.0
        )
        exact = exact_variant["image"]
        decoy = exact.copy()
        decoy[3, 5, 1] = (int(decoy[3, 5, 1]) + 1) % 256
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        frame[100:140, 100:140] = decoy
        frame[401:441, 901:941] = exact

        score, location, scale, matched_variant = core.match_template_multiscale(
            frame,
            template,
            variants=variants,
            match_mode=core.MATCH_MODE_ANIMATED,
            allow_coarse=True,
            return_details=True,
        )

        self.assertEqual(location, (901, 401))
        self.assertEqual(score, 1.0)
        self.assertEqual(scale, 1.0)
        self.assertEqual(matched_variant["angle"], -8.0)

    def test_rotated_exact_target_survives_twenty_four_rotated_decoys(self):
        rng = np.random.default_rng(0)
        template = rng.integers(0, 256, (40, 40, 3), dtype=np.uint8)
        variants = core.prepare_template_variants(
            template,
            scales=(1.0, 0.95),
            rotations=core.DEFAULT_ROTATIONS,
            match_mode=core.MATCH_MODE_ANIMATED,
        )
        exact_variant = next(
            variant
            for variant in variants
            if variant["angle"] == -8.0 and variant["scale"] == 1.0
        )
        exact = exact_variant["image"]
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        positions = [
            (30 + 110 * (index % 8), 30 + 130 * (index // 8)) for index in range(24)
        ]
        for index, (x, y) in enumerate(positions):
            decoy = exact.copy()
            top = 2 + index % 5
            decoy[top : top + 7, 3:10] = 255 - decoy[top : top + 7, 3:10]
            frame[y : y + 40, x : x + 40] = decoy
        frame[401:441, 901:941] = exact

        score, location, scale, matched_variant = core.match_template_multiscale(
            frame,
            template,
            variants=variants,
            match_mode=core.MATCH_MODE_ANIMATED,
            allow_coarse=True,
            return_details=True,
        )

        self.assertEqual(location, (901, 401))
        self.assertEqual(score, 1.0)
        self.assertEqual(scale, 1.0)
        self.assertEqual(matched_variant["angle"], -8.0)

    def test_large_search_stops_after_verified_exact_batch(self):
        rng = np.random.default_rng(43)
        template = rng.integers(0, 256, (40, 40, 3), dtype=np.uint8)
        frame = rng.integers(0, 40, (710, 710, 3), dtype=np.uint8)
        frame[300:340, 400:440] = template
        original_match = core._best_variant_match

        with patch.object(core, "_best_variant_match", wraps=original_match) as matcher:
            score, location, _scale = core.match_template_multiscale(
                frame,
                template,
                scales=core.ALERT_DEFAULT_SCALES,
                rotations=core.DEFAULT_ROTATIONS,
                match_mode=core.MATCH_MODE_ANIMATED,
                allow_coarse=True,
                early_exit_score=0.90,
            )

        self.assertGreaterEqual(score, 0.99)
        self.assertEqual(location, (400, 300))
        self.assertLessEqual(
            matcher.call_count,
            core.MAX_PARALLEL_VARIANT_WORKERS,
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

    def test_near_perfect_tie_break_is_bounded(self):
        screen = np.zeros((1001, 1001), dtype=np.uint8)
        template = np.zeros((2, 2), dtype=np.uint8)
        scores = np.ones((1000, 1000), dtype=np.float32)
        squared_differences = np.zeros_like(scores)

        with (
            patch.object(core, "_score_map", return_value=scores),
            patch.object(
                core.cv2,
                "matchTemplate",
                return_value=squared_differences,
            ),
            patch.object(
                core,
                "_pixel_mean_squared_error",
                return_value=1.0,
            ) as pixel_error,
        ):
            score, location = core._best_variant_match(
                screen,
                template,
                low_variance=False,
            )

        self.assertEqual(score, 1.0)
        self.assertIsNotNone(location)
        self.assertLessEqual(
            pixel_error.call_count,
            core.MAX_PIXEL_TIE_CANDIDATES + 1,
        )

    def test_exact_target_survives_more_than_sixty_four_float_ranked_decoys(self):
        rng = np.random.default_rng(149)
        template = rng.integers(0, 120, (4, 4), dtype=np.uint8)
        candidate_count = 80
        stride = template.shape[1] + 1
        screen = np.full(
            (template.shape[0], candidate_count * stride),
            255,
            dtype=np.uint8,
        )
        scores = np.full(
            (
                1,
                screen.shape[1] - template.shape[1] + 1,
            ),
            -1.0,
            dtype=np.float32,
        )
        squared_differences = np.full(scores.shape, 10_000.0, dtype=np.float32)
        for index in range(candidate_count - 1):
            x = index * stride
            decoy = template.copy()
            decoy[index % 4, (index // 4) % 4] += 1
            screen[:, x : x + template.shape[1]] = decoy
            scores[0, x] = 1.0
            squared_differences[0, x] = float(index)
        exact_x = (candidate_count - 1) * stride
        screen[:, exact_x : exact_x + template.shape[1]] = template
        scores[0, exact_x] = 1.0
        # Simulate OpenCV's float32 SQDIFF ordering the exact patch behind all
        # corrupt patches, so a 64-item approximate shortlist cannot contain it.
        squared_differences[0, exact_x] = 20_000.0

        with (
            patch.object(core, "_score_map", return_value=scores),
            patch.object(
                core.cv2,
                "matchTemplate",
                return_value=squared_differences,
            ),
        ):
            score, location, pixel_error = core._best_variant_match(
                screen,
                template,
                low_variance=False,
                return_pixel_error=True,
            )

        self.assertEqual(score, 1.0)
        self.assertEqual(location, (exact_x, 0))
        self.assertEqual(pixel_error, 0.0)

    def test_best_variant_match_observes_cancellation_after_score_map(self):
        cancel_event = threading.Event()
        screen = np.zeros((40, 40), dtype=np.uint8)
        template = np.zeros((3, 3), dtype=np.uint8)

        def score_and_cancel(*_args):
            cancel_event.set()
            return np.ones((38, 38), dtype=np.float32)

        with (
            patch.object(core, "_score_map", side_effect=score_and_cancel),
            patch.object(core, "_pixel_mean_squared_error") as pixel_error,
        ):
            score, location = core._best_variant_match(
                screen,
                template,
                low_variance=False,
                cancel_event=cancel_event,
            )

        self.assertEqual(score, -1.0)
        self.assertIsNone(location)
        pixel_error.assert_not_called()

    def test_colored_text_shape_checks_distinct_candidates_beyond_top_eight(self):
        screen = np.zeros((7, 63), dtype=np.uint8)
        template = np.zeros((3, 3), dtype=np.uint8)
        scores = np.full((5, 61), -1.0, dtype=np.float32)
        wrong_locations = [(x, 1) for x in range(0, 48, 4)]
        for rank, (x, y) in enumerate(wrong_locations):
            scores[y, x] = 0.99 - rank * 0.01
        correct_location = (52, 1)
        scores[correct_location[1], correct_location[0]] = 0.40

        def shape_score(_screen, _template, location):
            return 0.95 if location == correct_location else 0.20

        with (
            patch.object(core, "_score_map", return_value=scores),
            patch.object(core, "_text_shape_score", side_effect=shape_score),
            patch.object(core, "_pixel_mean_squared_error", return_value=10.0),
        ):
            score, location = core._best_variant_match(
                screen,
                template,
                low_variance=False,
                text_shape=True,
            )

        self.assertEqual(location, correct_location)
        self.assertAlmostEqual(score, 0.95)


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
