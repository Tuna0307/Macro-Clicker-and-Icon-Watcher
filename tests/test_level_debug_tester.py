import tempfile
import unittest
from pathlib import Path

from engine import MacroEngine
from level_debug_tester import (
    evaluate_grid,
    load_labels,
    parse_float_list,
    save_labels,
    sorted_results,
    set_label,
)


class LevelDebugTesterTests(unittest.TestCase):
    def test_label_file_round_trips_by_filename(self):
        with tempfile.TemporaryDirectory() as tmp:
            label_path = Path(tmp) / "labels.json"
            labels = {}

            set_label(labels, Path(tmp) / "level_a.png", 15)
            save_labels(label_path, labels)

            self.assertEqual(load_labels(label_path), {"level_a.png": 15})

    def test_evaluate_grid_counts_correct_wrong_and_unread(self):
        crops = [Path("a.png"), Path("b.png"), Path("unlabeled.png")]
        labels = {"a.png": 15, "b.png": 30}

        def predictor(path, confidence, score_margin):
            if path.name == "a.png":
                return 15 if confidence >= 0.70 else 1115
            if path.name == "b.png":
                return None if score_margin >= 0.10 else 30
            return 99

        results = evaluate_grid(
            crops,
            labels,
            predictor,
            thresholds=[0.52, 0.70],
            margins=[0.0, 0.10],
        )

        by_setting = {(result.threshold, result.margin): result for result in results}
        self.assertEqual(by_setting[(0.52, 0.0)].summary, (1, 1, 0, 2))
        self.assertEqual(by_setting[(0.70, 0.0)].summary, (2, 0, 0, 2))
        self.assertEqual(by_setting[(0.70, 0.10)].summary, (1, 0, 1, 2))

    def test_evaluate_grid_returns_empty_when_no_crops_are_labeled(self):
        results = evaluate_grid(
            [Path("a.png")],
            {},
            lambda path, confidence, score_margin: 15,
            thresholds=[0.52],
            margins=[0.0],
        )

        self.assertEqual(results, [])

    def test_sorted_results_prefers_correct_then_fewer_errors(self):
        crops = [Path("a.png"), Path("b.png")]
        labels = {"a.png": 15, "b.png": 30}

        def predictor(path, confidence, score_margin):
            if confidence >= 0.70:
                return labels[path.name]
            return None if path.name == "a.png" else 99

        results = sorted_results(
            evaluate_grid(crops, labels, predictor, thresholds=[0.52, 0.70], margins=[0.0])
        )

        self.assertEqual(results[0].threshold, 0.70)
        self.assertEqual(results[0].summary, (2, 0, 0, 2))

    def test_parse_float_list_accepts_commas_and_spaces(self):
        self.assertEqual(parse_float_list("0.52, 0.7 0.75"), [0.52, 0.7, 0.75])

    def test_score_margin_drops_ambiguous_overlapping_digit_candidates(self):
        engine = object.__new__(MacroEngine)
        candidates = [
            {"digit": "4", "box": (10, 5, 24, 19), "score": 0.78},
            {"digit": "1", "box": (11, 5, 25, 19), "score": 0.76},
            {"digit": "5", "box": (32, 5, 46, 19), "score": 0.81},
        ]

        filtered = engine._filter_digit_candidates_by_margin(candidates, min_score_margin=0.05)

        self.assertEqual([candidate["digit"] for candidate in filtered], ["5"])


if __name__ == "__main__":
    unittest.main()
