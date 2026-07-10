import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import cv2

from engine import MacroEngine


DEFAULT_CROP_DIR = Path("logs") / "level_debug"
DEFAULT_LABELS = DEFAULT_CROP_DIR / "labels.json"
DEFAULT_DIGIT_DIR = Path("templates") / "level_digits"
DEFAULT_THRESHOLDS = [0.52, 0.58, 0.62, 0.66, 0.70, 0.74]
DEFAULT_MARGINS = [0.0, 0.04, 0.08, 0.12]


@dataclass(frozen=True)
class TuneCase:
    crop: str
    expected: int
    predicted: int | None


@dataclass(frozen=True)
class TuneResult:
    threshold: float
    margin: float
    correct: int = 0
    wrong: int = 0
    unread: int = 0
    cases: tuple[TuneCase, ...] = field(default_factory=tuple)

    @property
    def total(self):
        return self.correct + self.wrong + self.unread

    @property
    def summary(self):
        return (self.correct, self.wrong, self.unread, self.total)


def parse_float_list(value):
    if not value:
        return []
    parts = value.replace(",", " ").split()
    return [float(part) for part in parts]


def load_labels(path=DEFAULT_LABELS):
    path = Path(path)
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    return {str(key): int(value) for key, value in data.items()}


def save_labels(path, labels):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(dict(sorted(labels.items())), handle, indent=2, sort_keys=True)
        handle.write("\n")


def set_label(labels, crop_path, level):
    labels[Path(crop_path).name] = int(level)


def iter_crops(crop_dir=DEFAULT_CROP_DIR):
    crop_dir = Path(crop_dir)
    if not crop_dir.exists():
        return []
    return sorted(
        path
        for path in crop_dir.iterdir()
        if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".bmp"}
    )


def evaluate_grid(crops, labels, predictor, thresholds, margins):
    labeled_crops = [Path(crop) for crop in crops if Path(crop).name in labels]
    if not labeled_crops:
        return []
    results = []
    for threshold in thresholds:
        for margin in margins:
            cases = []
            correct = wrong = unread = 0
            for crop in labeled_crops:
                expected = int(labels[crop.name])
                predicted = predictor(crop, threshold, margin)
                cases.append(TuneCase(crop=crop.name, expected=expected, predicted=predicted))
                if predicted is None:
                    unread += 1
                elif int(predicted) == expected:
                    correct += 1
                else:
                    wrong += 1
            results.append(
                TuneResult(
                    threshold=float(threshold),
                    margin=float(margin),
                    correct=correct,
                    wrong=wrong,
                    unread=unread,
                    cases=tuple(cases),
                )
            )
    return results


def sorted_results(results):
    return sorted(
        results,
        key=lambda result: (result.correct, -result.wrong, -result.unread, result.threshold, -result.margin),
        reverse=True,
    )


def make_digit_predictor(digit_dir=DEFAULT_DIGIT_DIR, min_digits=1):
    engine = object.__new__(MacroEngine)
    engine._digit_template_cache = {}
    templates = engine._load_digit_templates(str(digit_dir))
    if not templates:
        raise RuntimeError(f"No digit templates found in {digit_dir}")

    def predict(crop_path, threshold, margin):
        frame = cv2.imread(str(crop_path), cv2.IMREAD_COLOR)
        if frame is None:
            return None
        return engine._read_level_from_frame(
            frame,
            templates,
            confidence=float(threshold),
            min_digits=int(min_digits),
            min_score_margin=float(margin),
        )

    return predict


def resolve_crop(crop_dir, crop_ref):
    crop_dir = Path(crop_dir)
    crop_ref_path = Path(crop_ref)
    if crop_ref_path.exists():
        return crop_ref_path
    crops = iter_crops(crop_dir)
    matches = [
        crop
        for crop in crops
        if crop.name == crop_ref or crop.stem == crop_ref or crop_ref in crop.name
    ]
    if not matches:
        raise ValueError(f"No crop matched '{crop_ref}' in {crop_dir}")
    if len(matches) > 1:
        names = ", ".join(crop.name for crop in matches[:8])
        raise ValueError(f"'{crop_ref}' matched multiple crops: {names}")
    return matches[0]


def print_crop_list(crops, labels):
    if not crops:
        print("No level debug crops found.")
        return
    for crop in crops:
        label = labels.get(crop.name, "?")
        print(f"{crop.name}\tlabel={label}")


def print_tune_results(results, limit):
    if not results:
        print("No labeled crops to evaluate.")
        return
    print("threshold  margin  correct  wrong  unread  total")
    for result in sorted_results(results)[:limit]:
        print(
            f"{result.threshold:>9.2f}  {result.margin:>6.2f}  "
            f"{result.correct:>7}  {result.wrong:>5}  {result.unread:>6}  {result.total:>5}"
        )
    best = sorted_results(results)[0]
    misses = [case for case in best.cases if case.predicted != case.expected]
    print(f"\nBest: confidence={best.threshold:.2f}, score_margin={best.margin:.2f}")
    if misses:
        print("Misses for best setting:")
        for case in misses[:20]:
            value = "unread" if case.predicted is None else str(case.predicted)
            print(f"  {case.crop}: expected {case.expected}, got {value}")


def build_parser():
    parser = argparse.ArgumentParser(
        description="Tune the level digit fallback against saved logs/level_debug crops."
    )
    parser.add_argument("--crop-dir", default=str(DEFAULT_CROP_DIR))
    parser.add_argument("--labels", default=str(DEFAULT_LABELS))
    parser.add_argument("--digit-dir", default=str(DEFAULT_DIGIT_DIR))
    parser.add_argument("--min-digits", type=int, default=1)
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list", help="Show saved crops and their current labels.")

    label_parser = subparsers.add_parser("label", help="Set the correct level for one crop.")
    label_parser.add_argument("crop")
    label_parser.add_argument("level", type=int)

    predict_parser = subparsers.add_parser("predict", help="Show predictions for crops.")
    predict_parser.add_argument("--confidence", type=float, default=0.52)
    predict_parser.add_argument("--score-margin", type=float, default=0.0)

    tune_parser = subparsers.add_parser("tune", help="Grid-search confidence and score margin.")
    tune_parser.add_argument("--thresholds", default=",".join(str(value) for value in DEFAULT_THRESHOLDS))
    tune_parser.add_argument("--margins", default=",".join(str(value) for value in DEFAULT_MARGINS))
    tune_parser.add_argument("--limit", type=int, default=10)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    crop_dir = Path(args.crop_dir)
    labels_path = Path(args.labels)
    labels = load_labels(labels_path)
    crops = iter_crops(crop_dir)

    if args.command == "list":
        print_crop_list(crops, labels)
        return 0

    if args.command == "label":
        crop = resolve_crop(crop_dir, args.crop)
        set_label(labels, crop, args.level)
        save_labels(labels_path, labels)
        print(f"Labeled {crop.name} as {args.level}")
        return 0

    predictor = make_digit_predictor(args.digit_dir, min_digits=args.min_digits)

    if args.command == "predict":
        if not crops:
            print("No level debug crops found.")
            return 0
        for crop in crops:
            predicted = predictor(crop, args.confidence, args.score_margin)
            expected = labels.get(crop.name, "?")
            value = "unread" if predicted is None else predicted
            print(f"{crop.name}\texpected={expected}\tpredicted={value}")
        return 0

    if args.command == "tune":
        thresholds = parse_float_list(args.thresholds)
        margins = parse_float_list(args.margins)
        results = evaluate_grid(crops, labels, predictor, thresholds, margins)
        print_tune_results(results, args.limit)
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
