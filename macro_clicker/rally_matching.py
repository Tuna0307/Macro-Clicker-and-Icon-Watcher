"""Rally-row matching, level OCR, and evidence capture services for the macro engine."""
from __future__ import annotations

import math
import os
import re
import threading
import time
from dataclasses import dataclass

import cv2
import numpy as np

from .atomic_io import atomic_write_png
from .level_ocr import LevelOcrReader
from .models import Action, ImageCondition, project_path
from .runtime_paths import LEVEL_DEBUG_DIR

_REFERENCE_UNSET = object()
_LEVEL_ELIGIBLE = "eligible"
_LEVEL_INELIGIBLE = "ineligible"
_LEVEL_UNREADABLE = "unreadable"
_MATCHING_ROW_SNAPSHOT_KEY = object()
_LEVEL_DEBUG_MIN_INTERVAL = 15.0 * 60.0
_LEVEL_DEBUG_MAX_FILES = 200
_LEVEL_DEBUG_MAX_AGE = 7.0 * 24.0 * 60.0 * 60.0


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
        reference_index = action.match_condition_index
        target_index = action.on_condition_index
        if reference_index is None or target_index is None:
            return [], False

        reference_matches = matches.get(reference_index, [])
        remaining_targets = list(matches.get(target_index, []))
        selected = []
        had_unreadable_level = False
        for reference in sorted(reference_matches, key=lambda m: m["center"][1]):
            if self._stop_requested():
                break
            ref_y = reference["center"][1]
            _scale_x, scale_y = self._match_geometry_scale(reference)
            row_tolerance = max(0, round(action.row_tolerance * scale_y))
            row_targets = [
                target for target in remaining_targets
                if abs(target["center"][1] - ref_y) <= row_tolerance
            ]
            if not row_targets:
                continue
            level = None
            if apply_level_filter:
                level_status, level = self._row_level_status(action, reference)
                if level_status == _LEVEL_UNREADABLE:
                    had_unreadable_level = True
                    continue
                if level_status != _LEVEL_ELIGIBLE:
                    continue
            chosen = self._choose_row_target(reference, row_targets, action.target_choice)
            selected.append({"reference": reference, "target": chosen, "level": level})
            remaining_targets.remove(chosen)
            if action.row_mode != "all":
                break
        return selected, had_unreadable_level

    def _revalidate_row_selections(self, action: Action, original_selections, matches: dict):
        """Re-find delayed selections near their original rows and re-check level limits."""
        reference_index = action.match_condition_index
        target_index = action.on_condition_index
        if reference_index is None or target_index is None:
            return []

        references = list(matches.get(reference_index, []))
        remaining_targets = list(matches.get(target_index, []))
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
                level_status, level = self._row_level_status(action, reference)
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

    def _row_level_status(self, action: Action, reference: dict):
        if self._stop_requested():
            return _LEVEL_UNREADABLE, None
        if action.min_level is None and action.max_level is None:
            return _LEVEL_ELIGIBLE, None

        level = self._read_level_for_row(action, reference)
        center = tuple(reference.get("center", ()))
        limits = self._level_limit_text(action)
        if level is None:
            self.log(f"  [skip] row center={center} level unread; cannot compare with {limits}")
            return _LEVEL_UNREADABLE, None
        if action.min_level is not None and level < action.min_level:
            self.log(f"  [skip] row center={center} level read {level}; {level} < min {action.min_level}")
            return _LEVEL_INELIGIBLE, level
        if action.max_level is not None and level > action.max_level:
            self.log(f"  [skip] row center={center} level read {level}; {level} > max {action.max_level}")
            return _LEVEL_INELIGIBLE, level
        self.log(f"  [level] row center={center} level read {level}; within {limits} => accepted")
        return _LEVEL_ELIGIBLE, level

    def _level_limit_text(self, action: Action):
        limits = []
        if action.min_level is not None:
            limits.append(f"min {action.min_level}")
        if action.max_level is not None:
            limits.append(f"max {action.max_level}")
        return " and ".join(limits) if limits else "no level limits"

    def _read_level_for_row(self, action: Action, reference: dict):
        if self._stop_requested():
            return None
        roi = self._scaled_level_roi(action, reference)
        window_rect = self._get_target_window_rect()
        roi_text = tuple(roi)
        center_text = tuple(reference["center"])
        min_digits = max(1, int(getattr(action, "level_min_digits", 1) or 1))
        digit_templates = self._load_digit_templates(action.level_digit_template_dir)
        digit_templates = self._scale_digit_templates_for_match(
            digit_templates,
            reference,
        )
        if not digit_templates:
            warning_key = os.path.abspath(project_path(action.level_digit_template_dir or ""))
            warned = getattr(self, "_missing_digit_template_warnings", None)
            if warned is None:
                warned = set()
                self._missing_digit_template_warnings = warned
            if warning_key not in warned:
                self.log(
                    f"  [warn] no level digit templates found in "
                    f"{action.level_digit_template_dir}"
                )
                warned.add(warning_key)

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
            fallback_level = None
            if digit_templates:
                fallback_level = self._read_level_from_frame(
                    frame,
                    digit_templates,
                    min_digits=min_digits,
                )
            if self._stop_requested():
                return None

            attempt = {
                "frame": frame,
                "rect": rect,
                "base_offset": base_offset,
                "ocr_result": ocr_result,
                "fallback_level": fallback_level,
                "top_scores": None,
                "status": "unread",
            }
            attempts.append(attempt)

            if (
                ocr_result
                and ocr_result.level is not None
                and not self._ocr_level_meets_min_digits(ocr_result, min_digits)
            ):
                confidence_text = "" if ocr_result.confidence is None else f" conf={ocr_result.confidence:.2f}"
                self.log(
                    f"  [level] {ocr_result.engine} ignored {ocr_result.level}{confidence_text} "
                    f"text='{ocr_result.text}' from crop rect={rect} roi={roi_text}; need {min_digits} digit(s)"
                )

            if (
                ocr_result
                and ocr_result.level is not None
                and self._ocr_level_meets_min_digits(ocr_result, min_digits)
            ):
                confidence_text = "" if ocr_result.confidence is None else f" conf={ocr_result.confidence:.2f}"
                self.log(
                    f"  [level] {ocr_result.engine} read {ocr_result.level}{confidence_text} "
                    f"text='{ocr_result.text}' from crop rect={rect} roi={roi_text}"
                )
                fallback_confirms_ocr = fallback_level == ocr_result.level
                if fallback_level is not None and not fallback_confirms_ocr:
                    if self._should_ignore_digit_fallback_conflict(ocr_result, fallback_level):
                        self.log(
                            f"  [level] ignored digit_fallback={fallback_level} for row center={center_text}; "
                            f"matches OCR level {ocr_result.level} with extra digit noise"
                        )
                    else:
                        attempt["status"] = "conflict"
                        continue

                confidence = ocr_result.confidence or 0.0
                if (
                    confidence >= LevelOcrReader.STRONG_ACCEPT_CONFIDENCE
                    or fallback_confirms_ocr
                ):
                    if fallback_confirms_ocr:
                        self.log(
                            f"  [level] OCR and digit fallback agree on {ocr_result.level}"
                        )
                    if attempt_index:
                        self.log(f"  [level] recovered with alternate crop rect={rect}")
                    self._remember_level_crop_offset(
                        action,
                        reference,
                        window_rect,
                        base_offset,
                    )
                    decision = (
                        "ocr_fallback_agreement"
                        if fallback_confirms_ocr
                        else "strong_ocr"
                    )
                    return self._finish_level_diagnostic(
                        action,
                        reference,
                        attempts,
                        decision=decision,
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

            if ocr_result and ocr_result.error and not getattr(self, "_level_ocr_unavailable_logged", False):
                self.log(f"  [warn] OCR unavailable: {ocr_result.error}")
                self._level_ocr_unavailable_logged = True

            if fallback_level is not None:
                ocr_text = "" if not ocr_result or not ocr_result.text else f"; OCR text='{ocr_result.text}'"
                self.log(
                    f"  [level] digit_fallback read {fallback_level} from crop rect={rect} "
                    f"roi={roi_text}{ocr_text}"
                )
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
                    decision="fallback_only",
                    level=fallback_level,
                    selected_attempt=attempt,
                )

            attempt["top_scores"] = self._level_read_top_scores(frame, digit_templates)

        conflict_attempt = next((attempt for attempt in attempts if attempt["status"] == "conflict"), None)
        if conflict_attempt:
            rect = conflict_attempt["rect"]
            ocr_result = conflict_attempt["ocr_result"]
            fallback_level = conflict_attempt["fallback_level"]
            self.log(
                f"  [skip] OCR conflict for row center={center_text}: "
                f"{ocr_result.engine}={ocr_result.level}, digit_fallback={fallback_level}"
            )
            return self._finish_level_diagnostic(
                action,
                reference,
                attempts,
                decision="ocr_fallback_conflict",
                level=None,
                selected_attempt=conflict_attempt,
                save_event=True,
            )

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
                debug_attempt = self._best_level_debug_attempt(provisional_attempts)
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
                f"{level} ({counts[level]} crop(s))"
                for level in sorted(winning_levels)
            )
            self.log(
                f"  [skip] conflicting provisional OCR levels for row "
                f"center={center_text}: {levels_text}"
            )
            debug_attempt = self._best_level_debug_attempt(provisional_attempts)
            return self._finish_level_diagnostic(
                action,
                reference,
                attempts,
                decision="provisional_conflict",
                level=None,
                selected_attempt=debug_attempt,
                save_event=True,
            )

        debug_attempt = self._best_level_debug_attempt(attempts)
        if debug_attempt:
            rect = debug_attempt["rect"]
            self.log(
                f"  [level] row center={center_text} unread from crop rect={rect} "
                f"roi={roi_text}; need {min_digits} digit(s)"
            )
            top_scores = debug_attempt["top_scores"] or []
            if top_scores:
                scores_text = ", ".join(f"{digit}={score:.2f}" for digit, score in top_scores)
                self.log(f"  [debug] top digit scores: {scores_text}")
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
        serialized_attempts = []
        images = {}
        for index, attempt in enumerate(attempts):
            ocr_result = attempt.get("ocr_result")
            serialized_attempts.append({
                "index": index,
                "base_offset": attempt.get("base_offset"),
                "rect": attempt.get("rect"),
                "status": attempt.get("status"),
                "fallback_level": attempt.get("fallback_level"),
                "top_digit_scores": attempt.get("top_scores"),
                "ocr": None if ocr_result is None else {
                    "level": ocr_result.level,
                    "text": ocr_result.text,
                    "confidence": ocr_result.confidence,
                    "engine": ocr_result.engine,
                    "error": ocr_result.error,
                },
            })
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
                "min_digits": action.level_min_digits,
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
            event_path = self._submit_rally_diagnostic(
                f"rally_level_{decision}",
                {
                    "scenario": getattr(getattr(self, "scenario", None), "name", ""),
                    "level_read": {key: value for key, value in record.items() if key != "images"},
                },
                images,
                reference=reference,
                crop_rects=[attempt.get("rect") for attempt in attempts],
                key=f"level:{decision}:{tuple(reference.get('center', ()))}:{level}",
                min_interval=2.0,
            )
            if event_path and selected_attempt is not None:
                debug_path = self._save_level_debug_crop(
                    selected_attempt.get("frame"),
                    selected_attempt.get("rect"),
                    reference,
                    decision=decision,
                )
                if debug_path:
                    self.log(f"  [debug] saved curated level crop: {debug_path}")
        return level

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
            if (
                value
                and len(value) == 2
                and value[0] > 0
                and value[1] > 0
            ):
                parsed = (int(value[0]), int(value[1]))
                if parsed not in collection:
                    collection.append(parsed)

        for step in getattr(scenario, "steps", ()):
            for condition in getattr(step, "conditions", ()):
                condition_size = (
                    condition.template_reference_size
                    or condition.region_window_size
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

    def _level_offset_cache_key(self, action: Action, reference: dict, window_rect=None):
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
        cache[self._level_offset_cache_key(action, reference, window_rect)] = base_offset

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
                self.log(
                    "  [skip] level crop falls outside the atomic row snapshot"
                )
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
            if frame.size == 0 or frame.shape[0] != rect[3] or frame.shape[1] != rect[2]:
                continue
            result.append((base_offset, rect, frame))
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

    def _scale_digit_templates_for_match(self, digit_templates, reference):
        scale_x, scale_y = self._match_geometry_scale(reference)
        if abs(scale_x - 1.0) < 0.001 and abs(scale_y - 1.0) < 0.001:
            return digit_templates
        scaled = {}
        for digit, template in digit_templates.items():
            height, width = template.shape[:2]
            scaled[digit] = cv2.resize(
                template,
                (
                    max(1, round(width * scale_x)),
                    max(1, round(height * scale_y)),
                ),
                interpolation=cv2.INTER_NEAREST,
            )
        return scaled

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

    def _best_level_debug_attempt(self, attempts):
        if not attempts:
            return None
        with_scores = [attempt for attempt in attempts if attempt.get("top_scores")]
        if with_scores:
            return max(with_scores, key=lambda attempt: attempt["top_scores"][0][1])
        with_text = [
            attempt for attempt in attempts
            if attempt.get("ocr_result") is not None and attempt["ocr_result"].text
        ]
        if with_text:
            return max(
                with_text,
                key=lambda attempt: attempt["ocr_result"].confidence or 0.0,
            )
        return attempts[0]

    def _is_spurious_leading_one_conflict(self, ocr_level, fallback_level):
        if ocr_level is None or fallback_level is None:
            return False
        try:
            return int(fallback_level) == int(f"1{int(ocr_level)}")
        except (TypeError, ValueError):
            return False

    def _should_ignore_digit_fallback_conflict(self, ocr_result, fallback_level):
        if ocr_result is None or ocr_result.level is None or fallback_level is None:
            return False
        if self._is_spurious_leading_one_conflict(ocr_result.level, fallback_level):
            return True
        try:
            ocr_text = str(int(ocr_result.level))
            fallback_text = str(int(fallback_level))
        except (TypeError, ValueError):
            return False
        confidence = ocr_result.confidence
        if confidence is not None and confidence >= 0.95:
            return True
        if confidence is not None and confidence < 0.75:
            return False
        if (
            confidence is not None
            and confidence >= 0.75
            and self._ocr_text_has_level_prefix(ocr_result.text)
            and self._is_repeated_one_noise(fallback_text, ocr_text)
        ):
            return True
        if confidence is not None and confidence >= 0.85 and self._ocr_text_has_level_prefix(ocr_result.text):
            return True
        return len(fallback_text) > len(ocr_text) and ocr_text in fallback_text

    def _is_repeated_one_noise(self, fallback_text, ocr_text):
        return (
            len(fallback_text) > len(ocr_text)
            and len(ocr_text) >= 2
            and set(fallback_text) == {"1"}
        )

    def _ocr_text_has_level_prefix(self, text):
        normalized = (text or "").lower().replace(" ", "")
        normalized = normalized.replace("1v", "lv").replace("iv", "lv").replace("ly", "lv")
        return bool(re.search(r"l[v\W_]*\d", normalized))

    def _ocr_level_meets_min_digits(self, ocr_result, min_digits):
        if min_digits <= 1 or ocr_result is None or ocr_result.level is None:
            return True
        try:
            extracted_digits = str(abs(int(ocr_result.level)))
        except (TypeError, ValueError, OverflowError):
            return False
        return len(extracted_digits) >= min_digits

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
                if (
                    action.type == "click_matching_row"
                    and (action.min_level is not None or action.max_level is not None)
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

    def _load_digit_templates(self, folder):
        if self._stop_requested():
            return {}
        folder = project_path(folder)
        cache_key = os.path.abspath(folder)
        cache = getattr(self, "_digit_template_cache", None)
        if cache is None:
            cache = {}
            self._digit_template_cache = cache
        if cache_key in cache:
            return cache[cache_key]

        templates = {}
        for digit in "0123456789":
            if self._stop_requested():
                return {}
            path = os.path.join(folder, f"{digit}.png")
            img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
            if img is not None:
                templates[digit] = self._preprocess_digit_image(img)
        cache[cache_key] = templates
        return templates

    def _read_level_from_frame(
        self,
        frame,
        digit_templates,
        confidence=0.52,
        min_digits=1,
        min_score_margin=0.0,
    ):
        if self._stop_requested() or frame is None or not digit_templates:
            return None
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if len(frame.shape) == 3 else frame
        prepared_frame = self._preprocess_digit_image(gray)
        candidates = []
        for digit, template in digit_templates.items():
            if self._stop_requested():
                return None
            th, tw = template.shape[:2]
            if prepared_frame.shape[0] < th or prepared_frame.shape[1] < tw:
                continue
            result = cv2.matchTemplate(prepared_frame, template, cv2.TM_CCOEFF_NORMED)
            ys, xs = np.where(result >= confidence)
            digit_candidates = sorted(
                ((int(x), int(y), float(result[y, x])) for x, y in zip(xs, ys)),
                key=lambda item: item[2],
                reverse=True,
            )
            kept = []
            for x, y, score in digit_candidates:
                if self._stop_requested():
                    return None
                box = (x, y, x + tw, y + th)
                if any(self._box_iou(box, existing["box"]) > 0.3 for existing in kept):
                    continue
                kept.append({"digit": digit, "box": box, "score": score})
                if len(kept) >= 12:
                    break
            candidates.extend(kept)

        candidates = self._filter_digit_candidates_by_margin(candidates, min_score_margin)
        selected = []
        for candidate in sorted(candidates, key=lambda item: item["score"], reverse=True):
            if self._stop_requested():
                return None
            if any(self._box_iou(candidate["box"], existing["box"]) > 0.3 for existing in selected):
                continue
            selected.append(candidate)
        if not selected:
            return None

        selected = self._select_level_digit_run(selected, min_digits)
        if not selected:
            return None

        digits = "".join(item["digit"] for item in selected)
        if len(digits) < min_digits:
            return None
        return int(digits) if digits else None

    def _filter_digit_candidates_by_margin(self, candidates, min_score_margin):
        if not candidates or min_score_margin <= 0:
            return candidates

        groups = []
        for candidate in sorted(candidates, key=lambda item: item["score"], reverse=True):
            if self._stop_requested():
                return []
            target_group = None
            for group in groups:
                if any(self._box_iou(candidate["box"], existing["box"]) > 0.3 for existing in group):
                    target_group = group
                    break
            if target_group is None:
                groups.append([candidate])
            else:
                target_group.append(candidate)

        filtered = []
        for group in groups:
            if self._stop_requested():
                return []
            group = sorted(group, key=lambda item: item["score"], reverse=True)
            if len(group) == 1 or group[0]["score"] - group[1]["score"] >= min_score_margin:
                filtered.append(group[0])
        return filtered

    def _select_level_digit_run(self, candidates, min_digits):
        if self._stop_requested() or not candidates:
            return []

        y_groups = []
        for candidate in sorted(candidates, key=lambda item: self._box_center(item["box"])[1]):
            if self._stop_requested():
                return []
            _, center_y = self._box_center(candidate["box"])
            placed = False
            for group in y_groups:
                group_y = sum(self._box_center(item["box"])[1] for item in group) / len(group)
                avg_height = sum(item["box"][3] - item["box"][1] for item in group) / len(group)
                if abs(center_y - group_y) <= max(6, avg_height * 0.45):
                    group.append(candidate)
                    placed = True
                    break
            if not placed:
                y_groups.append([candidate])

        runs = []
        for group in y_groups:
            if self._stop_requested():
                return []
            group.sort(key=lambda item: item["box"][0])
            run = []
            for candidate in group:
                if not run:
                    run = [candidate]
                    continue
                prev = run[-1]
                prev_width = prev["box"][2] - prev["box"][0]
                cur_width = candidate["box"][2] - candidate["box"][0]
                max_gap = max(8, max(prev_width, cur_width) * 0.8)
                gap = candidate["box"][0] - prev["box"][2]
                if gap <= max_gap:
                    run.append(candidate)
                else:
                    runs.append(run)
                    run = [candidate]
            if run:
                runs.append(run)

        valid_runs = [run for run in runs if len(run) >= min_digits]
        if not valid_runs:
            return []

        def run_key(run):
            right = max(item["box"][2] for item in run)
            baseline = sum(self._box_center(item["box"])[1] for item in run) / len(run)
            avg_score = sum(item["score"] for item in run) / len(run)
            return (baseline, right, len(run), avg_score)

        return sorted(valid_runs, key=run_key, reverse=True)[0]

    def _box_center(self, box):
        return ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)

    def _level_read_top_scores(self, frame, digit_templates, limit=5):
        if self._stop_requested() or frame is None or not digit_templates:
            return []
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if len(frame.shape) == 3 else frame
        prepared_frame = self._preprocess_digit_image(gray)
        scores = []
        for digit, template in digit_templates.items():
            if self._stop_requested():
                return []
            th, tw = template.shape[:2]
            if prepared_frame.shape[0] < th or prepared_frame.shape[1] < tw:
                continue
            result = cv2.matchTemplate(prepared_frame, template, cv2.TM_CCOEFF_NORMED)
            scores.append((digit, float(result.max())))
        return sorted(scores, key=lambda item: item[1], reverse=True)[:limit]

    def _save_level_debug_crop(self, frame, rect, reference, *, decision="unread"):
        if (
            frame is None
            or rect is None
            or not getattr(self, "diagnostics_enabled", True)
        ):
            return None
        center = tuple(reference.get("center", ()))
        rate_key = (str(decision), center)
        now = time.monotonic()
        last_writes = getattr(self, "_last_level_debug_writes", None)
        if last_writes is None:
            last_writes = {}
            self._last_level_debug_writes = last_writes
        last_write = last_writes.get(rate_key)
        if last_write is not None and now - last_write < _LEVEL_DEBUG_MIN_INTERVAL:
            return None
        try:
            os.makedirs(LEVEL_DEBUG_DIR, exist_ok=True)
            left, top, width, height = rect
            center_x, center_y = center
            stamp = f"{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns() % 1_000_000_000:09d}"
            safe_decision = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(decision))[:48]
            filename = (
                f"level_{stamp}_{safe_decision}_{left}_{top}_{width}x{height}_"
                f"row{center_x}_{center_y}.png"
            )
            path = os.path.join(LEVEL_DEBUG_DIR, filename)
            atomic_write_png(path, frame)
            last_writes[rate_key] = now
            self._prune_level_debug_crops()
            return path
        except Exception as e:
            self.log(f"  [debug] could not save level crop: {e}")
            return None

    def _prune_level_debug_crops(self):
        try:
            now = time.time()
            entries = []
            for name in os.listdir(LEVEL_DEBUG_DIR):
                if not name.startswith("level_") or not name.lower().endswith(".png"):
                    continue
                path = os.path.join(LEVEL_DEBUG_DIR, name)
                try:
                    modified = os.path.getmtime(path)
                except OSError:
                    continue
                if now - modified > _LEVEL_DEBUG_MAX_AGE:
                    try:
                        os.remove(path)
                    except OSError:
                        pass
                    continue
                entries.append((modified, path))
            entries.sort(reverse=True)
            for _modified, path in entries[_LEVEL_DEBUG_MAX_FILES:]:
                try:
                    os.remove(path)
                except OSError:
                    pass
        except OSError as exc:
            self.log(f"  [debug] could not prune level crops: {exc}")

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
                draw_box(selection.get("reference", {}).get("box"), (0, 255, 255), f"selected {index}")
                draw_box(selection.get("target", {}).get("box"), (0, 0, 255), f"target {index}")

            payload_images["context_annotated"] = annotated
            dedupe_image = annotated
            capture_metadata = {
                "capture_region": capture_region,
                "capture_origin": [off_x, off_y],
                "capture_shape": list(context.shape),
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
        if not isinstance(selected_index, int) or not 0 <= selected_index < len(attempts):
            return None
        ocr = attempts[selected_index].get("ocr") or {}
        confidence = ocr.get("confidence")
        return None if confidence is None else float(confidence)

    def _matching_row_diagnostic_policy(self, decision, level_records, min_interval):
        if decision == "eligible_before_delay":
            routine_decisions = {"strong_ocr", "ocr_fallback_agreement"}
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
                "capture_reason": "accepted_low_confidence_or_fallback_result",
            }
        if decision == "no_eligible_row":
            return {
                "category": "critical",
                "min_interval": max(float(min_interval), 5.0 * 60.0),
                "capture_reason": "rate_limited_no_eligible_row",
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
        diagnostic_selections = list(selections or ())
        if not diagnostic_selections:
            for reference in (matches or {}).get(action.match_condition_index, []):
                diagnostic_selections.append({
                    "reference": reference,
                    "target": {},
                    "level": None,
                })
        for selection_index, selection in enumerate(diagnostic_selections):
            center = tuple(selection.get("reference", {}).get("center", ()))
            record = stored.get(center)
            if record:
                level_records.append({
                    key: value for key, value in record.items() if key != "images"
                })
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
                        "reference_center": selection.get("reference", {}).get("center"),
                        "target_center": selection.get("target", {}).get("center"),
                    }
                    for selection in selections or ()
                ],
                "matches": match_records,
                "level_reads": level_records,
                "capture_reason": policy["capture_reason"],
            },
            images,
            matches=matches,
            selections=selections,
            key=f"row:{step.name}:{decision}",
            min_interval=policy["min_interval"],
            category=policy["category"],
        )

    def _preprocess_digit_image(self, image):
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image
        gray = gray.astype(np.uint8, copy=False)
        bright_cutoff = max(180, int(np.percentile(gray, 85)))
        mask = np.where(gray >= bright_cutoff, 255, 0).astype(np.uint8)
        num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
        cleaned = np.zeros_like(mask)
        min_area = 4
        min_height = 3
        for label in range(1, num_labels):
            if self._stop_requested():
                return cleaned
            area = stats[label, cv2.CC_STAT_AREA]
            height = stats[label, cv2.CC_STAT_HEIGHT]
            if area >= min_area and height >= min_height:
                cleaned[labels == label] = 255
        return cleaned

    def _choose_row_target(self, reference, row_targets, target_choice):
        if target_choice == "rightmost":
            return sorted(row_targets, key=lambda m: m["center"][0], reverse=True)[0]
        if target_choice == "nearest":
            ref_x, ref_y = reference["center"]
            return sorted(
                row_targets,
                key=lambda m: (m["center"][0] - ref_x) ** 2 + (m["center"][1] - ref_y) ** 2,
            )[0]
        return sorted(row_targets, key=lambda m: m["center"][0])[0]
