import unittest

import numpy as np

from level_ocr import LevelOcrReader


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

    def test_warm_up_initializes_both_ocr_engines(self):
        reader = LevelOcrReader()
        calls = []

        class FakeRecognitionEngine:
            def predict(self, image):
                calls.append(("predict", image.shape))
                return []

        reader._get_recognition_engine = lambda: calls.append("recognition") or FakeRecognitionEngine()
        full_engine = object()
        reader._get_engine = lambda: calls.append("full") or full_engine
        reader._prime_full_engine = lambda engine: calls.append(("prime_full", engine))

        self.assertTrue(reader.warm_up())
        self.assertEqual(calls[0], "recognition")
        self.assertEqual(calls[1][0], "predict")
        self.assertEqual(calls[2], "full")
        self.assertEqual(calls[3], ("prime_full", full_engine))

    def test_preprocess_variants_include_lower_text_band(self):
        reader = LevelOcrReader()
        frame = np.zeros((45, 150, 3), dtype=np.uint8)
        frame[12:, :, :] = 80

        variants = reader._preprocess_variants(frame)

        self.assertTrue(any(variant.shape[:2] == (132, 600) for variant in variants))


if __name__ == "__main__":
    unittest.main()
