"""Rally-row matching, level OCR, and evidence capture services for the macro engine."""

from __future__ import annotations

import math
import os
import threading
import time
from dataclasses import dataclass

import cv2
import numpy as np

from .level_ocr import LevelOcrReader
from .models import Action, ImageCondition, has_smart_rally_team_prefilter

_REFERENCE_UNSET = object()
_LEVEL_ELIGIBLE = "eligible"
_LEVEL_INELIGIBLE = "ineligible"
_LEVEL_UNREADABLE = "unreadable"
_MATCHING_ROW_SNAPSHOT_KEY = object()
_TEAM_LEVEL_CAP_UNSET = object()
_TEAM_LEVEL_CAP_UNBOUNDED = "unbounded"


@dataclass(frozen=True)
class _CaptureSnapshot:
    """One screen capture shared by row anchors and all OCR crops."""

    frame: np.ndarray
    left: int
    top: int

    @property
    def right(self):
        return self.left + int(self.frame.shape[1])

    @property
    def bottom(self):
        return self.top + int(self.frame.shape[0])

    def crop(self, rect):
        if rect is None:
            return self.frame
        left, top, width, height = (int(value) for value in rect)
        right = left + width
        bottom = top + height
        if (
            left < self.left
            or top < self.top
            or right > self.right
            or bottom > self.bottom
        ):
            return None
        local_left = left - self.left
        local_top = top - self.top
        crop = self.frame[
            local_top : local_top + height,
            local_left : local_left + width,
        ]
        if crop.shape[:2] != (height, width):
            return None
        return crop


class RallyMatchingMixin:
    """Private rally matching implementation mixed into ``MacroEngine``."""

    _level_ocr_reader: LevelOcrReader | None
    _last_rally_team_busy_state: tuple[bool, bool, int | str | None] | None
    _last_rally_team_availability: dict

    def _prepare_rally_team_availability_for_entry(self, step):
        """Capture squad availability before an entry click hides the queue."""
        enabled_step_names = {
            action.step_name
            for action in step.actions
            if action.type == "set_step" and action.set_enabled
        }
        if not enabled_step_names:
            return True

        row_action = next(
            (
                action
                for candidate_step in self.scenario.steps
                if candidate_step.name in enabled_step_names
                for action in candidate_step.actions
                if action.type == "click_matching_row"
            ),
            None,
        )
        if row_action is None:
            return True

        self._pending_rally_team_availability = None
        level_cap = self._available_rally_team_level_cap(row_action)
        if level_cap is _TEAM_LEVEL_CAP_UNSET:
            return True
        self._pending_rally_team_availability = self._last_rally_team_availability
        if level_cap is None:
            self.log("  [team] skip rally entry: Team 1 and Team 3 are both busy")
            return False
        self.log(f"  [team] saved pre-entry level cap {level_cap}")
        return True

    def _find_matching_row_targets(self, action: Action, matches: dict):
        selections, _had_unreadable = self._find_matching_row_selections(
            action,
            matches,
            apply_level_filter=True,
        )
        return [selection["target"] for selection in selections]

    def _find_matching_row_selections(
        self,
        action: Action,
        matches: dict,
        *,
        apply_level_filter: bool,
    ):
        # A matching pass is one diagnostic generation.  Row centers are not
        # stable identifiers across captures, so records from an earlier pass
        # must not be reused merely because a later row has the same center.
        self._begin_level_diagnostic_generation()
        reference_index = action.match_condition_index
        target_index = action.on_condition_index
        if reference_index is None or target_index is None:
            return [], False

        reference_matches = matches.get(reference_index, [])
        remaining_targets = list(matches.get(target_index, []))
        team_level_cap = _TEAM_LEVEL_CAP_UNSET
        if apply_level_filter:
            team_level_cap = self._available_rally_team_level_cap(action)
            if team_level_cap is None:
                return [], False
        selected = []
        had_unreadable_level = False
        for reference in sorted(reference_matches, key=lambda m: m["center"][1]):
            if self._stop_requested():
                break
            ref_y = reference["center"][1]
            _scale_x, scale_y = self._match_geometry_scale(reference)
            row_tolerance = max(0, round(action.row_tolerance * scale_y))
            row_targets = [
                target
                for target in remaining_targets
                if abs(target["center"][1] - ref_y) <= row_tolerance
            ]
            if not row_targets:
                continue
            level = None
            if apply_level_filter:
                level_status, level = self._row_level_status(
                    action,
                    reference,
                    max_level_override=team_level_cap,
                )
                if level_status == _LEVEL_UNREADABLE:
                    had_unreadable_level = True
                    continue
                if level_status != _LEVEL_ELIGIBLE:
                    continue
            chosen = self._choose_row_target(
                reference, row_targets, action.target_choice
            )
            selected.append({"reference": reference, "target": chosen, "level": level})
            remaining_targets.remove(chosen)
            if action.row_mode != "all":
                break
        return selected, had_unreadable_level

    def _revalidate_row_selections(
        self, action: Action, original_selections, matches: dict
    ):
        """Re-find delayed selections near their original rows and re-check level limits."""
        self._begin_level_diagnostic_generation()
        reference_index = action.match_condition_index
        target_index = action.on_condition_index
        if reference_index is None or target_index is None:
            return []

        references = list(matches.get(reference_index, []))
        remaining_targets = list(matches.get(target_index, []))
        team_level_cap = self._available_rally_team_level_cap(action)
        if team_level_cap is None:
            return []
        revalidated = []
        for original in original_selections:
            if self._stop_requested():
                break
            original_reference = original["reference"]
            original_y = original_reference["center"][1]
            _scale_x, scale_y = self._match_geometry_scale(original_reference)
            max_row_shift = max(
                8,
                round(min(30.0, max(8.0, action.row_tolerance * 0.5)) * scale_y),
            )
            nearby_references = sorted(
                (
                    reference
                    for reference in references
                    if abs(reference["center"][1] - original_y) <= max_row_shift
                ),
                key=lambda reference: abs(reference["center"][1] - original_y),
            )
            accepted = None
            for reference in nearby_references:
                ref_y = reference["center"][1]
                _ref_scale_x, ref_scale_y = self._match_geometry_scale(reference)
                row_tolerance = max(0, round(action.row_tolerance * ref_scale_y))
                row_targets = [
                    target
                    for target in remaining_targets
                    if abs(target["center"][1] - ref_y) <= row_tolerance
                ]
                if not row_targets:
                    continue
                level_status, level = self._row_level_status(
                    action,
                    reference,
                    max_level_override=team_level_cap,
                )
                if level_status != _LEVEL_ELIGIBLE:
                    continue
                chosen = self._choose_row_target(
                    reference,
                    row_targets,
                    action.target_choice,
                )
                accepted = {"reference": reference, "target": chosen, "level": level}
                remaining_targets.remove(chosen)
                references.remove(reference)
                break
            if accepted is not None:
                revalidated.append(accepted)
        return revalidated

    @staticmethod
    def _match_geometry_scale(match):
        if not match:
            return 1.0, 1.0
        try:
            scale_x = float(match.get("scale_x", match.get("scale", 1.0)))
            scale_y = float(match.get("scale_y", match.get("scale", 1.0)))
        except (AttributeError, TypeError, ValueError, OverflowError):
            return 1.0, 1.0
        if not math.isfinite(scale_x) or scale_x <= 0.0:
            scale_x = 1.0
        if not math.isfinite(scale_y) or scale_y <= 0.0:
            scale_y = 1.0
        return scale_x, scale_y

    def _row_level_allowed(self, action: Action, reference: dict):
        status, _level = self._row_level_status(action, reference)
        return status == _LEVEL_ELIGIBLE

    def _row_level_status(
        self,
        action: Action,
        reference: dict,
        *,
        max_level_override=_TEAM_LEVEL_CAP_UNSET,
    ):
        if self._stop_requested():
            return _LEVEL_UNREADABLE, None
        smart_override = max_level_override is not _TEAM_LEVEL_CAP_UNSET
        if max_level_override is None:
            return _LEVEL_INELIGIBLE, None
        if max_level_override == _TEAM_LEVEL_CAP_UNBOUNDED:
            effective_max = None
        elif smart_override:
            effective_max = int(max_level_override)
        else:
            effective_max = action.max_level
        if not smart_override and action.min_level is None and effective_max is None:
            return _LEVEL_ELIGIBLE, None

        level = self._read_level_for_row(action, reference)
        center = tuple(reference.get("center", ()))
        limits = self._level_limit_text(action, max_level=effective_max)
        if level is None:
            self.log(
                f"  [skip] row center={center} level unread; cannot compare with {limits}"
            )
            return _LEVEL_UNREADABLE, None
        if action.min_level is not None and level < action.min_level:
            self.log(
                f"  [skip] row center={center} level read {level}; {level} < min {action.min_level}"
            )
            return _LEVEL_INELIGIBLE, level
        if effective_max is not None and level > effective_max:
            max_label = (
                "available-team max"
                if max_level_override is not _TEAM_LEVEL_CAP_UNSET
                else "max"
            )
            self.log(
                f"  [skip] row center={center} level read {level}; "
                f"{level} > {max_label} {effective_max}"
            )
            return _LEVEL_INELIGIBLE, level
        self.log(
            f"  [level] row center={center} level read {level}; within {limits} => accepted"
        )
        return _LEVEL_ELIGIBLE, level

    def _level_limit_text(self, action: Action, *, max_level=_TEAM_LEVEL_CAP_UNSET):
        limits = []
        if action.min_level is not None:
            limits.append(f"min {action.min_level}")
        effective_max = (
            action.max_level if max_level is _TEAM_LEVEL_CAP_UNSET else max_level
        )
        if effective_max is not None:
            limits.append(f"max {effective_max}")
        return " and ".join(limits) if limits else "no level limits"

    def _resolve_rally_team_level_limits(
        self, action: Action
    ) -> tuple[
        dict[int, int | None],
        str,
        dict[str, str | int],
    ]:
        selectors = []
        for step_index, step in enumerate(
            getattr(getattr(self, "scenario", None), "steps", [])
        ):
            for action_index, candidate in enumerate(step.actions):
                if candidate.type == "select_rally_team":
                    selectors.append(
                        (
                            candidate,
                            {
                                "step_name": step.name,
                                "step_index": step_index,
                                "action_index": action_index,
                            },
                        )
                    )
        if len(selectors) != 1:
            raise ValueError(
                "Smart rally-team availability requires exactly one "
                f"select_rally_team action; found {len(selectors)}."
            )
        selector, identity = selectors[0]
        return (
            {
                1: selector.team1_max_level,
                3: selector.team3_max_level,
            },
            "select_rally_team",
            identity,
        )

    def _rally_team_level_limits(
        self, action: Action
    ) -> tuple[dict[int, int | None], str]:
        limits, source, _selector = self._resolve_rally_team_level_limits(action)
        return limits, source

    def _available_rally_team_level_cap(self, action: Action):
        if not has_smart_rally_team_prefilter(action):
            return _TEAM_LEVEL_CAP_UNSET
        pending = getattr(self, "_pending_rally_team_availability", None)
        if pending is not None:
            return pending.get("level_cap")
        required = (
            action.team_status_region,
            action.team_status_reference_size,
            action.team1_busy_template_path,
            action.team3_busy_template_path,
        )
        if not all(required):
            return _TEAM_LEVEL_CAP_UNSET
        reference_size = action.team_status_reference_size
        status_region = action.team_status_region
        if reference_size is None or status_region is None:
            return _TEAM_LEVEL_CAP_UNSET
        (
            level_limits,
            level_limits_source,
            level_limits_selector,
        ) = self._resolve_rally_team_level_limits(action)

        try:
            window_rect = self._get_target_window_rect()
            if not window_rect:
                _monitor_index, monitor = self._selected_monitor()
                window_rect = (
                    int(monitor["left"]),
                    int(monitor["top"]),
                    int(monitor["width"]),
                    int(monitor["height"]),
                )
            reference_width, reference_height = reference_size
            scale_x = window_rect[2] / reference_width
            scale_y = window_rect[3] / reference_height
            left, top, width, height = status_region
            capture_region = (
                window_rect[0] + round(left * scale_x),
                window_rect[1] + round(top * scale_y),
                max(1, round(width * scale_x)),
                max(1, round(height * scale_y)),
            )
            frame, off_x, off_y = self._grab(capture_region)
            template_scale = math.sqrt(scale_x * scale_y)
            scores = {}
            for team_number in (1, 3):
                template = self._load_template(
                    getattr(action, f"team{team_number}_busy_template_path")
                )
                scaled_template = self._scaled_template(template, template_scale)
                score, _location = self._best_scaled_template_match(
                    frame,
                    scaled_template,
                )
                scores[team_number] = float(score)
        except Exception as exc:
            if getattr(self, "_team_status_error_logged", None) != str(exc):
                self.log(f"  [team] availability prefilter unavailable: {exc}")
                self._team_status_error_logged = str(exc)
            return _TEAM_LEVEL_CAP_UNSET

        previous_availability = getattr(self, "_last_rally_team_availability", {}) or {}
        previous_busy = previous_availability.get("busy", {})
        busy_release_threshold = 0.50
        effective_thresholds = {
            team_number: (
                busy_release_threshold
                if previous_busy.get(team_number, False)
                else action.team_busy_confidence
            )
            for team_number in scores
        }
        busy = {
            team_number: score >= effective_thresholds[team_number]
            for team_number, score in scores.items()
        }
        idle_teams = [team_number for team_number in (1, 3) if not busy[team_number]]
        if not idle_teams:
            level_cap: int | str | None = None
        elif any(level_limits[team_number] is None for team_number in idle_teams):
            level_cap = _TEAM_LEVEL_CAP_UNBOUNDED
        else:
            level_cap = max(
                int(team_limit)
                for team_number in idle_teams
                if (team_limit := level_limits[team_number]) is not None
            )
        state = (busy[1], busy[3], level_cap)
        if state != getattr(self, "_last_rally_team_busy_state", None):
            self.log(
                "  [team] availability: "
                f"Team 1 {'busy' if busy[1] else 'idle'} ({scores[1]:.2f}), "
                f"Team 3 {'busy' if busy[3] else 'idle'} ({scores[3]:.2f}); "
                f"level cap {level_cap if level_cap is not None else 'none'}"
            )
            self._last_rally_team_busy_state = state
        self._last_rally_team_availability = {
            "capture_region": capture_region,
            "capture_origin": (off_x, off_y),
            "scores": scores,
            "effective_thresholds": effective_thresholds,
            "busy": busy,
            "level_cap": level_cap,
            "level_limits": level_limits,
            "level_limits_source": level_limits_source,
            "level_limits_selector": level_limits_selector,
            "frame": frame.copy(),
        }
        return level_cap

    def _read_level_for_row(self, action: Action, reference: dict):
        if self._stop_requested():
            return None
        roi = self._scaled_level_roi(action, reference)
        window_rect = self._get_target_window_rect()
        roi_text = tuple(roi)
        center_text = tuple(reference["center"])

        attempts = []
        provisional_attempts = []
        crop_candidates = self._capture_level_crop_candidates(
            action,
            reference,
            window_rect,
        )
        for attempt_index, (base_offset, rect, frame) in enumerate(crop_candidates):
            if self._stop_requested():
                return None
            ocr_result = self._read_level_with_ocr(frame)
            if self._stop_requested():
                return None

            attempt = {
                "frame": frame,
                "rect": rect,
                "base_offset": base_offset,
                "ocr_result": ocr_result,
                "status": "unread",
            }
            attempts.append(attempt)

            if ocr_result and ocr_result.level is not None:
                confidence_text = (
                    ""
                    if ocr_result.confidence is None
                    else f" conf={ocr_result.confidence:.2f}"
                )
                self.log(
                    f"  [level] {ocr_result.engine} read {ocr_result.level}{confidence_text} "
                    f"text='{ocr_result.text}' from crop rect={rect} roi={roi_text}"
                )
                confidence = ocr_result.confidence or 0.0
                if confidence >= LevelOcrReader.STRONG_ACCEPT_CONFIDENCE:
                    attempt["status"] = "accepted"
                    if attempt_index:
                        self.log(f"  [level] recovered with alternate crop rect={rect}")
                    self._remember_level_crop_offset(
                        action,
                        reference,
                        window_rect,
                        base_offset,
                    )
                    return self._finish_level_diagnostic(
                        action,
                        reference,
                        attempts,
                        decision="strong_ocr",
                        level=ocr_result.level,
                        selected_attempt=attempt,
                    )

                attempt["status"] = "provisional"
                provisional_attempts.append(attempt)
                self.log(
                    f"  [level] provisional OCR level {ocr_result.level} "
                    f"conf={confidence:.2f}; checking alternate crops"
                )
                continue

            if (
                ocr_result
                and ocr_result.error
                and not getattr(self, "_level_ocr_unavailable_logged", False)
            ):
                self.log(f"  [warn] OCR unavailable: {ocr_result.error}")
                self._level_ocr_unavailable_logged = True

        if provisional_attempts:
            counts = {}
            for attempt in provisional_attempts:
                level = attempt["ocr_result"].level
                counts[level] = counts.get(level, 0) + 1
            best_count = max(counts.values())
            winning_levels = [
                level for level, count in counts.items() if count == best_count
            ]
            if len(winning_levels) == 1 and best_count < 2:
                winning_level = winning_levels[0]
                self.log(
                    f"  [skip] only one provisional OCR crop read level "
                    f"{winning_level} for row center={center_text}; need consensus"
                )
                debug_attempt = self._best_ocr_attempt(provisional_attempts)
                return self._finish_level_diagnostic(
                    action,
                    reference,
                    attempts,
                    decision="provisional_insufficient_consensus",
                    level=None,
                    selected_attempt=debug_attempt,
                    save_event=True,
                )
            if len(winning_levels) == 1:
                winning_level = winning_levels[0]
                selected = max(
                    (
                        attempt
                        for attempt in provisional_attempts
                        if attempt["ocr_result"].level == winning_level
                    ),
                    key=lambda attempt: attempt["ocr_result"].confidence or 0.0,
                )
                selected_result = selected["ocr_result"]
                selected_confidence = selected_result.confidence or 0.0
                self.log(
                    f"  [level] accepted provisional level {winning_level} "
                    f"from {best_count} crop(s), best conf={selected_confidence:.2f}"
                )
                self._remember_level_crop_offset(
                    action,
                    reference,
                    window_rect,
                    selected["base_offset"],
                )
                return self._finish_level_diagnostic(
                    action,
                    reference,
                    attempts,
                    decision="provisional_consensus",
                    level=winning_level,
                    selected_attempt=selected,
                    save_event=True,
                )

            levels_text = ", ".join(
                f"{level} ({counts[level]} crop(s))" for level in sorted(winning_levels)
            )
            self.log(
                f"  [skip] conflicting provisional OCR levels for row "
                f"center={center_text}: {levels_text}"
            )
            debug_attempt = self._best_ocr_attempt(provisional_attempts)
            return self._finish_level_diagnostic(
                action,
                reference,
                attempts,
                decision="provisional_conflict",
                level=None,
                selected_attempt=debug_attempt,
                save_event=True,
            )

        debug_attempt = self._best_ocr_attempt(attempts)
        if debug_attempt:
            rect = debug_attempt["rect"]
            self.log(
                f"  [level] row center={center_text} unread from crop rect={rect} "
                f"roi={roi_text}"
            )
        return self._finish_level_diagnostic(
            action,
            reference,
            attempts,
            decision="unread",
            level=None,
            selected_attempt=debug_attempt,
            save_event=True,
        )

    def _finish_level_diagnostic(
        self,
        action,
        reference,
        attempts,
        *,
        decision,
        level,
        selected_attempt=None,
        save_event=False,
    ):
        generation = self._ensure_level_diagnostic_generation()
        serialized_attempts = []
        images = {}
        for index, attempt in enumerate(attempts):
            ocr_result = attempt.get("ocr_result")
            serialized_attempts.append(
                {
                    "index": index,
                    "base_offset": attempt.get("base_offset"),
                    "rect": attempt.get("rect"),
                    "status": attempt.get("status"),
                    "ocr": None
                    if ocr_result is None
                    else {
                        "level": ocr_result.level,
                        "text": ocr_result.text,
                        "confidence": ocr_result.confidence,
                        "engine": ocr_result.engine,
                        "error": ocr_result.error,
                    },
                }
            )
            frame = attempt.get("frame")
            offset = attempt.get("base_offset")
            images[f"crop_{index:02d}_offset_{offset}"] = frame

        selected_index = None
        if selected_attempt is not None:
            try:
                selected_index = attempts.index(selected_attempt)
            except ValueError:
                selected_index = None
        record = {
            "_generation": generation,
            "decision": decision,
            "level": level,
            "selected_attempt_index": selected_index,
            "reference": {
                "center": reference.get("center"),
                "box": reference.get("box"),
                "score": reference.get("score"),
                "scale_x": reference.get("scale_x"),
                "scale_y": reference.get("scale_y"),
            },
            "level_limits": {
                "min": action.min_level,
                "max": action.max_level,
            },
            "level_roi": action.level_roi,
            "attempts": serialized_attempts,
            "images": images,
        }
        diagnostics = getattr(self, "_last_level_diagnostics", None)
        if diagnostics is None:
            diagnostics = {}
            self._last_level_diagnostics = diagnostics
        if len(diagnostics) >= 16:
            diagnostics.pop(next(iter(diagnostics)))
        diagnostics[tuple(reference.get("center", ()))] = record

        if save_event:
            self._submit_rally_diagnostic(
                f"rally_level_{decision}",
                {
                    "scenario": getattr(getattr(self, "scenario", None), "name", ""),
                    "level_read": self._public_level_diagnostic_record(record),
                },
                images,
                reference=reference,
                crop_rects=[attempt.get("rect") for attempt in attempts],
                key=f"level:{decision}:{tuple(reference.get('center', ()))}:{level}",
                min_interval=2.0,
            )
        return level

    def _begin_level_diagnostic_generation(self):
        """Start a matching-pass scope for OCR evidence.

        Diagnostic records are keyed by row center for convenient lookup.  A
        center can recur on the next screen capture, however, so retaining the
        previous mapping could pair new boxes with old OCR evidence.
        """
        generation = int(getattr(self, "_level_diagnostic_generation", 0)) + 1
        self._level_diagnostic_generation = generation
        self._last_level_diagnostics = {}
        return generation

    def _ensure_level_diagnostic_generation(self):
        generation = getattr(self, "_level_diagnostic_generation", None)
        if generation is None:
            generation = self._begin_level_diagnostic_generation()
        return generation

    @staticmethod
    def _public_level_diagnostic_record(record):
        return {
            key: value
            for key, value in record.items()
            if key != "images" and not str(key).startswith("_")
        }

    @staticmethod
    def _template_path_key(path):
        return os.path.normcase(os.path.normpath(str(path or "").strip()))

    def _legacy_reference_sizes_for_path(self, template_path):
        """Collect safe legacy candidates without equating region and template metadata."""
        scenario = getattr(self, "scenario", None)
        if scenario is None:
            return ()
        target_key = self._template_path_key(template_path)
        path_sizes = []
        global_sizes = []

        def add(collection, value):
            if value and len(value) == 2 and value[0] > 0 and value[1] > 0:
                parsed = (int(value[0]), int(value[1]))
                if parsed not in collection:
                    collection.append(parsed)

        for step in getattr(scenario, "steps", ()):
            for condition in getattr(step, "conditions", ()):
                condition_size = (
                    condition.template_reference_size or condition.region_window_size
                )
                add(global_sizes, condition_size)
                if self._template_path_key(condition.template_path) == target_key:
                    add(path_sizes, condition_size)
                comparison_path = getattr(condition, "comparison_template_path", "")
                if self._template_path_key(comparison_path) == target_key:
                    add(
                        path_sizes,
                        getattr(
                            condition,
                            "comparison_template_reference_size",
                            None,
                        ),
                    )

        result = []
        for size in (*path_sizes, *global_sizes):
            if size not in result:
                result.append(size)
            if len(result) >= 4:
                break
        return tuple(result)

    def _condition_matching_kwargs(
        self,
        cond: ImageCondition,
        *,
        template_path=None,
        explicit_reference_size=_REFERENCE_UNSET,
    ):
        if template_path is None:
            template_path = cond.template_path
        if explicit_reference_size is _REFERENCE_UNSET:
            explicit_reference_size = cond.template_reference_size
        reference_size = explicit_reference_size or None
        reference_sizes = (
            ()
            if reference_size
            else self._legacy_reference_sizes_for_path(template_path)
        )
        current_size = None
        scenario = getattr(self, "scenario", None)
        if scenario is not None and scenario.target_window_title.strip():
            rect = self._get_target_window_rect()
            if rect:
                current_size = (rect[2], rect[3])
        elif scenario is not None and (reference_size or reference_sizes):
            try:
                _index, monitor = self._selected_monitor()
            except (AttributeError, RuntimeError):
                monitor = None
            if monitor is not None:
                current_size = (int(monitor["width"]), int(monitor["height"]))
        return {
            "match_mode": cond.match_mode,
            "use_grayscale": cond.use_grayscale,
            "reference_size": reference_size,
            "reference_sizes": reference_sizes,
            "current_size": current_size,
        }

    def _level_crop_rects(self, action: Action, reference: dict, window_rect=None):
        return [
            rect
            for _base_offset, rect in self._level_crop_candidates(
                action,
                reference,
                window_rect,
            )
        ]

    def _level_crop_candidates(self, action: Action, reference: dict, window_rect=None):
        roi = self._scaled_level_roi(action, reference)
        center_x, center_y = reference["center"]
        _scale_x, scale_y = self._match_geometry_scale(reference)
        base_offsets = (0, 8, 16, 24, -8, -16)
        candidates = []
        seen = set()
        for base_offset in base_offsets:
            y_offset = round(base_offset * scale_y)
            left = int(center_x + roi[0])
            top = int(center_y + roi[1] + y_offset)
            width = int(roi[2])
            height = int(roi[3])
            rect = self._constrain_level_rect((left, top, width, height), window_rect)
            if rect in seen:
                continue
            seen.add(rect)
            candidates.append((base_offset, rect))
        return candidates

    def _level_offset_cache_key(
        self, action: Action, reference: dict, window_rect=None
    ):
        scale_x, scale_y = self._match_geometry_scale(reference)
        window_size = None
        if window_rect:
            window_size = (int(window_rect[2]), int(window_rect[3]))
        roi = tuple(action.level_roi or [-90, -45, 220, 100])
        return (
            roi,
            window_size,
            round(scale_x, 4),
            round(scale_y, 4),
        )

    def _remember_level_crop_offset(
        self,
        action: Action,
        reference: dict,
        window_rect,
        base_offset,
    ):
        cache = getattr(self, "_level_offset_cache", None)
        if cache is None:
            cache = {}
            self._level_offset_cache = cache
        if len(cache) >= 32:
            cache.pop(next(iter(cache)))
        cache[self._level_offset_cache_key(action, reference, window_rect)] = (
            base_offset
        )

    def _capture_level_crop_candidates(
        self,
        action: Action,
        reference: dict,
        window_rect=None,
    ):
        candidates = self._level_crop_candidates(action, reference, window_rect)
        if not candidates:
            return []
        cache = getattr(self, "_level_offset_cache", {})
        preferred = cache.get(
            self._level_offset_cache_key(action, reference, window_rect)
        )
        if preferred is not None:
            candidates.sort(key=lambda item: item[0] != preferred)

        union_left = min(rect[0] for _offset, rect in candidates)
        union_top = min(rect[1] for _offset, rect in candidates)
        union_right = max(rect[0] + rect[2] for _offset, rect in candidates)
        union_bottom = max(rect[1] + rect[3] for _offset, rect in candidates)
        union_rect = (
            union_left,
            union_top,
            union_right - union_left,
            union_bottom - union_top,
        )
        snapshot = getattr(self, "_matching_row_snapshot", None)
        if snapshot is not None:
            union_frame = snapshot.crop(union_rect)
            if union_frame is None:
                self.log("  [skip] level crop falls outside the atomic row snapshot")
                return []
        else:
            union_frame, _off_x, _off_y = self._grab(union_rect)
        result = []
        for base_offset, rect in candidates:
            left = rect[0] - union_left
            top = rect[1] - union_top
            right = left + rect[2]
            bottom = top + rect[3]
            frame = union_frame[top:bottom, left:right]
            if (
                frame.size == 0
                or frame.shape[0] != rect[3]
                or frame.shape[1] != rect[2]
            ):
                continue
            # NumPy slices retain their full backing allocation.  These crops
            # can live until diagnostic submission, so detach each small ROI
            # from the (potentially full-window) atomic snapshot.
            result.append((base_offset, rect, frame.copy()))
        return result

    def _scaled_level_roi(self, action: Action, reference: dict):
        roi = action.level_roi or [-90, -45, 220, 100]
        scale_x, scale_y = self._match_geometry_scale(reference)
        return [
            round(roi[0] * scale_x),
            round(roi[1] * scale_y),
            max(1, round(roi[2] * scale_x)),
            max(1, round(roi[3] * scale_y)),
        ]

    def _constrain_level_rect(self, rect, window_rect=None):
        left, top, width, height = rect
        if window_rect:
            win_left, win_top, win_width, win_height = window_rect
            left = max(win_left, min(left, win_left + win_width - 1))
            top = max(win_top, min(top, win_top + win_height - 1))
            right = max(left + 1, min(left + width, win_left + win_width))
            bottom = max(top + 1, min(top + height, win_top + win_height))
            width, height = right - left, bottom - top
        return (left, top, width, height)

    def _best_ocr_attempt(self, attempts):
        if not attempts:
            return None
        with_text = [
            attempt
            for attempt in attempts
            if attempt.get("ocr_result") is not None and attempt["ocr_result"].text
        ]
        if with_text:
            return max(
                with_text,
                key=lambda attempt: attempt["ocr_result"].confidence or 0.0,
            )
        return attempts[0]

    def _read_level_with_ocr(self, frame):
        if self._stop_requested():
            return None
        result = self._get_level_ocr_reader().read_level(frame)
        return None if self._stop_requested() else result

    def _get_level_ocr_reader(self):
        lock = getattr(self, "_level_ocr_reader_lock", None)
        if lock is None:
            lock = threading.Lock()
            self._level_ocr_reader_lock = lock
        with lock:
            reader = getattr(self, "_level_ocr_reader", None)
            if reader is None:
                reader = LevelOcrReader()
                self._level_ocr_reader = reader
            return reader

    def _scenario_uses_level_ocr(self):
        for step in getattr(self.scenario, "steps", []):
            for action in getattr(step, "actions", []):
                if action.type != "click_matching_row":
                    continue
                if (
                    action.min_level is not None
                    or action.max_level is not None
                    or has_smart_rally_team_prefilter(action)
                ):
                    return True
        return False

    def _warm_up_level_ocr(self):
        if self._stop_requested():
            return False
        started = time.perf_counter()
        reader = self._get_level_ocr_reader()
        try:
            ready = reader.warm_up()
        except Exception as exc:
            self.log(f"[ocr] warm-up failed: {exc}")
            return False
        if self._stop_requested():
            return False
        elapsed = time.perf_counter() - started
        if ready:
            self.log(f"[ocr] warm-up ready in {elapsed:.2f}s")
        else:
            error = reader.init_error or "unknown error"
            self.log(f"[ocr] warm-up unavailable after {elapsed:.2f}s: {error}")
        return ready

    def _submit_rally_diagnostic(
        self,
        event_type,
        metadata,
        images=None,
        *,
        reference=None,
        crop_rects=None,
        matches=None,
        selections=None,
        key=None,
        min_interval=0.0,
        sample_rate=1.0,
        category="critical",
        dedupe_window=300.0,
        capture_images=True,
        context_snapshot=None,
    ):
        collector = getattr(self, "_diagnostic_collector", None)
        if collector is None or not getattr(self, "diagnostics_enabled", True):
            return None
        capture_reservation = (
            collector.reserve_capture(
                key or event_type,
                min_interval=min_interval,
                sample_rate=sample_rate,
            )
            if capture_images
            else None
        )
        capture_selected = capture_reservation is not None
        collector.record_decision(
            event_type,
            {
                **metadata,
                "screenshot_policy": {
                    "selected": capture_selected,
                    "category": category,
                    "minimum_interval_seconds": min_interval,
                    "dedupe_window_seconds": dedupe_window,
                },
            },
            category=category,
        )
        if not capture_selected:
            return None

        payload_images = dict(images or {})
        capture_metadata = {}
        dedupe_image = None
        try:
            snapshot = context_snapshot
            if snapshot is None:
                snapshot = getattr(self, "_matching_row_snapshot", None)
            if snapshot is not None:
                # Boxes, template matches, and level crops were all computed
                # from this frame.  Annotating a later live capture can make
                # valid evidence look incorrect when the rally list moves.
                context = snapshot.frame
                off_x, off_y = snapshot.left, snapshot.top
                capture_region = (
                    snapshot.left,
                    snapshot.top,
                    int(snapshot.frame.shape[1]),
                    int(snapshot.frame.shape[0]),
                )
            else:
                window_rect = self._get_target_window_rect()
                if window_rect:
                    capture_region = tuple(window_rect)
                else:
                    _monitor_index, monitor = self._selected_monitor()
                    capture_region = (
                        int(monitor["left"]),
                        int(monitor["top"]),
                        int(monitor["width"]),
                        int(monitor["height"]),
                    )
                context, off_x, off_y = self._grab(capture_region)
            annotated = context.copy()

            def draw_box(box, color, label=None, xywh=False):
                if not box or len(box) != 4:
                    return
                left, top, third, fourth = (int(value) for value in box)
                if xywh:
                    right, bottom = left + third, top + fourth
                else:
                    right, bottom = third, fourth
                p1 = (left - off_x, top - off_y)
                p2 = (right - off_x, bottom - off_y)
                cv2.rectangle(annotated, p1, p2, color, 3)
                if label:
                    cv2.putText(
                        annotated,
                        str(label),
                        (p1[0], max(16, p1[1] - 5)),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.55,
                        color,
                        2,
                        cv2.LINE_AA,
                    )

            for condition_index, condition_matches in (matches or {}).items():
                color = (0, 220, 0) if int(condition_index) == 0 else (255, 160, 0)
                for match in condition_matches:
                    draw_box(
                        match.get("box"),
                        color,
                        f"condition {condition_index} "
                        f"{match.get('confidence', match.get('score', ''))}",
                    )
            if reference:
                draw_box(reference.get("box"), (0, 255, 255), "level row")
            for rect in crop_rects or ():
                draw_box(rect, (255, 0, 255), "level crop", xywh=True)
            for index, selection in enumerate(selections or ()):
                draw_box(
                    selection.get("reference", {}).get("box"),
                    (0, 255, 255),
                    f"selected {index}",
                )
                draw_box(
                    selection.get("target", {}).get("box"),
                    (0, 0, 255),
                    f"target {index}",
                )

            payload_images["context_annotated"] = annotated
            dedupe_image = annotated
            capture_metadata = {
                "capture_region": capture_region,
                "capture_origin": [off_x, off_y],
                "capture_shape": list(context.shape),
                "capture_source": (
                    "atomic_matching_snapshot"
                    if snapshot is not None
                    else "live_capture"
                ),
            }
        except Exception as exc:
            capture_metadata = {"context_capture_error": str(exc)}

        metadata = {**metadata, **capture_metadata}
        return collector.submit(
            event_type,
            metadata,
            payload_images,
            force=True,
            category=category,
            dedupe_image=dedupe_image,
            dedupe_window=dedupe_window,
            key=key or event_type,
            log_decision=False,
            capture_reservation=capture_reservation,
        )

    @staticmethod
    def _selected_level_ocr_confidence(record):
        selected_index = record.get("selected_attempt_index")
        attempts = record.get("attempts") or ()
        if not isinstance(selected_index, int) or not 0 <= selected_index < len(
            attempts
        ):
            return None
        ocr = attempts[selected_index].get("ocr") or {}
        confidence = ocr.get("confidence")
        return None if confidence is None else float(confidence)

    def _matching_row_diagnostic_policy(self, decision, level_records, min_interval):
        if decision == "eligible_before_delay":
            routine_decisions = {"strong_ocr"}
            routine = bool(level_records)
            for record in level_records:
                confidence = self._selected_level_ocr_confidence(record)
                routine = (
                    routine
                    and record.get("decision") in routine_decisions
                    and confidence is not None
                    and confidence >= 0.95
                )
            if routine:
                return {
                    "category": "samples",
                    "min_interval": max(float(min_interval), 30.0 * 60.0),
                    "capture_reason": "periodic_high_confidence_success_sample",
                }
            return {
                "category": "critical",
                "min_interval": float(min_interval),
                "capture_reason": "accepted_lower_confidence_ocr_result",
            }
        if decision == "no_eligible_row":
            return {
                "category": "samples",
                "min_interval": max(float(min_interval), 10.0 * 60.0),
                "capture_reason": "periodic_no_eligible_row_sample",
            }
        return {
            "category": "critical",
            "min_interval": float(min_interval),
            "capture_reason": "failure_or_near_miss",
        }

    def _record_matching_row_diagnostic(
        self,
        step,
        action,
        selections,
        matches,
        decision,
        *,
        min_interval=0.0,
    ):
        level_records = []
        images = {}
        stored = getattr(self, "_last_level_diagnostics", {})
        generation = getattr(self, "_level_diagnostic_generation", None)
        diagnostic_selections = list(selections or ())
        if not diagnostic_selections:
            for reference in (matches or {}).get(action.match_condition_index, []):
                diagnostic_selections.append(
                    {
                        "reference": reference,
                        "target": {},
                        "level": None,
                    }
                )
        for selection_index, selection in enumerate(diagnostic_selections):
            center = tuple(selection.get("reference", {}).get("center", ()))
            record = stored.get(center)
            record_generation = record.get("_generation") if record else None
            if record and (
                record_generation is None
                or generation is None
                or record_generation == generation
            ):
                level_records.append(self._public_level_diagnostic_record(record))
                for name, frame in record.get("images", {}).items():
                    images[f"row_{selection_index}_{name}"] = frame

        match_records = {}
        for condition_index, condition_matches in (matches or {}).items():
            match_records[str(condition_index)] = [
                {
                    "center": match.get("center"),
                    "box": match.get("box"),
                    "score": match.get("confidence", match.get("score")),
                    "scale": match.get("scale"),
                }
                for match in condition_matches
            ]
        policy = self._matching_row_diagnostic_policy(
            decision,
            level_records,
            min_interval,
        )
        team_availability = dict(
            getattr(self, "_last_rally_team_availability", {}) or {}
        )
        team_status_frame = team_availability.pop("frame", None)
        if team_status_frame is not None:
            images["team_status"] = team_status_frame
        return self._submit_rally_diagnostic(
            f"rally_row_{decision}",
            {
                "scenario": getattr(getattr(self, "scenario", None), "name", ""),
                "step": step.name,
                "decision": decision,
                "action": {
                    "reference_condition": action.match_condition_index,
                    "target_condition": action.on_condition_index,
                    "row_tolerance": action.row_tolerance,
                    "row_mode": action.row_mode,
                    "target_choice": action.target_choice,
                    "min_level": action.min_level,
                    "max_level": action.max_level,
                    "pre_click_delay": action.pre_click_delay,
                },
                "selections": [
                    {
                        "level": selection.get("level"),
                        "reference_center": selection.get("reference", {}).get(
                            "center"
                        ),
                        "target_center": selection.get("target", {}).get("center"),
                    }
                    for selection in selections or ()
                ],
                "matches": match_records,
                "level_reads": level_records,
                "team_availability": team_availability,
                "capture_reason": policy["capture_reason"],
            },
            images,
            matches=matches,
            selections=selections,
            key=f"row:{step.name}:{decision}",
            min_interval=policy["min_interval"],
            category=policy["category"],
        )

    def _choose_row_target(self, reference, row_targets, target_choice):
        if target_choice == "rightmost":
            return sorted(row_targets, key=lambda m: m["center"][0], reverse=True)[0]
        if target_choice == "nearest":
            ref_x, ref_y = reference["center"]
            return sorted(
                row_targets,
                key=lambda m: (
                    (m["center"][0] - ref_x) ** 2 + (m["center"][1] - ref_y) ** 2
                ),
            )[0]
        return sorted(row_targets, key=lambda m: m["center"][0])[0]
