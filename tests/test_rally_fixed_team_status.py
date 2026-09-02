import itertools
import unittest
from pathlib import Path

import cv2
import numpy as np

from macro_clicker.detection_core import resize_template_xy
from macro_clicker.rally_matching import (
    RallyMatchingMixin,
    RALLY_FIXED_TEAM_IDLE_CONFIDENCE,
    RALLY_FIXED_TEAM_IDLE_TEMPLATE,
    RALLY_FIXED_TEAM_SCREEN_ANCHOR_CONFIDENCE,
    RALLY_FIXED_TEAM_SCREEN_ANCHOR_REGION,
    RALLY_FIXED_TEAM_SCREEN_ANCHOR_TEMPLATE,
    RALLY_FIXED_TEAM_STATUS_REGIONS,
    RALLY_TEAM_BUSY,
    RALLY_TEAM_IDLE,
    RALLY_TEAM_UNKNOWN,
    detect_fixed_rally_team_status,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _read_project_image(portable_path):
    image = cv2.imread(str(PROJECT_ROOT / portable_path), cv2.IMREAD_COLOR)
    if image is None:
        raise AssertionError(f"could not load test image: {portable_path}")
    return image


class _CaptureHarness(RallyMatchingMixin):
    def __init__(self, frame, rect, anchor_template, idle_template):
        self.frame = frame
        self.rect = rect
        self.anchor_template = anchor_template
        self.idle_template = idle_template
        self.grab_count = 0

    def _get_target_window_rect(self):
        return self.rect

    def _grab(self, region):
        self.grab_count += 1
        if region != self.rect:
            raise AssertionError(f"unexpected capture region: {region}")
        return self.frame.copy(), self.rect[0], self.rect[1]

    def _load_template(self, path):
        if path == RALLY_FIXED_TEAM_SCREEN_ANCHOR_TEMPLATE:
            return self.anchor_template
        if path == RALLY_FIXED_TEAM_IDLE_TEMPLATE:
            return self.idle_template
        raise AssertionError(f"unexpected template path: {path}")


class RallyFixedTeamStatusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.anchor_template = _read_project_image(
            RALLY_FIXED_TEAM_SCREEN_ANCHOR_TEMPLATE
        )
        cls.idle_template = _read_project_image(RALLY_FIXED_TEAM_IDLE_TEMPLATE)

    def _frame(
        self,
        idle_teams=(1, 2, 3),
        *,
        size=(1920, 1080),
        anchor_y_offset=0,
        include_anchor=True,
    ):
        frame_width, frame_height = size
        scale_x = frame_width / 1920
        scale_y = frame_height / 1080
        frame = np.full((frame_height, frame_width, 3), 64, dtype=np.uint8)
        anchor_template = resize_template_xy(
            self.anchor_template,
            scale_x,
            scale_y,
        )
        anchor_left, anchor_top, _width, _height = RALLY_FIXED_TEAM_SCREEN_ANCHOR_REGION
        if include_anchor:
            anchor_y = round((anchor_top + 15 + anchor_y_offset) * scale_y)
            anchor_x = round((anchor_left + 16) * scale_x)
            anchor_height, anchor_width = anchor_template.shape[:2]
            frame[
                anchor_y : anchor_y + anchor_height,
                anchor_x : anchor_x + anchor_width,
            ] = anchor_template

        idle_template = resize_template_xy(
            self.idle_template,
            scale_x,
            scale_y,
        )
        idle_height, idle_width = idle_template.shape[:2]
        for team_number in idle_teams:
            left, top, _width, _height = RALLY_FIXED_TEAM_STATUS_REGIONS[team_number]
            idle_x = round((left + 16) * scale_x)
            idle_y = round((top + 13) * scale_y)
            frame[
                idle_y : idle_y + idle_height,
                idle_x : idle_x + idle_width,
            ] = idle_template
        return frame

    def _detect(self, frame):
        return detect_fixed_rally_team_status(
            frame,
            self.anchor_template,
            self.idle_template,
        )

    def test_reference_configuration_matches_1920x1080_calibration(self):
        self.assertEqual(RALLY_FIXED_TEAM_SCREEN_ANCHOR_REGION, (900, 480, 130, 145))
        self.assertEqual(
            RALLY_FIXED_TEAM_STATUS_REGIONS,
            {
                1: (712, 937, 40, 38),
                2: (837, 937, 40, 38),
                3: (963, 937, 40, 38),
            },
        )
        self.assertEqual(self.idle_template.shape[:2], (12, 9))
        self.assertEqual(RALLY_FIXED_TEAM_SCREEN_ANCHOR_CONFIDENCE, 0.85)
        self.assertEqual(RALLY_FIXED_TEAM_IDLE_CONFIDENCE, 0.90)

    def test_all_eight_idle_busy_combinations(self):
        for bits in itertools.product((False, True), repeat=3):
            idle_teams = {
                team_number
                for team_number, is_idle in zip((1, 2, 3), bits, strict=True)
                if is_idle
            }
            with self.subTest(idle_teams=sorted(idle_teams)):
                result = self._detect(self._frame(idle_teams))
                self.assertTrue(result["screen_valid"])
                self.assertIsNone(result["error"])
                for team_number in (1, 2, 3):
                    expected = (
                        RALLY_TEAM_IDLE
                        if team_number in idle_teams
                        else RALLY_TEAM_BUSY
                    )
                    self.assertEqual(result["states"][team_number], expected)

    def test_wrong_screen_returns_unknown_instead_of_busy(self):
        result = self._detect(np.full((1080, 1920, 3), 64, dtype=np.uint8))

        self.assertFalse(result["screen_valid"])
        self.assertEqual(result["error"], "screen_anchor_not_found")
        self.assertEqual(
            result["states"],
            {1: RALLY_TEAM_UNKNOWN, 2: RALLY_TEAM_UNKNOWN, 3: RALLY_TEAM_UNKNOWN},
        )

    def test_lower_dispatch_panel_anchor_position_is_valid(self):
        result = self._detect(self._frame(anchor_y_offset=85))

        self.assertTrue(result["screen_valid"])
        self.assertEqual(
            result["states"],
            {1: RALLY_TEAM_IDLE, 2: RALLY_TEAM_IDLE, 3: RALLY_TEAM_IDLE},
        )

    def test_status_glyphs_without_dispatch_anchor_stay_unknown(self):
        result = self._detect(self._frame(include_anchor=False))

        self.assertFalse(result["screen_valid"])
        self.assertEqual(result["error"], "screen_anchor_not_found")
        self.assertEqual(
            result["states"],
            {1: RALLY_TEAM_UNKNOWN, 2: RALLY_TEAM_UNKNOWN, 3: RALLY_TEAM_UNKNOWN},
        )

    def test_invalid_capture_returns_unknown(self):
        result = self._detect(np.zeros((10, 10), dtype=np.uint8))

        self.assertFalse(result["screen_valid"])
        self.assertEqual(result["error"], "invalid_frame")
        self.assertTrue(
            all(state == RALLY_TEAM_UNKNOWN for state in result["states"].values())
        )

    def test_missing_template_returns_unknown(self):
        result = detect_fixed_rally_team_status(
            self._frame(),
            self.anchor_template,
            None,
        )

        self.assertFalse(result["screen_valid"])
        self.assertEqual(result["error"], "template_unavailable")
        self.assertTrue(
            all(state == RALLY_TEAM_UNKNOWN for state in result["states"].values())
        )

    def test_resized_window_keeps_fixed_slot_detection(self):
        result = self._detect(self._frame(size=(1600, 900)))

        self.assertTrue(result["screen_valid"])
        self.assertEqual(
            result["states"],
            {1: RALLY_TEAM_IDLE, 2: RALLY_TEAM_IDLE, 3: RALLY_TEAM_IDLE},
        )

    def test_runtime_capture_is_atomic_and_keeps_negative_monitor_origin(self):
        harness = _CaptureHarness(
            self._frame(),
            (-1920, 0, 1920, 1080),
            self.anchor_template,
            self.idle_template,
        )

        result = harness._capture_fixed_rally_team_status()

        self.assertEqual(harness.grab_count, 1)
        self.assertTrue(result["screen_valid"])
        self.assertEqual(result["capture_region"], (-1920, 0, 1920, 1080))
        self.assertEqual(result["capture_origin"], (-1920, 0))
        self.assertEqual(
            result["states"],
            {1: RALLY_TEAM_IDLE, 2: RALLY_TEAM_IDLE, 3: RALLY_TEAM_IDLE},
        )


if __name__ == "__main__":
    unittest.main()
