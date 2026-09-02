"""Aggressive three-team Rally hot-path scheduling.

This module is intentionally additive.  It leaves the legacy two-team Rally
scenario untouched and only activates when the loaded scenario has an explicit
three-team selector (priority containing Team 2).

The hot path does four things:

* poll three-team Rally work at 50 ms without persisting a scenario rewrite;
* gate world-map Rally entry when the small 3/3 counter is positively proven;
* keep MisClick Base full-screen, but evaluate it only after a risky world-map
  click instead of on every normal polling cycle; and
* micro-revalidate the exact Join/+ immediately before input, with an
  event-driven MisClick Profile fallback if the last-slot race is still lost.
"""

from __future__ import annotations

import math
import os
import time

from .rally_team_policy import effective_rally_team_priority

FAST_THREE_TEAM_POLL_INTERVAL = 0.05
MISCLICK_BASE_DEFER_SECONDS = 0.12
FULL_SQUAD_TEMPLATE = "templates/FullSquad3_3.png"
FULL_SQUAD_REFERENCE_SIZE = (1920, 1080)
FULL_SQUAD_REFERENCE_REGION = (154, 205, 51, 28)
FULL_SQUAD_CONFIDENCE = 0.90
JOIN_TEMPLATE_BASENAME = "join.png"
JOIN_REVALIDATION_MIN_HALF_SIZE = 24
JOIN_REVALIDATION_TEMPLATE_PADDING = 1.75

_INSTALLED = False
_ORIGINAL_START = None
_ORIGINAL_EVALUATE_STEP = None
_ORIGINAL_RUN_ACTION = None
_ORIGINAL_CLICK_POINT = None


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


def _is_three_team(engine):
    cached = getattr(engine, "_rally_hot_path_three_team", None)
    if cached is None:
        cached = _three_team_selector(engine) is not None
        engine._rally_hot_path_three_team = bool(cached)
    return bool(cached)


def _set_step_enabled(engine, name, enabled):
    scenario = getattr(engine, "scenario", None)
    if scenario is None:
        return
    for step in getattr(scenario, "steps", ()):
        if getattr(step, "name", None) == name:
            step.enabled = bool(enabled)
            return


def _scale_reference_region(window_rect, region):
    left, top, width, height = window_rect
    ref_width, ref_height = FULL_SQUAD_REFERENCE_SIZE
    x, y, w, h = region
    scale_x = width / ref_width
    scale_y = height / ref_height
    return (
        int(round(left + x * scale_x)),
        int(round(top + y * scale_y)),
        max(1, int(round(w * scale_x))),
        max(1, int(round(h * scale_y))),
    )


def _all_three_squads_out(engine):
    """Cheap fail-open 3/3 gate; final fixed-slot ZZ remains authoritative."""

    window_rect = engine._get_target_window_rect()
    if not window_rect:
        return False
    try:
        region = _scale_reference_region(window_rect, FULL_SQUAD_REFERENCE_REGION)
        frame, _off_x, _off_y = engine._grab(region)
        template = engine._load_template(FULL_SQUAD_TEMPLATE)
        matches = engine._find_template_matches_in_frame(
            frame,
            template,
            FULL_SQUAD_CONFIDENCE,
            collect_all=False,
            allow_coarse=False,
            reference_size=FULL_SQUAD_REFERENCE_SIZE,
            current_size=(window_rect[2], window_rect[3]),
            early_exit_score=FULL_SQUAD_CONFIDENCE,
        )
        return bool(matches)
    except Exception:
        # This count is an optimization only.  Never block a Rally opportunity
        # because the fast gate itself was unavailable; final ZZ status fails
        # closed before dispatch.
        return False


def _join_condition(step, action):
    if getattr(step, "name", None) != "Joining":
        return None
    if getattr(action, "type", None) != "click_matching_row":
        return None
    index = getattr(action, "on_condition_index", None)
    conditions = getattr(step, "conditions", ())
    if not isinstance(index, int) or not 0 <= index < len(conditions):
        return None
    condition = conditions[index]
    basename = os.path.basename(getattr(condition, "template_path", "") or "")
    return condition if basename.casefold() == JOIN_TEMPLATE_BASENAME else None


def _inside(x, y, region):
    if not isinstance(region, (tuple, list)) or len(region) != 4:
        return False
    left, top, width, height = region
    return left <= x < left + width and top <= y < top + height


def _local_join_region(engine, condition, template, x, y, window_rect):
    reference = (
        getattr(condition, "template_reference_size", None)
        or getattr(condition, "region_window_size", None)
    )
    scale_x = scale_y = 1.0
    if reference and reference[0] > 0 and reference[1] > 0:
        scale_x = window_rect[2] / reference[0]
        scale_y = window_rect[3] / reference[1]
    template_height, template_width = template.shape[:2]
    half_width = max(
        JOIN_REVALIDATION_MIN_HALF_SIZE,
        int(math.ceil(template_width * scale_x * JOIN_REVALIDATION_TEMPLATE_PADDING)),
    )
    half_height = max(
        JOIN_REVALIDATION_MIN_HALF_SIZE,
        int(math.ceil(template_height * scale_y * JOIN_REVALIDATION_TEMPLATE_PADDING)),
    )
    left = max(window_rect[0], int(round(x - half_width)))
    top = max(window_rect[1], int(round(y - half_height)))
    right = min(window_rect[0] + window_rect[2], int(round(x + half_width + 1)))
    bottom = min(window_rect[1] + window_rect[3], int(round(y + half_height + 1)))
    if right <= left or bottom <= top:
        return None
    return left, top, right - left, bottom - top


def revalidate_join_target(engine, step, action, x, y):
    """Freshly prove the exact Join/+ is still present at the input boundary."""

    condition = _join_condition(step, action)
    if condition is None:
        return {"applies": False, "valid": True, "center": (x, y)}
    try:
        condition_region = engine._resolve_capture_region(condition)
    except Exception as exc:
        return {
            "applies": True,
            "valid": False,
            "reason": f"region_error:{type(exc).__name__}",
        }
    if not _inside(x, y, condition_region):
        # The click_matching_row no-match path can click BackButton.  Do not
        # intercept that fallback just because the action context is active.
        return {"applies": False, "valid": True, "center": (x, y)}

    started = time.perf_counter()
    try:
        window_rect = engine._get_target_window_rect()
        if not window_rect:
            return {"applies": True, "valid": False, "reason": "window_unavailable"}
        template = engine._load_template(condition.template_path)
        region = _local_join_region(engine, condition, template, x, y, window_rect)
        if region is None:
            return {"applies": True, "valid": False, "reason": "roi_invalid"}
        frame, off_x, off_y = engine._grab(region)
        matches = engine._find_template_matches_in_frame(
            frame,
            template,
            condition.confidence,
            collect_all=True,
            **engine._condition_matching_kwargs(condition),
        )
        if not matches:
            return {
                "applies": True,
                "valid": False,
                "reason": "join_disappeared",
                "elapsed": time.perf_counter() - started,
            }
        candidates = []
        for match in matches:
            mx, my, width, height, score, _scale = match[:6]
            center = (int(off_x + mx + width // 2), int(off_y + my + height // 2))
            distance = (center[0] - x) ** 2 + (center[1] - y) ** 2
            candidates.append((distance, -float(score), center, float(score)))
        _distance, _negative_score, center, score = min(candidates)
        return {
            "applies": True,
            "valid": True,
            "center": center,
            "score": score,
            "elapsed": time.perf_counter() - started,
        }
    except Exception as exc:
        return {
            "applies": True,
            "valid": False,
            "reason": f"revalidation_error:{type(exc).__name__}",
            "elapsed": time.perf_counter() - started,
        }


def install_rally_hot_path_runtime():
    """Install the explicit-three-team hot path once."""

    global _INSTALLED
    global _ORIGINAL_START
    global _ORIGINAL_EVALUATE_STEP
    global _ORIGINAL_RUN_ACTION
    global _ORIGINAL_CLICK_POINT
    if _INSTALLED:
        return

    from .engine import MacroEngine

    _ORIGINAL_START = MacroEngine.start
    _ORIGINAL_EVALUATE_STEP = MacroEngine._evaluate_step
    _ORIGINAL_RUN_ACTION = MacroEngine._run_action
    _ORIGINAL_CLICK_POINT = MacroEngine._click_point

    def start(self):
        self._rally_hot_path_three_team = _three_team_selector(self) is not None
        if self._rally_hot_path_three_team:
            self.scenario.poll_interval = FAST_THREE_TEAM_POLL_INTERVAL
            self._rally_hot_base_armed = False
            self._rally_hot_profile_armed = False
            self._rally_hot_base_not_before = 0.0
            self.RALLY_DIAGNOSTIC_BUILD = "JOIN-HOT-RACE-v6"
        return _ORIGINAL_START(self)

    def evaluate_step(self, step, frame_cache=None):
        if not _is_three_team(self):
            return _ORIGINAL_EVALUATE_STEP(self, step, frame_cache=frame_cache)

        name = getattr(step, "name", None)
        now = time.monotonic()
        if name == "MisClick Base":
            if not getattr(self, "_rally_hot_base_armed", False):
                return False, {}, {}
            if now < float(getattr(self, "_rally_hot_base_not_before", 0.0)):
                return False, {}, {}
        elif name == "MisClick Profile" and not getattr(
            self, "_rally_hot_profile_armed", False
        ):
            return False, {}, {}

        result = _ORIGINAL_EVALUATE_STEP(self, step, frame_cache=frame_cache)
        if name == "Enter Rally after team probe" and result[0]:
            if _all_three_squads_out(self):
                return False, {}, {}
        return result

    def run_action(self, step, action, points, matches):
        if not _is_three_team(self):
            return _ORIGINAL_RUN_ACTION(self, step, action, points, matches)

        name = getattr(step, "name", None)
        action_type = getattr(action, "type", None)

        # Remove the old fixed 0.5s world-map -> Rally sleep.  Normal 50 ms
        # polling detects the destination as soon as it exists.
        if name == "Enter Rally after team probe" and action_type == "wait":
            return False

        if name == "Enter Rally after team probe" and action_type == "click":
            self._rally_hot_base_armed = True
            self._rally_hot_profile_armed = False
            self._rally_hot_base_not_before = (
                time.monotonic() + MISCLICK_BASE_DEFER_SECONDS
            )
            result = _ORIGINAL_RUN_ACTION(self, step, action, points, matches)
            if not result:
                self._rally_hot_base_armed = False
            return result

        if name == "Joining" and action_type == "click_matching_row":
            self._rally_hot_base_armed = False
            self._rally_hot_profile_armed = True
            previous = getattr(self, "_rally_hot_join_context", None)
            self._rally_hot_join_context = (step, action)
            try:
                result = _ORIGINAL_RUN_ACTION(self, step, action, points, matches)
            finally:
                self._rally_hot_join_context = previous
            if not result or getattr(self, "_pending_rally_level", None) is None:
                self._rally_hot_profile_armed = False
            return result

        if name == "Attack Confirm" and action_type == "select_rally_team":
            self._rally_hot_base_armed = False
            self._rally_hot_profile_armed = False

        if name in {"Back if wrong mob", "Back if no slot"} and action_type == "click":
            self._rally_hot_base_armed = False
            self._rally_hot_profile_armed = False

        result = _ORIGINAL_RUN_ACTION(self, step, action, points, matches)

        if name == "MisClick Profile" and action_type == "click" and result:
            self._rally_hot_profile_armed = False
            self._rally_join_guard_until = 0.0
            self._pending_rally_level = None
            self._pending_rally_team_selected = None
            self.log("  [rally-fast] profile misclick recovered; rescan immediately")
        elif name == "MisClick Base" and action_type == "click" and result:
            self._rally_hot_base_armed = False
            self._rally_hot_profile_armed = False
            for step_name in (
                "Joining",
                "Attack Confirm",
                "Back if wrong mob",
                "Back if no slot",
            ):
                _set_step_enabled(self, step_name, False)
        return result

    def click_point(self, x, y, button):
        context = getattr(self, "_rally_hot_join_context", None)
        if context is not None and _is_three_team(self):
            step, action = context
            result = revalidate_join_target(self, step, action, x, y)
            if result.get("applies"):
                if not result.get("valid"):
                    self._rally_hot_profile_armed = False
                    elapsed = result.get("elapsed")
                    elapsed_text = (
                        "" if elapsed is None else f" in {float(elapsed):.3f}s"
                    )
                    self.log(
                        "  [rally-fast] last-slot + vanished before input"
                        f"{elapsed_text}; stale click cancelled"
                    )
                    return False
                x, y = result["center"]
                score = result.get("score")
                elapsed = result.get("elapsed")
                score_text = "n/a" if score is None else f"{float(score):.3f}"
                elapsed_text = "n/a" if elapsed is None else f"{float(elapsed):.3f}s"
                self.log(
                    "  [rally-fast] revalidated last-slot + "
                    f"score={score_text} {elapsed_text}"
                )
        return _ORIGINAL_CLICK_POINT(self, x, y, button)

    MacroEngine.start = start
    MacroEngine._evaluate_step = evaluate_step
    MacroEngine._run_action = run_action
    MacroEngine._click_point = click_point
    _INSTALLED = True
