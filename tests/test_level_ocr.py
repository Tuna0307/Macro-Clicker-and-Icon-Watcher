import unittest

import numpy as np

from macro_clicker.level_ocr import LevelOcrReader


class LevelOcrReaderTests(unittest.TestCase):
    def test_extracts_level_after_lv_prefix(self):
        reader = LevelOcrReader()

        self.assertEqual(reader._extract_level("Lv.22"), 22)
        self.assertEqual(reader._extract_level("LV 105"), 105)

    def test_extracts_level_from_noisy_lv_text(self):
        reader = LevelOcrReader()

        self.assertEqual(reader._extract_level("LV-407"), 40)
        self.assertEqual(reader._extract_level("[LV-.4070"), 40)
        self.assertEqual(reader._extract_level("L.500"), 50)

    def test_prefix_corrections_only_apply_at_token_boundaries(self):
        reader = LevelOcrReader()

        for text, expected in (("iv.20", 20), ("1v.7", 7), ("ly30", 30)):
            with self.subTest(text=text):
                self.assertTrue(reader._has_level_prefix(text))
                self.assertEqual(reader._extract_level(text), expected)

    def test_ordinary_words_and_leading_l_words_are_not_level_prefixes(self):
        reader = LevelOcrReader()

        for text in (
            "give20",
            "alive40",
            "civil 55",
            "archive 12",
            "lucky7",
            "lastwar60",
            "love20",
            "ALV-.4070",
        ):
            with self.subTest(text=text):
                result = reader._result_from_entries([(text, 0.99)], "fake")
                self.assertFalse(reader._has_level_prefix(text))
                self.assertFalse(reader._is_strong_result(result))

    def test_hyphenated_real_three_digit_levels_are_not_truncated(self):
        reader = LevelOcrReader()

        self.assertEqual(reader._extract_level("Lv-105"), 105)
        self.assertEqual(reader._extract_level("Lv-150"), 150)
        self.assertEqual(reader._extract_level("Lv-060"), 60)
        self.assertEqual(reader._extract_level("Lv-300"), 300)
        self.assertEqual(reader._extract_level("Lv-301"), 30)

    def test_extracts_last_number_when_prefix_is_missing(self):
        reader = LevelOcrReader()

        self.assertEqual(reader._extract_level("monster 18 km 24"), 24)

    def test_extracts_entries_from_paddleocr_legacy_shape(self):
        reader = LevelOcrReader()
        raw = [[[[0, 0], [1, 0], [1, 1], [0, 1]], ("Lv.22", 0.94)]]

        entries = list(reader._extract_text_entries(raw))

        self.assertEqual(entries, [("Lv.22", 0.94)])

    def test_extracts_entries_from_paddleocr_predict_dict_shape(self):
        reader = LevelOcrReader()
        raw = [{"rec_texts": ["Lv.22"], "rec_scores": [0.95]}]

        entries = list(reader._extract_text_entries(raw))

        self.assertEqual(entries, [("Lv.22", 0.95)])

    def test_extracts_entries_from_text_recognition_shape(self):
        reader = LevelOcrReader()
        raw = [{"rec_text": "LVU22", "rec_score": 0.59}]

        entries = list(reader._extract_text_entries(raw))

        self.assertEqual(entries, [("LVU22", 0.59)])

    def test_warm_up_keeps_heavy_full_engine_lazy_when_recognition_is_ready(self):
        reader = LevelOcrReader()
        calls = []

        class FakeRecognitionEngine:
            def predict(self, image):
                calls.append(("predict", image.shape))
                return []

        reader._get_recognition_engine = lambda: (
            calls.append("recognition") or FakeRecognitionEngine()
        )
        reader._get_engine = lambda: calls.append("full") or object()

        self.assertTrue(reader.warm_up())
        self.assertEqual(calls[0], "recognition")
        self.assertEqual(calls[1][0], "predict")
        self.assertNotIn("full", calls)

    def test_numpy_entry_arrays_are_supported_without_truth_value_errors(self):
        reader = LevelOcrReader()
        raw = [
            {
                "rec_texts": np.array(["Lv.22", "Ready"]),
                "rec_scores": np.array([0.82, 0.99]),
            }
        ]

        self.assertEqual(
            list(reader._extract_text_entries(raw)),
            [("Lv.22", 0.82), ("Ready", 0.99)],
        )

    def test_level_confidence_comes_from_the_entry_that_contains_the_level(self):
        reader = LevelOcrReader()

        result = reader._result_from_entries(
            [("Lv.99", 0.21), ("Unrelated status", 0.99)],
            "fake",
        )

        self.assertEqual(result.level, 99)
        self.assertEqual(result.text, "Lv.99")
        self.assertAlmostEqual(result.confidence, 0.21)

    def test_reader_selects_best_safe_variant_instead_of_first_number(self):
        reader = LevelOcrReader()
        reader._preprocess_variants = lambda _frame: ["first", "second"]
        results = iter(
            [
                reader._result_from_entries([("Lv.10", 0.71)], "fake"),
                reader._result_from_entries([("Lv.20", 0.88)], "fake"),
            ]
        )
        reader._run_text_recognition = lambda _image: next(results)
        reader._get_engine = lambda: None

        result = reader._read_level_locked(np.zeros((4, 4, 3), dtype=np.uint8))

        self.assertEqual(result.level, 20)
        self.assertAlmostEqual(result.confidence, 0.88)

    def test_fast_path_accepts_strong_prefixed_result_without_extra_ocr_calls(self):
        reader = LevelOcrReader()
        reader._preprocess_variants = lambda _frame: [
            "region-0-plain",
            "region-0-sharp",
            "region-0-threshold",
            "focused-plain",
            "focused-sharp",
        ]
        reader._preprocess_fast_variant = lambda _frame: "focused-plain"
        calls = []

        def recognize(image):
            calls.append(image)
            return reader._result_from_entries([("Lv.7", 0.97)], "fake")

        reader._run_text_recognition = recognize
        reader._get_engine = lambda: self.fail("full OCR should remain lazy")

        result = reader._read_level_locked(np.zeros((4, 4, 3), dtype=np.uint8))

        self.assertEqual(result.level, 7)
        self.assertAlmostEqual(result.confidence, 0.97)
        self.assertEqual(calls, ["focused-plain"])

    def test_fast_path_falls_back_for_uncertain_result_without_duplicate_call(self):
        reader = LevelOcrReader()
        variants = ["plain", "sharp", "threshold", "focused", "focused-sharp"]
        reader._preprocess_variants = lambda _frame: variants
        reader._preprocess_fast_variant = lambda _frame: "focused"
        calls = []
        results = {
            "focused": reader._result_from_entries([("Lv.10", 0.89)], "fake"),
            "plain": reader._result_from_entries([("Lv.20", 0.96)], "fake"),
            "sharp": reader._result_from_entries([("Lv.20", 0.95)], "fake"),
            "threshold": reader._result_from_entries([], "fake"),
            "focused-sharp": reader._result_from_entries([], "fake"),
        }

        def recognize(image):
            calls.append(image)
            return results[image]

        reader._run_text_recognition = recognize
        reader._get_engine = lambda: None

        result = reader._read_level_locked(np.zeros((4, 4, 3), dtype=np.uint8))

        self.assertEqual(result.level, 20)
        self.assertAlmostEqual(result.confidence, 0.96)
        self.assertEqual(calls[0], "focused")
        self.assertEqual(calls.count("focused"), 1)

    def test_fast_path_requires_literal_lv_or_level_prefix(self):
        reader = LevelOcrReader()
        variants = ["plain", "sharp", "threshold", "focused"]
        reader._preprocess_variants = lambda _frame: variants
        reader._preprocess_fast_variant = lambda _frame: "focused"
        calls = []
        results = {
            "focused": reader._result_from_entries([("Ly-15", 0.99)], "fake"),
            "plain": reader._result_from_entries([("Lv.15", 0.93)], "fake"),
            "sharp": reader._result_from_entries([("Lv.15", 0.92)], "fake"),
            "threshold": reader._result_from_entries([], "fake"),
        }

        def recognize(image):
            calls.append(image)
            return results[image]

        reader._run_text_recognition = recognize
        reader._get_engine = lambda: None

        result = reader._read_level_locked(np.zeros((4, 4, 3), dtype=np.uint8))

        self.assertEqual(result.level, 15)
        self.assertGreater(len(calls), 1)

    def test_fast_path_does_not_accept_corrected_prefix(self):
        reader = LevelOcrReader()

        for text in ("iv.20", "1v.7", "ly30"):
            with self.subTest(text=text):
                result = reader._result_from_entries([(text, 0.99)], "fake")
                self.assertFalse(reader._is_fast_path_result(result))

    def test_single_unprefixed_high_confidence_number_is_provisional(self):
        reader = LevelOcrReader()
        reader._preprocess_fast_variant = lambda _frame: "focused"
        reader._preprocess_variants = lambda _frame: ["focused"]
        reader._run_text_recognition = lambda _image: reader._result_from_entries(
            [("give20", 0.99)], "fake"
        )
        reader._get_engine = lambda: None

        result = reader._read_level_locked(np.zeros((4, 4, 3), dtype=np.uint8))

        self.assertEqual(result.level, 20)
        self.assertGreaterEqual(result.confidence, reader.MIN_ACCEPT_CONFIDENCE)
        self.assertLess(result.confidence, reader.STRONG_ACCEPT_CONFIDENCE)

    def test_repeated_unprefixed_number_can_reach_strong_consensus(self):
        reader = LevelOcrReader()
        reader._preprocess_fast_variant = lambda _frame: "focused"
        reader._preprocess_variants = lambda _frame: ["second", "focused"]
        calls = []

        def recognize(image):
            calls.append(image)
            confidence = 0.99 if image == "focused" else 0.96
            return reader._result_from_entries([("give20", confidence)], "fake")

        reader._run_text_recognition = recognize
        reader._get_engine = lambda: None

        result = reader._read_level_locked(np.zeros((4, 4, 3), dtype=np.uint8))

        self.assertEqual(result.level, 20)
        self.assertAlmostEqual(result.confidence, 0.99)
        self.assertEqual(calls, ["focused", "second"])

    def test_safe_unprefixed_number_beats_unsafe_prefixed_number(self):
        reader = LevelOcrReader()

        result = reader._result_from_entries(
            [("Lv.20", 0.20), ("99", 0.95)],
            "fake",
        )

        self.assertEqual(result.level, 99)
        self.assertAlmostEqual(result.confidence, 0.95)

    def test_reader_rejects_low_confidence_number_when_no_fallback_is_available(self):
        reader = LevelOcrReader()
        reader._preprocess_variants = lambda _frame: ["only"]
        reader._run_text_recognition = lambda _image: reader._result_from_entries(
            [("Lv.99", 0.20)], "fake"
        )
        reader._get_engine = lambda: None

        result = reader._read_level_locked(np.zeros((4, 4, 3), dtype=np.uint8))

        self.assertIsNone(result.level)
        self.assertAlmostEqual(result.confidence, 0.20)

    def test_preprocess_variants_include_lower_text_band(self):
        reader = LevelOcrReader()
        frame = np.zeros((45, 150, 3), dtype=np.uint8)
        frame[12:, :, :] = 80

        variants = reader._preprocess_variants(frame)

        self.assertTrue(any(variant.shape[:2] == (132, 600) for variant in variants))


if __name__ == "__main__":
    unittest.main()
