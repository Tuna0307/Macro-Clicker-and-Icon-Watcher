"""Second-stage safeguards for the explicit three-team Rally hot path.

This overlay is installed after ``rally_hot_path_runtime``.  It addresses two
live-only states that were not represented by the original scenario steps:

* once the world-map Rally icon has been clicked, the same entry step must not
  fire again while the Rally/formation workflow is still in progress; and
* when all three squads are already out, clicking a Rally row ``+`` can expose
  only the fixed bottom squad tray (no central SquadAmount/Attack panel).  That
  tray must never dispatch.  If all three fixed slots are proven BUSY, one
  neutral tray-background click dismisses it and the hot loop resumes.

The legacy two-team path is untouched.
"""

from __future__ import annotations

import time

from . import rally_hot_path_runtime as _hot
from . import rally_matching as _rm
from .rally_team_policy import RALLY_TEAM_BUSY, RALLY_TEAM_IDLE, RALLY_TEAM_UNKNOWN
from .rally_three_team_runtime import LIVE_FIXED_TEAM_SCREEN_ANCHOR_REGION

FULL_SQUAD_BROAD_REGION = (80, 140, 300, 260)
FULL_SQUAD_BROAD_CONFIDENCE = 0.90

TRAY_ANCHOR_TEMPLATE = "templates/AddSquad.png"
TRAY_ANCHOR_REGION = (650, 880, 630, 200)
TRAY_ANCHOR_CONFIDENCE = 0.95
FORMATION_ANCHOR_TEMPLATE = _rm.RALLY_FIXED_TEAM_SCREEN_ANCHOR_TEMPLATE
FORMATION_ANCHOR_CONFIDENCE = _rm.RALLY_FIXED_TEAM_SCREEN_ANCHOR_CONFIDENCE
TRAY_DISMISS_REFERENCE_POINT = (1218, 1045)
TRAY_RECOVERY_BASE_DEFER_SECONDS = 0.05

_INSTALLED = False
_ORIGINAL_START = None
_ORIGINAL_EVALUATE_STEP = None
_ORIGINAL_RUN_ACTION = None
_ORIGINAL_CYCLE = None


def _scale_reference_region(window_rect, region):
    left, top, width, height = window_rect
    ref_width, ref_height = _rm.RALLY_FIXED_TEAM_REFERENCE_SIZE
    x, y, w, h = region
    scale_x = width / ref_width
    scale_y = height / ref_height
    return (
        int(round(left + x * scale_x)),
        int(round(top + y * scale_y)),
        max(1, int(round(w * scale_x))),
        max(1, int(round(h * scale_y))),
    )


def _broad_three_of_three(engine):
    """Search a bounded left-side band for 3/3 instead of one exact tiny ROI."""

    window_rect = engine._get_target_window_rect()
    if not window_rect:
        return False
    try:
        region = _scale_reference_region(window_rect, FULL_SQUAD_BROAD_REGION)
        frame, _off_x, _off_y = engine._grab(region)
        template = engine._load_template(_hot.FULL_SQUAD_TEMPLATE)
        matches = engine._find_template_matches_in_frame(
            frame,
            template,
            FULL_SQUAD_BROAD_CONFIDENCE,
            collect_all=False,
            allow_coarse=False,
            use_grayscale=True,
            reference_size=_rm.RALLY_FIXED_TEAM_REFERENCE_SIZE,
            current_size=(window_rect[2], window_rect[3]),
            early_exit_score=FULL_SQUAD_BROAD_CONFIDENCE,
        )
        return bool(matches)
    except Exception:
        # This is an entry optimization only.  Uncertainty must not authorize a
        # dispatch; final fixed-slot/team-tray checks remain fail-closed.
        return False


def _score_scaled_template(frame, template, region):
    frame_height, frame_width = frame.shape[:2]
    ref_width, ref_height = _rm.RALLY_FIXED_TEAM_REFERENCE_SIZE
    scale_x = frame_width / ref_width
    scale_y = frame_height / ref_height
    scaled_region = _rm._scaled_fixed_team_region(
        region,
        frame_width,
        frame_height,
    )
    return _rm._fixed_team_template_score(
        frame,
        template,
        scaled_region,
        scale_x,
        scale_y,
    )


def detect_full_squad_tray(
    frame,
    tray_anchor_template,
    formation_anchor_template,
    idle_template,
    *,
    tray_anchor_confidence=TRAY_ANCHOR_CONFIDENCE,
    formation_anchor_confidence=FORMATION_ANCHOR_CONFIDENCE,
    idle_confidence=_rm.RALLY_FIXED_TEAM_IDLE_CONFIDENCE,
):
    """Classify the fixed bottom tray without mistaking a real formation panel.

    ``AddSquad.png`` positively proves the fixed bottom squad-card bar is
    present.  ``SquadAmount.png`` positively proves the normal formation screen;
    in that case this helper deliberately does nothing and leaves normal Attack
    Confirm handling in charge.  Only a tray with no formation anchor gets its
    three fixed ZZ slots interpreted.
    """

    if getattr(frame, "ndim", 0) != 3 or frame.shape[2] < 3:
        return {
            "tray_valid": False,
            "formation_visible": False,
            "states": {team: RALLY_TEAM_UNKNOWN for team in (1, 2, 3)},
            "error": "invalid_frame",
        }

    tray_score = _score_scaled_template(frame, tray_anchor_template, TRAY_ANCHOR_REGION)
    if tray_score is None or tray_score < float(tray_anchor_confidence):
        return {
            "tray_valid": False,
            "formation_visible": False,
            "tray_score": tray_score,
            "states": {team: RALLY_TEAM_UNKNOWN for team in (1, 2, 3)},
            "error": "tray_anchor_not_found",
        }

    formation_score = _score_scaled_template(
        frame,
        formation_anchor_template,
        LIVE_FIXED_TEAM_SCREEN_ANCHOR_REGION,
    )
    if (
        formation_score is not None
        and formation_score >= float(formation_anchor_confidence)
    ):
        return {
            "tray_valid": True,
            "formation_visible": True,
            "tray_score": tray_score,
            "formation_score": formation_score,
            "states": {team: RALLY_TEAM_UNKNOWN for team in (1, 2, 3)},
            "error": None,
        }

    frame_height, frame_width = frame.shape[:2]
    ref_width, ref_height = _rm.RALLY_FIXED_TEAM_REFERENCE_SIZE
    scale_x = frame_width / ref_width
    scale_y = frame_height / ref_height
    states = {}
    idle_scores = {}
    for team_number, reference_region in _rm.RALLY_FIXED_TEAM_STATUS_REGIONS.items():
        region = _rm._scaled_fixed_team_region(
            reference_region,
            frame_width,
            frame_height,
        )
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
        "tray_valid": True,
        "formation_visible": False,
        "tray_score": tray_score,
        "formation_score": formation_score,
        "states": states,
        "idle_scores": idle_scores,
        "error": (
            "status_roi_invalid"
            if any(state == RALLY_TEAM_UNKNOWN for state in states.values())
            else None
        ),
    }


def _capture_full_squad_tray(engine):
    """Probe the observed tray with bounded captures, not a whole-window scan."""

    window_rect = engine._get_target_window_rect()
    if not window_rect:
        return None
    try:
        ref_width, ref_height = _rm.RALLY_FIXED_TEAM_REFERENCE_SIZE
        scale_x = window_rect[2] / ref_width
        scale_y = window_rect[3] / ref_height

        tray_capture = _scale_reference_region(window_rect, TRAY_ANCHOR_REGION)
        tray_frame, tray_off_x, tray_off_y = engine._grab(tray_capture)
        tray_template = engine._load_template(TRAY_ANCHOR_TEMPLATE)
        tray_score = _rm._fixed_team_template_score(
            tray_frame,
            tray_template,
            (0, 0, int(tray_frame.shape[1]), int(tray_frame.shape[0])),
            scale_x,
            scale_y,
        )
        if tray_score is None or tray_score < TRAY_ANCHOR_CONFIDENCE:
            return None

        formation_capture = _scale_reference_region(
            window_rect,
            LIVE_FIXED_TEAM_SCREEN_ANCHOR_REGION,
        )
        formation_frame, _formation_off_x, _formation_off_y = engine._grab(
            formation_capture
        )
        formation_template = engine._load_template(FORMATION_ANCHOR_TEMPLATE)
        formation_score = _rm._fixed_team_template_score(
            formation_frame,
            formation_template,
            (
                0,
                0,
                int(formation_frame.shape[1]),
                int(formation_frame.shape[0]),
            ),
            scale_x,
            scale_y,
        )
        if (
            formation_score is not None
            and formation_score >= FORMATION_ANCHOR_CONFIDENCE
        ):
            return {
                "tray_valid": True,
                "formation_visible": True,
                "tray_score": tray_score,
                "formation_score": formation_score,
                "window_rect": tuple(int(value) for value in window_rect),
            }

        idle_template = engine._load_template(_rm.RALLY_FIXED_TEAM_IDLE_TEMPLATE)
        tray_local_left = int(tray_capture[0] - window_rect[0])
        tray_local_top = int(tray_capture[1] - window_rect[1])
        states = {}
        idle_scores = {}
        for team_number, reference_region in _rm.RALLY_FIXED_TEAM_STATUS_REGIONS.items():
            status_region = _rm._scaled_fixed_team_region(
                reference_region,
                int(window_rect[2]),
                int(window_rect[3]),
            )
            local_region = (
                int(status_region[0] - tray_local_left),
                int(status_region[1] - tray_local_top),
                int(status_region[2]),
                int(status_region[3]),
            )
            score = _rm._fixed_team_template_score(
                tray_frame,
                idle_template,
                local_region,
                scale_x,
                scale_y,
            )
            idle_scores[team_number] = score
            if score is None:
                states[team_number] = RALLY_TEAM_UNKNOWN
            elif score >= _rm.RALLY_FIXED_TEAM_IDLE_CONFIDENCE:
                states[team_number] = RALLY_TEAM_IDLE
            else:
                states[team_number] = RALLY_TEAM_BUSY

        return {
            "tray_valid": True,
            "formation_visible": False,
            "tray_score": tray_score,
            "formation_score": formation_score,
            "states": states,
            "idle_scores": idle_scores,
            "window_rect": tuple(int(value) for value in window_rect),
            "capture_origins": (
                (int(tray_off_x), int(tray_off_y)),
                (int(_formation_off_x), int(_formation_off_y)),
            ),
        }
    except Exception:
        return None


def _dismiss_point(window_rect):
    ref_width, ref_height = _rm.RALLY_FIXED_TEAM_REFERENCE_SIZE
    left, top, width, height = window_rect
    x = left + TRAY_DISMISS_REFERENCE_POINT[0] * width / ref_width
    y = top + TRAY_DISMISS_REFERENCE_POINT[1] * height / ref_height
    return int(round(x)), int(round(y))


def _clear_workflow_after_tray(engine):
    engine._pending_rally_level = None
    engine._pending_rally_team_selected = None
    engine._rally_join_guard_until = 0.0
    reset = getattr(engine, "_reset_three_team_rally_state", None)
    if reset is not None:
        reset("all squads busy tray recovery")
    for step_name in (
        "Joining",
        "Attack Confirm",
        "Back if wrong mob",
        "Back if no slot",
    ):
        _hot._set_step_enabled(engine, step_name, False)


def _recover_all_busy_tray(engine):
    if not getattr(engine, "_rally_hot_entry_latched", False):
        return False
    if getattr(engine, "_pending_rally_level", None) is None:
        return False
    if getattr(engine, "_pending_rally_team_selected", None) is not None:
        return False

    result = _capture_full_squad_tray(engine)
    if not result or not result.get("tray_valid"):
        return False
    if result.get("formation_visible"):
        return False

    states = result.get("states", {})
    if any(states.get(team) != RALLY_TEAM_BUSY for team in (1, 2, 3)):
        # Fail closed.  A tray with any IDLE/UNKNOWN evidence is not the
        # specifically observed "all squads are already out" state.
        return False

    point = _dismiss_point(result["window_rect"])
    if engine._click_point(point[0], point[1], "left") is False:
        return False

    engine.log(
        "  [team3] fixed squad tray shows T1=BUSY T2=BUSY T3=BUSY; "
        f"dismissed at {point} without dispatch"
    )
    _clear_workflow_after_tray(engine)
    engine._rally_hot_entry_latched = False
    engine._rally_hot_profile_armed = False

    # The user-confirmed tray is dismissed with a map-adjacent click.  Keep the
    # whole-screen Base recovery available immediately afterward in case that
    # neutral dismissal happens to land on a player base.
    engine._rally_hot_base_armed = True
    engine._rally_hot_base_not_before = (
        time.monotonic() + TRAY_RECOVERY_BASE_DEFER_SECONDS
    )
    return True


def _clear_entry_latch(engine):
    engine._rally_hot_entry_latched = False


def install_rally_hot_path_v7_runtime():
    """Install the full-squad recovery/entry-latch overlay once."""

    global _INSTALLED
    global _ORIGINAL_START
    global _ORIGINAL_EVALUATE_STEP
    global _ORIGINAL_RUN_ACTION
    global _ORIGINAL_CYCLE
    if _INSTALLED:
        return

    from .engine import MacroEngine

    _ORIGINAL_START = MacroEngine.start
    _ORIGINAL_EVALUATE_STEP = MacroEngine._evaluate_step
    _ORIGINAL_RUN_ACTION = MacroEngine._run_action
    _ORIGINAL_CYCLE = MacroEngine._cycle

    def start(self):
        self._rally_hot_entry_latched = False
        self._rally_hot_last_full_squad_log = 0.0
        result = _ORIGINAL_START(self)
        if _hot._is_three_team(self):
            self.log("[build] JOIN-HOT-RACE-v7 full-squad recovery loaded")
        return result

    def evaluate_step(self, step, frame_cache=None):
        if not _hot._is_three_team(self):
            return _ORIGINAL_EVALUATE_STEP(self, step, frame_cache=frame_cache)

        name = getattr(step, "name", None)
        if (
            name == "Enter Rally after team probe"
            and getattr(self, "_rally_hot_entry_latched", False)
        ):
            return False, {}, {}

        result = _ORIGINAL_EVALUATE_STEP(self, step, frame_cache=frame_cache)
        if name == "Enter Rally after team probe" and result[0]:
            if _broad_three_of_three(self):
                now = time.monotonic()
                last = float(getattr(self, "_rally_hot_last_full_squad_log", 0.0))
                if now - last >= 1.0:
                    self.log(
                        "  [rally-fast] broad 3/3 gate: all squads out; "
                        "Rally entry suppressed"
                    )
                    self._rally_hot_last_full_squad_log = now
                return False, {}, {}
        return result

    def run_action(self, step, action, points, matches):
        if not _hot._is_three_team(self):
            return _ORIGINAL_RUN_ACTION(self, step, action, points, matches)

        name = getattr(step, "name", None)
        action_type = getattr(action, "type", None)

        result = _ORIGINAL_RUN_ACTION(self, step, action, points, matches)

        if name == "Enter Rally after team probe" and action_type == "click":
            if result:
                self._rally_hot_entry_latched = True
                self.log("  [rally-fast] Rally entry latched until workflow exit")
            return result

        if name in {"Back if wrong mob", "Back if no slot"} and action_type == "click":
            if result:
                _clear_entry_latch(self)
            return result

        if name == "MisClick Base" and action_type == "click":
            if result:
                _clear_entry_latch(self)
            return result

        if name == "Attack Confirm" and action_type == "select_rally_team":
            if getattr(self, "_abort_current_step", False):
                _clear_entry_latch(self)
            return result

        if name == "Attack Confirm" and action_type == "click":
            if result:
                _clear_entry_latch(self)
            return result

        return result

    def cycle(self):
        if _hot._is_three_team(self) and _recover_all_busy_tray(self):
            # Recovery itself is useful work for this cycle.  Returning now
            # avoids immediately re-entering a Rally before the dismissal has
            # visually settled; the normal 30-50 ms hot poll resumes next.
            return True
        return _ORIGINAL_CYCLE(self)

    MacroEngine.start = start
    MacroEngine._evaluate_step = evaluate_step
    MacroEngine._run_action = run_action
    MacroEngine._cycle = cycle
    _INSTALLED = True
