"""Live three-team Rally runtime corrections.

This module keeps the legacy two-team path untouched while applying two fixes to
explicit three-team scenarios (priority containing Team 2):

* use the broader, proven Attack/formation-panel area when validating the
  fixed-slot Team 1/2/3 ZZ states; and
* cap Rally-row OCR by the highest configured enabled-team maximum even though
  the pre-entry status probe is intentionally disabled.

The installer is invoked from :mod:`macro_clicker.__init__` so every normal app
entry point receives the same behavior.
"""

from __future__ import annotations

import numpy as np

from . import rally_matching as _rm
from .rally_team_policy import (
    RALLY_TEAM_BUSY,
    RALLY_TEAM_IDLE,
    RALLY_TEAM_UNKNOWN,
    effective_rally_team_priority,
)

# The old runtime validator used (900, 480, 130, 145), which proved too narrow
# for real Attack/formation panels where SquadAmount.png can appear lower.  This
# region is the 1920x1080 equivalent of the existing broad Attack Confirm
# SquadAmount condition and covers both the upper and lower observed panel
# variants without accepting world-map-only screenshots at the existing 0.85
# confidence threshold.
LIVE_FIXED_TEAM_SCREEN_ANCHOR_REGION = (740, 448, 466, 299)

_INSTALLED = False
_ORIGINAL_CAPTURE = None
_ORIGINAL_AVAILABLE_LEVEL_CAP = None


def _three_team_selector(engine):
    scenario = getattr(engine, "scenario", None)
    if scenario is None:
        return None
    selectors = [
        action
        for step in getattr(scenario, "steps", ())
        for action in getattr(step, "actions", ())
        if getattr(action, "type", None) == "select_rally_team"
    ]
    if len(selectors) != 1:
        return None
    selector = selectors[0]
    try:
        priority = effective_rally_team_priority(selector.team_priority)
    except (TypeError, ValueError):
        return None
    return selector if 2 in priority else None


def _live_unknown_result(error, *, frame_size=None, anchor_score=None):
    width, height = frame_size or _rm.RALLY_FIXED_TEAM_REFERENCE_SIZE
    status_regions = {
        team_number: _rm._scaled_fixed_team_region(region, width, height)
        for team_number, region in _rm.RALLY_FIXED_TEAM_STATUS_REGIONS.items()
    }
    return {
        "screen_valid": False,
        "error": error,
        "reference_size": _rm.RALLY_FIXED_TEAM_REFERENCE_SIZE,
        "frame_size": (width, height),
        "anchor_region": _rm._scaled_fixed_team_region(
            LIVE_FIXED_TEAM_SCREEN_ANCHOR_REGION,
            width,
            height,
        ),
        "anchor_score": anchor_score,
        "status_regions": status_regions,
        "states": {team_number: RALLY_TEAM_UNKNOWN for team_number in status_regions},
        "idle_scores": {team_number: None for team_number in status_regions},
    }


def detect_live_fixed_rally_team_status(
    frame,
    anchor_template,
    idle_template,
    *,
    anchor_confidence=_rm.RALLY_FIXED_TEAM_SCREEN_ANCHOR_CONFIDENCE,
    idle_confidence=_rm.RALLY_FIXED_TEAM_IDLE_CONFIDENCE,
):
    """Read fixed Team 1/2/3 states using the live Attack-panel anchor area."""

    if not isinstance(frame, np.ndarray) or frame.ndim != 3 or frame.shape[2] < 3:
        return _live_unknown_result("invalid_frame")
    frame_height, frame_width = frame.shape[:2]
    if frame_width <= 0 or frame_height <= 0:
        return _live_unknown_result("invalid_frame")
    if (
        not isinstance(anchor_template, np.ndarray)
        or anchor_template.ndim != 3
        or not isinstance(idle_template, np.ndarray)
        or idle_template.ndim != 3
    ):
        return _live_unknown_result(
            "template_unavailable",
            frame_size=(frame_width, frame_height),
        )

    reference_width, reference_height = _rm.RALLY_FIXED_TEAM_REFERENCE_SIZE
    scale_x = frame_width / reference_width
    scale_y = frame_height / reference_height
    anchor_region = _rm._scaled_fixed_team_region(
        LIVE_FIXED_TEAM_SCREEN_ANCHOR_REGION,
        frame_width,
        frame_height,
    )
    anchor_score = _rm._fixed_team_template_score(
        frame,
        anchor_template,
        anchor_region,
        scale_x,
        scale_y,
    )
    if anchor_score is None:
        return _live_unknown_result(
            "anchor_roi_invalid",
            frame_size=(frame_width, frame_height),
        )
    if anchor_score < float(anchor_confidence):
        return _live_unknown_result(
            "screen_anchor_not_found",
            frame_size=(frame_width, frame_height),
            anchor_score=anchor_score,
        )

    status_regions = {
        team_number: _rm._scaled_fixed_team_region(region, frame_width, frame_height)
        for team_number, region in _rm.RALLY_FIXED_TEAM_STATUS_REGIONS.items()
    }
    states = {}
    idle_scores = {}
    for team_number, region in status_regions.items():
        score = _rm._fixed_team_template_score(
            frame,
            idle_template,
            region,
            scale_x,
            scale_y,
        )
        idle_scores[team_number] = score
        if score is None:
            states[team_number] = RALLY_TEAM_UNKNOWN
        elif score >= float(idle_confidence):
            states[team_number] = RALLY_TEAM_IDLE
        else:
            states[team_number] = RALLY_TEAM_BUSY

    return {
        "screen_valid": True,
        "error": (
            "status_roi_invalid"
            if any(state == RALLY_TEAM_UNKNOWN for state in states.values())
            else None
        ),
        "reference_size": _rm.RALLY_FIXED_TEAM_REFERENCE_SIZE,
        "frame_size": (frame_width, frame_height),
        "anchor_region": anchor_region,
        "anchor_score": anchor_score,
        "status_regions": status_regions,
        "states": states,
        "idle_scores": idle_scores,
    }


def _capture_live_fixed_rally_team_status(engine):
    window_rect = engine._get_target_window_rect()
    if not window_rect:
        return _live_unknown_result("target_window_unavailable")
    try:
        frame, off_x, off_y = engine._grab(window_rect)
        if (int(off_x), int(off_y)) != (int(window_rect[0]), int(window_rect[1])):
            return _live_unknown_result(
                "capture_origin_mismatch",
                frame_size=(int(frame.shape[1]), int(frame.shape[0])),
            )
        anchor_template = engine._load_template(
            _rm.RALLY_FIXED_TEAM_SCREEN_ANCHOR_TEMPLATE
        )
        idle_template = engine._load_template(_rm.RALLY_FIXED_TEAM_IDLE_TEMPLATE)
        result = detect_live_fixed_rally_team_status(
            frame,
            anchor_template,
            idle_template,
        )
    except Exception as exc:
        return _live_unknown_result(
            f"capture_or_template_error:{type(exc).__name__}"
        )
    result["capture_region"] = tuple(int(value) for value in window_rect)
    result["capture_origin"] = (int(off_x), int(off_y))
    if not result.get("screen_valid"):
        score = result.get("anchor_score")
        score_text = "n/a" if score is None else f"{float(score):.3f}"
        engine.log(
            "  [team3] fixed status screen invalid: "
            f"error={result.get('error')} anchor_score={score_text}"
        )
    return result


def _configured_three_team_level_ceiling(engine):
    selector = _three_team_selector(engine)
    if selector is None:
        return _rm._TEAM_LEVEL_CAP_UNSET
    priority = effective_rally_team_priority(selector.team_priority)
    limits = [getattr(selector, f"team{team_number}_max_level") for team_number in priority]
    if any(limit is None for limit in limits):
        return _rm._TEAM_LEVEL_CAP_UNBOUNDED
    if not limits:
        return _rm._TEAM_LEVEL_CAP_UNSET
    return max(int(limit) for limit in limits)


def install_rally_three_team_runtime():
    """Install the live three-team fixes once for all normal entry points."""

    global _INSTALLED, _ORIGINAL_CAPTURE, _ORIGINAL_AVAILABLE_LEVEL_CAP
    if _INSTALLED:
        return

    _ORIGINAL_CAPTURE = _rm.RallyMatchingMixin._capture_fixed_rally_team_status
    _ORIGINAL_AVAILABLE_LEVEL_CAP = _rm.RallyMatchingMixin._available_rally_team_level_cap

    def capture_fixed_rally_team_status(self):
        if _three_team_selector(self) is None:
            return _ORIGINAL_CAPTURE(self)
        return _capture_live_fixed_rally_team_status(self)

    def available_rally_team_level_cap(self, action):
        result = _ORIGINAL_AVAILABLE_LEVEL_CAP(self, action)
        if result is not _rm._TEAM_LEVEL_CAP_UNSET:
            return result
        if getattr(action, "type", None) != "click_matching_row":
            return result
        return _configured_three_team_level_ceiling(self)

    _rm.RallyMatchingMixin._capture_fixed_rally_team_status = (
        capture_fixed_rally_team_status
    )
    _rm.RallyMatchingMixin._available_rally_team_level_cap = (
        available_rally_team_level_cap
    )
    _INSTALLED = True
