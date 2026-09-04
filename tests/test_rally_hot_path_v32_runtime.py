import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from macro_clicker import rally_hot_path_v32_runtime as v32
from macro_clicker.level_ocr import LevelOcrReader


class RallyHotPathV32RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.reader = LevelOcrReader()
        self.frame = np.zeros((20, 80, 3), dtype=np.uint8)

    def _run_special_read(self, result):
        self.reader._preprocess_fast_variant = lambda _frame: "fast"
        self.reader._run_text_recognition = lambda _image: result
        old_active = getattr(v32._THREAD_STATE, "three_team_row_read", False)
        old_stats = getattr(v32._THREAD_STATE, "stats", None)
        v32._THREAD_STATE.three_team_row_read = True
        v32._THREAD_STATE.stats = {"literal": 0, "provisional": 0, "unread": 0}
        try:
            return v32._read_level_locked(self.reader, self.frame), dict(v32._THREAD_STATE.stats)
        finally:
            v32._THREAD_STATE.three_team_row_read = old_active
            v32._THREAD_STATE.stats = old_stats

    def test_literal_lv_high_confidence_remains_strong(self):
        source = self.reader._result_from_entries([("Lv.80", 0.97)], "fake")
        result, stats = self._run_special_read(source)

        self.assertEqual(result.level, 80)
        self.assertAlmostEqual(result.confidence, 0.97)
        self.assertEqual(stats["literal"], 1)
        self.assertEqual(stats["provisional"], 0)

    def test_bare_high_confidence_number_is_forced_provisional(self):
        source = self.reader._result_from_entries([("80", 0.97)], "fake")
        result, stats = self._run_special_read(source)

        self.assertEqual(result.level, 80)
        self.assertLess(result.confidence, LevelOcrReader.STRONG_ACCEPT_CONFIDENCE)
        self.assertEqual(stats["provisional"], 1)

    def test_corrected_prefix_is_not_one_crop_strong(self):
        source = self.reader._result_from_entries([("ly80", 0.99)], "fake")
        result, _stats = self._run_special_read(source)

        self.assertEqual(result.level, 80)
        self.assertLess(result.confidence, LevelOcrReader.STRONG_ACCEPT_CONFIDENCE)

    def test_low_confidence_single_crop_stays_unreadable(self):
        source = self.reader._result_from_entries([("Lv.80", 0.40)], "fake")
        result, stats = self._run_special_read(source)

        self.assertIsNone(result.level)
        self.assertEqual(stats["unread"], 1)

    def test_two_team_row_reader_delegates_unchanged(self):
        engine = SimpleNamespace(
            _rally_hot_path_three_team=False,
            log=lambda _msg: None,
        )
        expected = 60

        with patch.object(v32, "_ORIGINAL_READ_LEVEL_FOR_ROW", return_value=expected) as delegated:
            result = v32._read_level_for_row(engine, object(), {})

        self.assertEqual(result, expected)
        delegated.assert_called_once()

    def test_three_team_row_reader_logs_cross_crop_timing(self):
        messages = []
        engine = SimpleNamespace(
            _rally_hot_path_three_team=True,
            log=messages.append,
        )

        def delegated(_engine, _action, _reference):
            v32._stats()["provisional"] += 2
            return 80

        with patch.object(v32, "_ORIGINAL_READ_LEVEL_FOR_ROW", side_effect=delegated), patch.object(
            v32.time, "perf_counter", side_effect=[10.0, 10.4]
        ):
            result = v32._read_level_for_row(engine, object(), {})

        self.assertEqual(result, 80)
        self.assertTrue(any("result=80 elapsed=0.400s" in line for line in messages))
        self.assertTrue(any("provisional=2" in line for line in messages))


if __name__ == "__main__":
    unittest.main()
