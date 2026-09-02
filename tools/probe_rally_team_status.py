"""Offline probe for the fixed Team 1/2/3 Add Squad status detector."""

from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np

from macro_clicker.models import project_path
from macro_clicker.rally_matching import (
    RALLY_FIXED_TEAM_IDLE_TEMPLATE,
    RALLY_FIXED_TEAM_SCREEN_ANCHOR_TEMPLATE,
    detect_fixed_rally_team_status,
)


def _load_image(path: Path):
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise FileNotFoundError(f"could not read image: {path}")
    return image


def _score_text(score):
    return "n/a" if score is None else f"{float(score):.3f}"


def _write_status_montage(frame, result, destination: Path):
    panels = []
    for team_number in (1, 2, 3):
        left, top, width, height = result["status_regions"][team_number]
        crop = frame[top : top + height, left : left + width]
        if crop.shape[:2] != (height, width):
            crop = np.zeros((height, width, 3), dtype=np.uint8)
        crop = cv2.resize(crop, None, fx=4, fy=4, interpolation=cv2.INTER_NEAREST)
        panel = cv2.copyMakeBorder(crop, 34, 4, 4, 4, cv2.BORDER_CONSTANT)
        label = (
            f"T{team_number} {result['states'][team_number]} "
            f"{_score_text(result['idle_scores'][team_number])}"
        )
        cv2.putText(
            panel,
            label,
            (5, 22),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.42,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )
        panels.append(panel)
    montage = np.hstack(panels)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(destination), montage):
        raise OSError(f"could not write montage: {destination}")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Read fixed Team 1/2/3 ZZ status from 1920x1080-style screenshots."
    )
    parser.add_argument("screenshots", nargs="+", type=Path)
    parser.add_argument(
        "--crops-dir",
        type=Path,
        help="Optional directory for a three-status-crop montage per screenshot.",
    )
    args = parser.parse_args(argv)

    anchor_template = _load_image(
        Path(project_path(RALLY_FIXED_TEAM_SCREEN_ANCHOR_TEMPLATE))
    )
    idle_template = _load_image(Path(project_path(RALLY_FIXED_TEAM_IDLE_TEMPLATE)))

    exit_code = 0
    for screenshot in args.screenshots:
        try:
            frame = _load_image(screenshot)
            result = detect_fixed_rally_team_status(
                frame,
                anchor_template,
                idle_template,
            )
        except Exception as exc:
            print(f"{screenshot}: ERROR {type(exc).__name__}: {exc}")
            exit_code = 1
            continue

        team_text = ", ".join(
            f"T{team_number}={result['states'][team_number]} "
            f"idle-score={_score_text(result['idle_scores'][team_number])}"
            for team_number in (1, 2, 3)
        )
        screen_text = "VALID" if result["screen_valid"] else "UNKNOWN"
        print(
            f"{screenshot}: screen={screen_text} "
            f"anchor={_score_text(result['anchor_score'])}; {team_text}; "
            f"error={result['error'] or 'none'}"
        )

        if args.crops_dir is not None:
            destination = args.crops_dir / f"{screenshot.stem}-team-status.png"
            _write_status_montage(frame, result, destination)
            print(f"  crops={destination}")

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
