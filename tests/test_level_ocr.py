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
        self.assertEqual(reader._extract_level("ALV-.4070"), 40)
        self.assertEqual(reader._extract_level("L.500"), 50)

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

        reader._get_recognition_engine = lambda: calls.append("recognition") or FakeRecognitionEngine()
        reader._get_engine = lambda: calls.append("full") or object()

        self.assertTrue(reader.warm_up())
        self.assertEqual(calls[0], "recognition")
        self.assertEqual(calls[1][0], "predict")
        self.assertNotIn("full", calls)

    def test_numpy_entry_arrays_are_supported_without_truth_value_errors(self):
        reader = LevelOcrReader()
        raw = [{
            "rec_texts": np.array(["Lv.22", "Ready"]),
            "rec_scores": np.array([0.82, 0.99]),
        }]

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
        results = iter([
            reader._result_from_entries([("Lv.10", 0.71)], "fake"),
            reader._result_from_entries([("Lv.20", 0.88)], "fake"),
        ])
        reader._run_text_recognition = lambda _image: next(results)
        reader._get_engine = lambda: None

        result = reader._read_level_locked(np.zeros((4, 4, 3), dtype=np.uint8))

        self.assertEqual(result.level, 20)
        self.assertAlmostEqual(result.confidence, 0.88)

    def test_single_strong_result_is_confirmed_instead_of_returned_immediately(self):
        reader = LevelOcrReader()
        reader._preprocess_variants = lambda _frame: ["first", "second", "third"]
        results = iter([
            reader._result_from_entries([("Lv.10", 0.93)], "fake"),
            reader._result_from_entries([("Lv.20", 0.96)], "fake"),
            reader._result_from_entries([("Lv.20", 0.95)], "fake"),
        ])
        reader._run_text_recognition = lambda _image: next(results)
        reader._get_engine = lambda: None

        result = reader._read_level_locked(np.zeros((4, 4, 3), dtype=np.uint8))

        self.assertEqual(result.level, 20)
        self.assertAlmostEqual(result.confidence, 0.96)

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
