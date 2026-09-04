import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from macro_clicker import rally_hot_path_v28_runtime as v28
from macro_clicker.level_ocr import LevelOcrReader


class RallyHotPathV28RuntimeTests(unittest.TestCase):
    def tearDown(self):
        v28._THREAD_STATE.three_team_row_read = False
        v28._THREAD_STATE.reordered_fallback = False

    @staticmethod
    def _engine(three_team=True):
        engine = SimpleNamespace(
            _rally_hot_path_three_team=three_team,
            log_messages=[],
        )
        engine.log = engine.log_messages.append
        return engine

    def test_three_team_fallback_tries_independent_plain_crops_first(self):
        reader = LevelOcrReader()
        original = [
            f"r{region}-{kind}"
            for region in range(5)
            for kind in ("plain", "sharp", "threshold")
        ]

        v28._THREAD_STATE.three_team_row_read = True
        with patch.object(v28, "_ORIGINAL_PREPROCESS_VARIANTS", return_value=original):
            reordered = v28._preprocess_variants(reader, object())

        self.assertEqual(
            reordered[:5],
            ["r2-plain", "r3-plain", "r0-plain", "r1-plain", "r4-plain"],
        )
        self.assertEqual(len(reordered), len(original))
        self.assertEqual(set(reordered), set(original))
        self.assertTrue(v28._THREAD_STATE.reordered_fallback)

    def test_inactive_path_preserves_original_variant_order_and_object(self):
        reader = LevelOcrReader()
        original = ["same", "order"]

        with patch.object(v28, "_ORIGINAL_PREPROCESS_VARIANTS", return_value=original):
            result = v28._preprocess_variants(reader, object())

        self.assertIs(result, original)
        self.assertFalse(v28._THREAD_STATE.reordered_fallback)

    def test_bare_number_still_requires_repeated_consensus_but_gets_it_early(self):
        reader = LevelOcrReader()
        original = [
            f"r{region}-{kind}"
            for region in range(5)
            for kind in ("plain", "sharp", "threshold")
        ]
        calls = []

        reader._preprocess_fast_variant = lambda _frame: "r1-plain"

        def original_preprocess(_reader, _frame):
            return list(original)

        def recognize(image):
            calls.append(image)
            if image in {"r1-plain", "r2-plain"}:
                return reader._result_from_entries([("85", 0.99)], "fake")
            return reader._result_from_entries([], "fake")

        reader._run_text_recognition = recognize
        reader._get_engine = lambda: self.fail("full OCR should not be needed")

        v28._THREAD_STATE.three_team_row_read = True
        with patch.object(v28, "_ORIGINAL_PREPROCESS_VARIANTS", original_preprocess):
            result = reader._read_level_locked(
                np.zeros((12, 24, 3), dtype=np.uint8)
            )

        self.assertEqual(result.level, 85)
        self.assertGreaterEqual(
            result.confidence,
            reader.STRONG_ACCEPT_CONFIDENCE,
        )
        self.assertEqual(calls, ["r1-plain", "r2-plain"])

    def test_two_team_row_reader_delegates_without_enabling_reorder(self):
        engine = self._engine(three_team=False)
        expected = 55

        def original(_engine, _action, _reference):
            self.assertFalse(
                getattr(v28._THREAD_STATE, "three_team_row_read", False)
            )
            return expected

        with patch.object(v28, "_ORIGINAL_READ_LEVEL_FOR_ROW", original):
            result = v28._read_level_for_row(engine, object(), {})

        self.assertEqual(result, expected)
        self.assertEqual(engine.log_messages, [])

    def test_three_team_wrapper_logs_only_when_fallback_reordering_was_used(self):
        engine = self._engine(three_team=True)

        def original(_engine, _action, _reference):
            v28._THREAD_STATE.reordered_fallback = True
            return 85

        with patch.object(v28, "_ORIGINAL_READ_LEVEL_FOR_ROW", original), patch.object(
            v28.time, "perf_counter", side_effect=[10.0, 10.25]
        ):
            result = v28._read_level_for_row(engine, object(), {})

        self.assertEqual(result, 85)
        self.assertTrue(
            any(
                "plain-crop-first consensus ordering" in line
                and "0.250s" in line
                for line in engine.log_messages
            )
        )


if __name__ == "__main__":
    unittest.main()
