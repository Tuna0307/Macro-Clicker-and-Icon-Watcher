"""
Runtime engine. Evaluates a Scenario's steps every polling cycle and
executes their actions -- the same model as game_macro.py, but driven
by a Scenario object and running on a background thread (with a log
callback instead of print) so it plays nicely with a GUI.
"""
import inspect
import math
import os
import threading
import time
from typing import Callable, Optional

import cv2
import keyboard
import mss
import numpy as np
import pyautogui
from PIL import Image

from .detection_core import (
    DETECTION_UNAVAILABLE,
    MACRO_DEFAULT_SCALES,
    _best_variant_match,
    _bounded_local_peaks,
    _spatial_deviation,
    box_iou,
    capture_bgr,
    find_template_matches,
    monitor_rect,
    physical_monitor_index,
    prepare_template_variants,
    resolution_scale_pairs,
    resize_template,
)
from .diagnostics import get_diagnostic_collector
from .level_ocr import LevelOcrReader
from .models import (
    Action,
    ImageCondition,
    Scenario,
    Step,
    project_path,
    validate_scenario,
)
from .rally_matching import (
    RallyMatchingMixin,
    _CaptureSnapshot,
    _MATCHING_ROW_SNAPSHOT_KEY,
    _REFERENCE_UNSET,
)
from .window_locator import (
    find_window_rect,
    resolve_saved_capture_region,
)

_WINDOW_UNAVAILABLE = DETECTION_UNAVAILABLE
_MATCHING_ROW_SNAPSHOT_STEP_KEY = object()


class _StopRequested(Exception):
    """Internal control-flow signal used to leave expensive engine work quietly."""


class MacroEngine(RallyMatchingMixin):
    TEMPLATE_SCALE_FACTORS = MACRO_DEFAULT_SCALES

    def __init__(self, scenario: Scenario, log: Optional[Callable[[str], None]] = None):
        self.scenario = Scenario.from_dict(scenario.to_dict())
        self.log = log or (lambda msg: None)
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._ready_event = threading.Event()
        self._last_fired = {s.name: 0.0 for s in self.scenario.steps}
        self._template_cache = {}
        self._scaled_template_cache = {}
        self._prepared_template_cache = {}
        self._level_offset_cache = {}
        self._last_level_diagnostics = {}
        self._matching_row_snapshot = None
        self._pending_rally_level = None
        self._last_rally_team_busy_state: Optional[
            tuple[bool, bool, Optional[int]]
        ] = None
        self._last_rally_team_availability: dict = {}
        self._pending_rally_team_availability = None
        self._abort_current_step = False
        self._cleanup_after_abort = False
        self._level_ocr_reader: Optional[LevelOcrReader] = None
        self._level_ocr_reader_lock = threading.Lock()
        self._level_ocr_unavailable_logged = False
        self._target_window_rect = None
        self._target_window_missing_logged = False
        self._monitor_index_warning_logged = None
        self._window_rect_provider = find_window_rect
        self._window_rect_lookup_cache: Optional[dict] = None
        self.sct = mss.MSS()
        self._sct_closed = False
        pyautogui.FAILSAFE = True
        pyautogui.PAUSE = 0.0
        self.click_move_duration = 0.0
        self.fast_poll_after_fire = 0.03
        self.slow_step_threshold = 0.15
        self.slow_cycle_threshold = 0.35
        self.perf_log_interval = 10.0
        self.capture_retry_attempts = 3
        self.capture_retry_backoff = 0.05
        self.low_variance_threshold = 1.0
        self.max_matches_per_scale = 128
        self.max_multiscale_candidates = 512
        self._last_perf_log = {}
        self._hotkey_handle = None
        self._ever_started = False
        self._stop_logged = False
        self._all_match_indices = {}
        self._step_lookup = {}
        self._step_names_snapshot = ()
        self._evaluate_uses_frame_cache = self._evaluate_step_supports_frame_cache(self._evaluate_step)
        self.diagnostics_enabled = self.scenario.diagnostics_enabled
        self._diagnostic_collector = get_diagnostic_collector(log=self.log)

    # ---- public control ----
    @property
    def is_running(self):
        return self._thread is not None and self._thread.is_alive()

    @property
    def is_ready(self):
        """True once optional OCR warm-up is complete and the run loop is ready."""
        ready_event = getattr(self, "_ready_event", None)
        return bool(ready_event is not None and ready_event.is_set() and self.is_running)

    def start(self):
        if self.is_running:
            return
        validate_scenario(self.scenario, require_files=True)
        if getattr(self, "_sct_closed", False):
            self.sct = mss.MSS()
            self._sct_closed = False
        self._stop_event.clear()
        self._ready_event.clear()
        self._stop_logged = False
        self._pending_rally_level = None
        self._last_rally_team_busy_state = None
        self._last_rally_team_availability = {}
        self._pending_rally_team_availability = None
        self._abort_current_step = False
        self._cleanup_after_abort = False
        self._last_perf_log.clear()
        self._ever_started = True
        self._step_names_snapshot = ()
        self._refresh_step_caches()
        self._evaluate_uses_frame_cache = self._evaluate_step_supports_frame_cache(self._evaluate_step)
        for s in self.scenario.steps:
            self._last_fired[s.name] = 0.0
        try:
            self._hotkey_handle = keyboard.add_hotkey(self.scenario.kill_switch, self.stop)
        except Exception as e:
            self._close_capture()
            raise RuntimeError(
                f"Could not register required kill switch '{self.scenario.kill_switch}': {e}"
            ) from e
        uses_level_ocr = self._scenario_uses_level_ocr()
        thread_target = self._run_after_ocr_warmup if uses_level_ocr else self._run_loop
        if uses_level_ocr:
            self.log(f"Scenario '{self.scenario.name}' preparing OCR before start...")
        self._thread = threading.Thread(target=thread_target, daemon=True)
        if not uses_level_ocr:
            self._ready_event.set()
        self._thread.start()
        if not uses_level_ocr:
            self.log(f"Scenario '{self.scenario.name}' started. Kill switch: {self.scenario.kill_switch.upper()}")

    def request_stop(self):
        """Signal a stop without waiting; safe to call from the Tk event loop."""
        was_active = (
            self.is_running
            or self._hotkey_handle is not None
            or getattr(self, "_ever_started", False)
        )
        self._stop_event.set()
        ready_event = getattr(self, "_ready_event", None)
        if ready_event is not None:
            ready_event.clear()
        self._remove_hotkey()
        return was_active

    def stop(self):
        was_active = self.request_stop()
        running = self.is_running
        thread = getattr(self, "_thread", None)
        if running and thread is not None and threading.current_thread() is not thread:
            thread.join(timeout=2.0)
            running = thread.is_alive()
        if not running:
            self._close_capture()
        if was_active and not getattr(self, "_stop_logged", False):
            self.log("Scenario stopped.")
            self._stop_logged = True
            self._ever_started = False

    def _refresh_step_caches(self):
        steps = tuple(getattr(self.scenario, "steps", []))
        names = tuple(step.name for step in steps)
        if names == getattr(self, "_step_names_snapshot", ()):
            return steps
        self._step_names_snapshot = names
        self._step_lookup = {step.name: step for step in steps}
        self._all_match_indices = {
            step.name: self._condition_indices_needing_all_matches(step)
            for step in steps
        }
        return steps

    def _cleanup_runtime(self):
        self._remove_hotkey()
        self._close_capture()

    def _remove_hotkey(self):
        if self._hotkey_handle is not None:
            try:
                keyboard.remove_hotkey(self._hotkey_handle)
            except Exception:
                pass
            self._hotkey_handle = None

    def _close_capture(self):
        if getattr(self, "_sct_closed", False):
            return
        sct = getattr(self, "sct", None)
        close = getattr(sct, "close", None)
        if close is None:
            return
        try:
            close()
        except Exception:
            pass
        self._sct_closed = True

    def _sleep_until_stop(self, seconds):
        try:
            seconds = max(0.0, float(seconds))
        except (TypeError, ValueError):
            seconds = 0.0
        return self._stop_event.wait(seconds)

    def _stop_requested(self):
        event = getattr(self, "_stop_event", None)
        return bool(event is not None and event.is_set())

    def _raise_if_stopped(self):
        if self._stop_requested():
            raise _StopRequested()

    def _should_log_perf(self, key, now=None):
        if now is None:
            now = time.monotonic()
        interval = max(0.0, float(getattr(self, "perf_log_interval", 10.0)))
        last_logs = getattr(self, "_last_perf_log", None)
        if last_logs is None:
            last_logs = {}
            self._last_perf_log = last_logs
        last = last_logs.get(key)
        if last is not None and now >= last and now - last < interval:
            return False
        last_logs[key] = now
        return True

    # ---- internals ----
    def _run_loop(self):
        try:
            while not self._stop_event.is_set():
                fired = self._cycle()
                if fired:
                    delay = min(self.scenario.poll_interval, getattr(self, "fast_poll_after_fire", 0.03))
                else:
                    delay = self.scenario.poll_interval
                self._sleep_until_stop(delay)
        except _StopRequested:
            pass
        except pyautogui.FailSafeException:
            self.log("[safety] scenario stopped because the mouse reached a fail-safe corner")
        except Exception as e:
            self.log(f"[error] engine stopped ({type(e).__name__}): {e}")
        finally:
            self._cleanup_runtime()

    def _run_after_ocr_warmup(self):
        ready = self._warm_up_level_ocr()
        if self._stop_event.is_set():
            self._cleanup_runtime()
            return
        if not ready:
            self.log("[ocr] unavailable; rows that require a level will be skipped")
        self.log(
            f"Scenario '{self.scenario.name}' started. "
            f"Kill switch: {self.scenario.kill_switch.upper()}"
        )
        ready_event = getattr(self, "_ready_event", None)
        if ready_event is not None:
            ready_event.set()
        self._run_loop()

    def _evaluate_step_supports_frame_cache(self, evaluate_step):
        try:
            parameters = inspect.signature(evaluate_step).parameters
        except (TypeError, ValueError):
            return False
        return (
            "frame_cache" in parameters
            or any(param.kind == inspect.Parameter.VAR_KEYWORD for param in parameters.values())
        )

    def _load_template(self, path):
        self._raise_if_stopped()
        resolved_path = project_path(path)
        cache_key = os.path.abspath(resolved_path) if resolved_path else resolved_path
        if cache_key not in self._template_cache:
            img = cv2.imread(resolved_path, cv2.IMREAD_COLOR)
            if img is None:
                raise FileNotFoundError(f"Could not load template image: {path}")
            self._template_cache[cache_key] = img
        self._raise_if_stopped()
        return self._template_cache[cache_key]

    def _selected_monitor(self):
        monitors = self.sct.monitors
        requested = self.scenario.monitor_index
        monitor_index = physical_monitor_index(monitors, requested)
        if monitor_index is None:
            raise RuntimeError("No screen monitor is available")
        if (
            monitor_index != requested
            and getattr(self, "_monitor_index_warning_logged", None) != requested
        ):
            self.log(
                f"[warn] monitor #{requested} is unavailable; "
                f"using monitor #{monitor_index}"
            )
            self._monitor_index_warning_logged = requested
        return monitor_index, monitors[monitor_index]

    def _grab(self, region=None):
        self._raise_if_stopped()
        if region:
            left, top, width, height = region
            monitor = {"left": left, "top": top, "width": width, "height": height}
        else:
            _monitor_index, monitor = self._selected_monitor()
        attempts = max(1, int(getattr(self, "capture_retry_attempts", 3)))
        backoff = max(0.0, float(getattr(self, "capture_retry_backoff", 0.05)))
        for attempt in range(attempts):
            self._raise_if_stopped()
            try:
                frame = capture_bgr(self.sct, monitor)
                break
            except Exception as exc:
                self._raise_if_stopped()
                if attempt + 1 >= attempts:
                    raise
                self.log(
                    f"[warn] screen capture failed; retrying "
                    f"({attempt + 1}/{attempts - 1}): {exc}"
                )
                if self._sleep_until_stop(backoff * (2 ** attempt)):
                    raise _StopRequested() from exc
        self._raise_if_stopped()
        return frame, monitor["left"], monitor["top"]

    def _get_target_window_rect(self):
        title = self.scenario.target_window_title.strip()
        if not title:
            return None

        lookup_cache = getattr(self, "_window_rect_lookup_cache", None)
        if lookup_cache is not None and title in lookup_cache:
            rect = lookup_cache[title]
            return None if rect is _WINDOW_UNAVAILABLE else rect

        provider = getattr(self, "_window_rect_provider", None)
        if provider:
            rect = provider(title)
            if lookup_cache is not None:
                lookup_cache[title] = rect if rect else _WINDOW_UNAVAILABLE
            if rect:
                self._target_window_rect = rect
                self._target_window_missing_logged = False
                return rect

            if not self._target_window_missing_logged:
                self.log(f"[warn] target window not found: '{title}'")
                self._target_window_missing_logged = True
            return None

        return getattr(self, "_target_window_rect", None)

    def _capture_for_condition(self, cond: ImageCondition, frame_cache=None):
        region = self._resolve_capture_region(cond)
        if region is _WINDOW_UNAVAILABLE:
            return _WINDOW_UNAVAILABLE, None, None, None

        cache_key = self._condition_frame_cache_key(region)
        if frame_cache is not None and cache_key in frame_cache:
            return region, *frame_cache[cache_key]

        frame, off_x, off_y = self._grab(region)
        if frame_cache is not None:
            frame_cache[cache_key] = (frame, off_x, off_y)
        return region, frame, off_x, off_y

    def _condition_frame_cache_key(self, region):
        return (
            ("monitor", self.scenario.monitor_index)
            if region is None
            else tuple(region)
        )

    @staticmethod
    def _step_uses_matching_row(step):
        return any(action.type == "click_matching_row" for action in step.actions)

    def _matching_row_snapshot_regions(self, step, regions):
        """Include every possible level crop in the step's atomic snapshot."""
        snapshot_regions = list(regions)
        # Template geometry can scale with both the saved reference resolution
        # and the local multi-scale search. These deliberately conservative
        # bounds still capture much less than a whole window for normal rally
        # layouts while ensuring a tight mob-search region cannot exclude the
        # level text immediately below it.
        scale_bounds = (0.25, 4.0)
        retry_offsets = (-16, 24)
        for action in step.actions:
            if (
                action.type != "click_matching_row"
                or (action.min_level is None and action.max_level is None)
                or action.match_condition_index is None
                or not 0 <= action.match_condition_index < len(regions)
            ):
                continue
            reference_region = regions[action.match_condition_index]
            if reference_region is None:
                return snapshot_regions
            left, top, width, height = reference_region
            roi_left, roi_top, roi_width, roi_height = (
                action.level_roi or [-90, -45, 220, 100]
            )
            x_edges = (
                roi_left,
                roi_left + roi_width,
            )
            y_edges = (
                roi_top + retry_offsets[0],
                roi_top + roi_height + retry_offsets[1],
            )
            min_x = math.floor(min(value * scale for value in x_edges for scale in scale_bounds))
            max_x = math.ceil(max(value * scale for value in x_edges for scale in scale_bounds))
            min_y = math.floor(min(value * scale for value in y_edges for scale in scale_bounds))
            max_y = math.ceil(max(value * scale for value in y_edges for scale in scale_bounds))
            expanded_left = left + min_x
            expanded_top = top + min_y
            expanded_right = left + width + max_x
            expanded_bottom = top + height + max_y
            snapshot_regions.append(
                (
                    expanded_left,
                    expanded_top,
                    expanded_right - expanded_left,
                    expanded_bottom - expanded_top,
                )
            )
        return snapshot_regions

    def _matching_row_capture_bounds(self):
        scenario = getattr(self, "scenario", None)
        if scenario is not None and scenario.target_window_title.strip():
            return self._get_target_window_rect()
        try:
            _index, monitor = self._selected_monitor()
        except (AttributeError, RuntimeError):
            return None
        return monitor_rect(monitor)

    @staticmethod
    def _intersect_capture_region(region, bounds):
        if region is None or bounds is None:
            return region
        left = max(region[0], bounds[0])
        top = max(region[1], bounds[1])
        right = min(region[0] + region[2], bounds[0] + bounds[2])
        bottom = min(region[1] + region[3], bounds[1] + bounds[3])
        if right <= left or bottom <= top:
            return region
        return left, top, right - left, bottom - top

    def _prime_matching_row_frame_cache(self, step, frame_cache):
        """Capture one atomic snapshot for every condition in a row action step."""
        if not self._step_uses_matching_row(step):
            return None

        regions = []
        for condition in step.conditions:
            region = self._resolve_capture_region(condition)
            if region is _WINDOW_UNAVAILABLE:
                return None
            regions.append(region)
        if not regions:
            return None

        snapshot_regions = self._matching_row_snapshot_regions(step, regions)
        if any(region is None for region in snapshot_regions):
            capture_region = None
        else:
            left = min(region[0] for region in snapshot_regions)
            top = min(region[1] for region in snapshot_regions)
            right = max(region[0] + region[2] for region in snapshot_regions)
            bottom = max(region[1] + region[3] for region in snapshot_regions)
            capture_region = (left, top, right - left, bottom - top)
            capture_region = self._intersect_capture_region(
                capture_region,
                self._matching_row_capture_bounds(),
            )

        frame, off_x, off_y = self._grab(capture_region)
        snapshot = _CaptureSnapshot(frame, int(off_x), int(off_y))
        frame_cache[_MATCHING_ROW_SNAPSHOT_KEY] = snapshot
        for region in regions:
            cache_key = self._condition_frame_cache_key(region)
            if region is None:
                frame_cache[cache_key] = (frame, snapshot.left, snapshot.top)
                continue
            crop = snapshot.crop(region)
            if crop is not None:
                frame_cache[cache_key] = (crop, int(region[0]), int(region[1]))
        return snapshot

    def _resolve_capture_region(self, cond: ImageCondition):
        window_rect = None
        if self.scenario.target_window_title.strip() or cond.region_mode == "window":
            window_rect = self._get_target_window_rect()
            if not window_rect:
                return _WINDOW_UNAVAILABLE

        selected_monitor_rect = None
        if window_rect is None or cond.region_mode == "monitor":
            _monitor_index, monitor = self._selected_monitor()
            selected_monitor_rect = monitor_rect(monitor)
        return resolve_saved_capture_region(
            cond.region,
            cond.region_mode,
            cond.region_ratio,
            cond.region_window_size,
            window_rect=window_rect,
            monitor_rect=selected_monitor_rect,
        )

    def preview_step(self, step: Step):
        results, matches = [], []
        preview_image = None
        condition_previews = []

        previous_cache = getattr(self, "_window_rect_lookup_cache", None)
        previous_all_matches = getattr(self, "_preview_all_match_indices", None)
        self._window_rect_lookup_cache = {}
        self._preview_all_match_indices = self._condition_indices_needing_all_matches(
            step
        )
        try:
            for i, cond in enumerate(step.conditions):
                self._raise_if_stopped()
                ok, condition_matches, image, capture_box = self._preview_condition(i, cond)
                results.append(ok)
                matches.extend(condition_matches)
                if preview_image is None and image is not None:
                    preview_image = image
                condition_previews.append({
                    "condition_index": i,
                    "ok": ok,
                    "image": image,
                    "capture_box": capture_box,
                    "matches": condition_matches,
                    "template_path": cond.template_path,
                    "negate": cond.negate,
                })
        finally:
            self._window_rect_lookup_cache = previous_cache
            self._preview_all_match_indices = previous_all_matches

        met = True if not results else (any(results) if step.condition_operator == "OR" else all(results))
        return {
            "met": met,
            "matches": matches,
            "image": preview_image,
            "condition_results": results,
            "condition_previews": condition_previews,
        }

    def _preview_condition(self, index: int, cond: ImageCondition):
        region, frame, off_x, off_y = self._capture_for_condition(cond)
        if region is _WINDOW_UNAVAILABLE:
            return False, [], None, None

        image = self._frame_to_image(frame)
        capture_box = None
        if image is not None:
            capture_box = (off_x, off_y, image.width, image.height)
        all_match_indices = getattr(self, "_preview_all_match_indices", None)
        collect_all = True if all_match_indices is None else index in all_match_indices
        ok, matches, image = self._preview_template_condition(
            index,
            cond,
            frame,
            off_x,
            off_y,
            image,
            collect_all=collect_all,
        )
        return ok, matches, image, capture_box

    def _evaluate_condition(self, index: int, cond: ImageCondition, frame_cache, collect_all=True):
        if self._stop_requested():
            return False, []
        region, frame, off_x, off_y = self._capture_for_condition(cond, frame_cache)
        if region is _WINDOW_UNAVAILABLE:
            return False, []

        return self._evaluate_template_condition(index, cond, frame, off_x, off_y, collect_all)

    def _evaluate_template_condition(self, index, cond, frame, off_x, off_y, collect_all):
        if self._stop_requested():
            return False, []
        template = self._load_template(cond.template_path)

        if cond.comparison_template_path:
            return self._evaluate_competing_template_condition(
                index, cond, frame, template, off_x, off_y, collect_all=collect_all
            )

        if collect_all and not cond.negate:
            template_matches = self._find_template_matches_in_frame(
                frame,
                template,
                cond.confidence,
                collect_all=True,
                **self._condition_matching_kwargs(cond),
            )
            found = bool(template_matches)
            ok = found
            return ok, self._template_matches_to_runtime_matches(index, cond, template_matches, off_x, off_y)

        template_matches = self._find_template_matches_in_frame(
            frame,
            template,
            cond.confidence,
            collect_all=False,
            allow_coarse=not cond.negate,
            early_exit_score=(cond.confidence if cond.negate else None),
            **self._condition_matching_kwargs(cond),
        )
        found = bool(template_matches)
        ok = (not found) if cond.negate else found
        if not found or cond.negate:
            return ok, []

        return ok, self._template_matches_to_runtime_matches(index, cond, template_matches, off_x, off_y)

    def _evaluate_competing_template_condition(
        self,
        index,
        cond,
        frame,
        template,
        off_x,
        off_y,
        collect_all=False,
        include_negated_matches=False,
    ):
        rival_template = self._load_template(cond.comparison_template_path)
        margin = max(0.0, float(cond.comparison_margin or 0.0))

        # Runtime and Preview deliberately use the same location-local comparison.
        # A strong rival elsewhere on screen must not disqualify a valid target.
        target_matches = self._find_template_matches_in_frame(
            frame,
            template,
            cond.confidence,
            collect_all=True,
            allow_coarse=not cond.negate,
            **self._condition_matching_kwargs(cond),
        )
        accepted = []
        rival_scores = []
        for target_match in target_matches:
            self._raise_if_stopped()
            rival_match = self._find_best_template_match_near(
                frame, rival_template, target_match, cond
            )
            rival_score = rival_match[4] if rival_match else -1.0
            if target_match[4] >= rival_score + margin:
                accepted.append(target_match)
                rival_scores.append(rival_score)

        found = bool(accepted)
        ok = (not found) if cond.negate else found
        if not found or (cond.negate and not include_negated_matches):
            return ok, []

        if not collect_all:
            best_index = max(
                range(len(accepted)),
                key=lambda item: (accepted[item][4], -abs(accepted[item][5] - 1.0)),
            )
            accepted = [accepted[best_index]]
            rival_scores = [rival_scores[best_index]]

        matches = self._template_matches_to_runtime_matches(
            index, cond, accepted, off_x, off_y
        )
        self._add_competing_match_details(cond, matches, rival_scores)
        return ok, matches

    def _add_competing_match_details(self, cond, matches, rival_scores):
        for match, rival_score in zip(matches, rival_scores):
            score_margin = match["confidence"] - rival_score
            match["comparison_confidence"] = rival_score
            match["score_margin"] = score_margin
            match["label"] += (
                f" beats {os.path.basename(cond.comparison_template_path)} "
                f"{rival_score:.2f} by {score_margin:.2f}"
            )

    def _find_best_template_match_near(self, frame, template, target_match, cond=None):
        self._raise_if_stopped()
        x, y, width, height = target_match[:4]
        matching_kwargs = (
            self._condition_matching_kwargs(
                cond,
                template_path=cond.comparison_template_path,
                explicit_reference_size=getattr(
                    cond,
                    "comparison_template_reference_size",
                    None,
                ),
            )
            if cond is not None
            else {}
        )
        scale_pairs = resolution_scale_pairs(
            matching_kwargs.get("reference_size"),
            matching_kwargs.get("current_size"),
            self.TEMPLATE_SCALE_FACTORS,
            matching_kwargs.get("reference_sizes", ()),
        ) or ((1.0, 1.0),)
        template_height, template_width = template.shape[:2]
        rival_width = max(
            max(1, round(template_width * scale_x))
            for scale_x, _scale_y in scale_pairs
        )
        rival_height = max(
            max(1, round(template_height * scale_y))
            for _scale_x, scale_y in scale_pairs
        )
        search_width = max(width, rival_width)
        search_height = max(height, rival_height)
        padding = max(4, round(max(search_width, search_height) * 0.25))
        frame_height, frame_width = frame.shape[:2]
        left = max(0, x - padding)
        top = max(0, y - padding)
        right = min(frame_width, x + search_width + padding)
        bottom = min(frame_height, y + search_height + padding)
        local_matches = self._find_template_matches_in_frame(
            frame[top:bottom, left:right],
            template,
            confidence=-1.0,
            collect_all=False,
            **matching_kwargs,
        )
        local_match = local_matches[0] if local_matches else None
        if local_match is None:
            return None
        local_x, local_y, match_width, match_height, score, scale = local_match
        return (left + local_x, top + local_y, match_width, match_height, score, scale)

    def _preview_template_condition(
        self,
        index,
        cond,
        frame,
        off_x,
        off_y,
        image,
        collect_all=True,
    ):
        template = self._load_template(cond.template_path)
        if cond.comparison_template_path:
            ok, matches = self._evaluate_competing_template_condition(
                index,
                cond,
                frame,
                template,
                off_x,
                off_y,
                collect_all=collect_all,
                include_negated_matches=True,
            )
            return ok, matches, image
        if cond.negate:
            template_matches = self._find_template_matches_in_frame(
                frame,
                template,
                cond.confidence,
                collect_all=False,
                allow_coarse=False,
                early_exit_score=cond.confidence,
                **self._condition_matching_kwargs(cond),
            )
            found = bool(template_matches)
            matches = (
                self._template_matches_to_runtime_matches(
                    index,
                    cond,
                    template_matches,
                    off_x,
                    off_y,
                )
                if found
                else []
            )
            return not found, matches, image
        if not collect_all:
            ok, matches = self._evaluate_template_condition(
                index,
                cond,
                frame,
                off_x,
                off_y,
                collect_all=False,
            )
            return ok, matches, image
        template_matches = self._find_template_matches_in_frame(
            frame,
            template,
            cond.confidence,
            collect_all=True,
            **self._condition_matching_kwargs(cond),
        )
        found = bool(template_matches)
        ok = (not found) if cond.negate else found
        if not found:
            return ok, [], image

        matches = self._template_matches_to_runtime_matches(index, cond, template_matches, off_x, off_y)
        return ok, matches, image

    def _template_matches_to_runtime_matches(self, index, cond, template_matches, off_x, off_y):
        matches = []
        for template_match in template_matches:
            x, y, w, h, score, scale = template_match
            scale_x = float(getattr(template_match, "scale_x", scale))
            scale_y = float(getattr(template_match, "scale_y", scale))
            box = (off_x + x, off_y + y, off_x + x + w, off_y + y + h)
            image_box = (x, y, x + w, y + h)
            center = (box[0] + w // 2, box[1] + h // 2)
            scale_label = "" if abs(scale - 1.0) < 0.001 else f" x{scale:.2f}"
            matches.append({
                "condition_index": index,
                "type": "template",
                "label": f"{cond.template_path} {score:.2f}{scale_label}",
                "confidence": score,
                "scale": scale,
                "scale_x": scale_x,
                "scale_y": scale_y,
                "angle": float(getattr(template_match, "angle", 0.0)),
                "box": box,
                "image_box": image_box,
                "center": center,
            })
        return matches

    def _find_best_template_match_in_frame(
        self,
        frame,
        template,
        cond=None,
        template_path=None,
        explicit_reference_size=_REFERENCE_UNSET,
    ):
        matches = self._find_template_matches_in_frame(
            frame,
            template,
            confidence=-1.0,
            collect_all=False,
            **(
                self._condition_matching_kwargs(
                    cond,
                    template_path=template_path,
                    explicit_reference_size=explicit_reference_size,
                )
                if cond is not None
                else {}
            ),
        )
        return matches[0] if matches else None

    def _find_template_matches_in_frame(
        self,
        frame,
        template,
        confidence,
        collect_all=True,
        allow_coarse=True,
        match_mode="static_picture",
        use_grayscale=False,
        reference_size=None,
        current_size=None,
        reference_sizes=(),
        early_exit_score=None,
    ):
        self._raise_if_stopped()
        cache = getattr(self, "_prepared_template_cache", None)
        if cache is None:
            cache = {}
            self._prepared_template_cache = cache
        reference_key = tuple(reference_size) if reference_size else None
        reference_sizes_key = tuple(
            tuple(size) for size in (reference_sizes or ())
        )
        current_key = tuple(current_size) if current_size else None
        low_variance_threshold = float(
            getattr(self, "low_variance_threshold", 1.0)
        )
        cache_key = (
            id(template),
            match_mode,
            bool(use_grayscale),
            reference_key,
            reference_sizes_key,
            current_key,
            tuple(self.TEMPLATE_SCALE_FACTORS),
            low_variance_threshold,
        )
        cached = cache.get(cache_key)
        if cached is None or cached[0] is not template:
            if len(cache) >= 32:
                cache.pop(next(iter(cache)))
            variants = prepare_template_variants(
                template,
                scales=self.TEMPLATE_SCALE_FACTORS,
                use_grayscale=use_grayscale,
                match_mode=match_mode,
                reference_size=reference_size,
                current_size=current_size,
                reference_sizes=reference_sizes,
                low_variance_threshold=low_variance_threshold,
                stop_check=self._raise_if_stopped,
            )
            cache[cache_key] = (template, variants)
        else:
            variants = cached[1]
        matches = find_template_matches(
            frame,
            template,
            confidence,
            collect_all=collect_all,
            allow_coarse=allow_coarse,
            match_mode=match_mode,
            use_grayscale=use_grayscale,
            scales=self.TEMPLATE_SCALE_FACTORS,
            variants=variants,
            reference_size=reference_size,
            current_size=current_size,
            reference_sizes=reference_sizes,
            stop_check=self._raise_if_stopped,
            low_variance_threshold=low_variance_threshold,
            early_exit_score=early_exit_score,
            max_matches_per_scale=getattr(self, "max_matches_per_scale", 128),
            max_candidates=getattr(self, "max_multiscale_candidates", 512),
        )
        return [match.legacy_tuple() for match in matches]

    def _template_spatial_deviation(self, template):
        return _spatial_deviation(template)

    def _best_scaled_template_match(self, frame, scaled_template):
        low_variance = _spatial_deviation(scaled_template) < float(
            getattr(self, "low_variance_threshold", 1.0)
        )
        return _best_variant_match(frame, scaled_template, low_variance)

    def _find_best_template_match_coarse(self, frame, template, confidence):
        return self._find_template_matches_in_frame(
            frame, template, confidence, collect_all=False, allow_coarse=True
        )

    def _find_template_matches_at_scale(
        self, frame, template, confidence, scale, collect_all
    ):
        matches = find_template_matches(
            frame,
            template,
            confidence,
            collect_all=collect_all,
            allow_coarse=False,
            match_mode="static_picture",
            scales=(scale,),
            stop_check=self._raise_if_stopped,
            low_variance_threshold=float(
                getattr(self, "low_variance_threshold", 1.0)
            ),
            max_matches_per_scale=getattr(self, "max_matches_per_scale", 128),
            max_candidates=getattr(self, "max_multiscale_candidates", 512),
        )
        return [match.legacy_tuple() for match in matches]

    def _bounded_local_peaks(self, scores, confidence, width, height, scale):
        peaks = _bounded_local_peaks(
            scores,
            confidence,
            width,
            height,
            max(1, int(getattr(self, "max_matches_per_scale", 128))),
        )
        return [
            (x, y, width, height, score, scale)
            for x, y, score in peaks
        ]

    def _scaled_template(self, template, scale):
        cache = getattr(self, "_scaled_template_cache", None)
        if cache is None:
            cache = {}
            self._scaled_template_cache = cache
        return resize_template(template, scale, cache=cache)

    def _box_iou(self, a, b):
        return box_iou(a, b)


    def _frame_to_image(self, frame):
        if isinstance(frame, Image.Image):
            return frame
        if isinstance(frame, np.ndarray):
            return Image.fromarray(frame[:, :, ::-1])
        return None

    def _evaluate_step(self, step: Step, frame_cache=None):
        if self._stop_requested():
            return False, {}, {}
        if not step.conditions:
            return True, {}, {}
        results, points, matches = [], {}, {}
        if frame_cache is None:
            frame_cache = {}
        matching_row_snapshot = self._prime_matching_row_frame_cache(step, frame_cache)
        if matching_row_snapshot is not None:
            # Bind the atomic capture to the step that produced ``matches``.
            # The cycle-level cache is shared by multiple steps, so the
            # snapshot key alone is not enough to prove that it is safe to
            # reuse for this step's first row action.
            frame_cache[_MATCHING_ROW_SNAPSHOT_STEP_KEY] = step
        cached_all_match_indices = getattr(self, "_all_match_indices", None)
        all_match_indices = (
            cached_all_match_indices.get(step.name)
            if cached_all_match_indices is not None
            else None
        )
        if all_match_indices is None:
            all_match_indices = self._condition_indices_needing_all_matches(step)
        for i, cond in enumerate(step.conditions):
            if self._stop_requested():
                return False, points, matches
            ok, condition_matches = self._evaluate_condition(
                i,
                cond,
                frame_cache,
                collect_all=i in all_match_indices,
            )
            results.append(ok)
            matches[i] = condition_matches
            if condition_matches:
                points[i] = condition_matches[0]["center"]
            if step.condition_operator == "AND" and not ok:
                self._maybe_record_matching_row_step_miss(
                    step,
                    i,
                    matches,
                    frame_cache,
                )
                return False, points, matches
        met = any(results) if step.condition_operator == "OR" else all(results)
        return met, points, matches

    def _maybe_record_matching_row_step_miss(
        self,
        step,
        failed_index,
        matches,
        frame_cache,
    ):
        collector = getattr(self, "_diagnostic_collector", None)
        if collector is None or not getattr(self, "diagnostics_enabled", True):
            return
        action = next(
            (
                candidate
                for candidate in step.actions
                if candidate.type == "click_matching_row"
            ),
            None,
        )
        if action is None:
            return
        reference_index = action.match_condition_index
        target_index = action.on_condition_index
        if failed_index not in {reference_index, target_index}:
            return
        diagnostic_matches = dict(matches)
        reason = None
        try:
            if failed_index == reference_index:
                if target_index is None or target_index >= len(step.conditions):
                    return
                target_ok, target_matches = self._evaluate_condition(
                    target_index,
                    step.conditions[target_index],
                    frame_cache,
                    collect_all=True,
                )
                if not target_ok or not target_matches:
                    return
                diagnostic_matches[target_index] = target_matches
                reason = "reference_missing_with_target_present"
            else:
                if not diagnostic_matches.get(reference_index, []):
                    return
                reason = "target_missing_with_reference_present"

            if not collector.should_capture(
                f"row-miss-probe:{step.name}:{reason}",
                min_interval=15.0,
            ):
                return

            cond = step.conditions[failed_index]
            region, frame, off_x, off_y = self._capture_for_condition(
                cond,
                frame_cache,
            )
            best_score = None
            best_box = None
            if region is not _WINDOW_UNAVAILABLE:
                template = self._load_template(cond.template_path)
                best = self._find_best_template_match_in_frame(frame, template, cond)
                if best is not None:
                    x, y, width, height, best_score, _scale = best
                    best_box = (
                        off_x + x,
                        off_y + y,
                        off_x + x + width,
                        off_y + y + height,
                    )
            self._submit_rally_diagnostic(
                f"rally_template_{reason}",
                {
                    "scenario": getattr(getattr(self, "scenario", None), "name", ""),
                    "step": step.name,
                    "decision": reason,
                    "failed_condition_index": failed_index,
                    "failed_template": cond.template_path,
                    "configured_threshold": cond.confidence,
                    "best_near_miss_score": best_score,
                    "best_near_miss_box": best_box,
                    "reference_condition_index": reference_index,
                    "target_condition_index": target_index,
                },
                matches=diagnostic_matches,
                key=f"row-miss-event:{step.name}:{reason}",
                context_snapshot=frame_cache.get(_MATCHING_ROW_SNAPSHOT_KEY),
            )
        except Exception as exc:
            self.log(f"  [diagnostic] matching-row miss capture failed: {exc}")

    def _condition_indices_needing_all_matches(self, step: Step):
        indices = set()
        for action in step.actions:
            if action.type != "click_matching_row":
                continue
            if action.match_condition_index is not None:
                indices.add(action.match_condition_index)
            if action.on_condition_index is not None:
                indices.add(action.on_condition_index)
        return indices

    def _run_action(self, step: Step, action: Action, points: dict, matches: dict):
        if self._stop_requested():
            return False
        if action.type == "click":
            geometry_match = None
            if action.x is not None and action.y is not None:
                x, y = action.x, action.y
            elif action.on_condition_index is not None and action.on_condition_index in points:
                x, y = points[action.on_condition_index]
                condition_matches = matches.get(action.on_condition_index, [])
                geometry_match = condition_matches[0] if condition_matches else None
            elif action.on_condition_index is not None:
                self.log(
                    f"  [skip] '{step.name}' click target condition "
                    f"#{action.on_condition_index} has no match"
                )
                return False
            elif action.x is not None or action.y is not None:
                self.log(f"  [skip] '{step.name}' click action has an incomplete fixed point")
                return False
            elif points:
                condition_index, point = next(iter(points.items()))
                x, y = point
                condition_matches = matches.get(condition_index, [])
                geometry_match = condition_matches[0] if condition_matches else None
            else:
                self.log(f"  [skip] '{step.name}' click action has no target point")
                return False
            scale_x, scale_y = self._match_geometry_scale(geometry_match)
            x += round(action.offset_x * scale_x)
            y += round(action.offset_y * scale_y)
            if self._click_point(x, y, action.button) is False:
                return False
            self.log(f"  click ({x}, {y})")
            return True

        elif action.type == "click_matching_row":
            reuse_context = getattr(self, "_matching_row_reuse_context", None)
            can_reuse_initial_evaluation = (
                reuse_context is not None
                and reuse_context[0] is step
                and reuse_context[1] is action
                and action.match_condition_index is not None
                and action.on_condition_index is not None
            )
            if can_reuse_initial_evaluation:
                assert reuse_context is not None
                # Step evaluation already found every row anchor/target from
                # this exact atomic capture.  Reusing it avoids a duplicate
                # screen capture and duplicate template matching immediately
                # before OCR.  A configured pre-click delay still takes the
                # mandatory fresh capture/re-OCR path below.
                self._matching_row_snapshot = reuse_context[2]
            else:
                refreshed = self._refresh_click_matching_row_matches(step, action)
                if refreshed is None:
                    self.log(f"  [skip] '{step.name}' conditions changed before row click")
                    self._retry_current_step = True
                    return False
                points, matches = refreshed
            selections, had_unreadable_level = self._find_matching_row_selections(
                action,
                matches,
                apply_level_filter=True,
            )
            if not selections:
                decision = (
                    "level_unreadable"
                    if had_unreadable_level
                    else "no_eligible_row"
                )
                self._record_matching_row_diagnostic(
                    step,
                    action,
                    selections,
                    matches,
                    decision,
                    min_interval=2.0,
                )
                if had_unreadable_level:
                    self.log(
                        f"  [retry] '{step.name}' level unreadable; "
                        "keeping the rally page open"
                    )
                    self._retry_current_step = True
                    return False
                self.log(f"  [skip] '{step.name}' no valid matching row target")
                return self._run_no_match_fallback(step, action, points)

            pre_click_delay = max(0.0, float(getattr(action, "pre_click_delay", 0.0)))
            diagnostic_elapsed = 0.0
            if pre_click_delay > 0.0:
                # Evidence work is absorbed by the user's configured delay.
                diagnostic_started = time.monotonic()
                self._record_matching_row_diagnostic(
                    step,
                    action,
                    selections,
                    matches,
                    "eligible_before_delay",
                )
                diagnostic_elapsed = time.monotonic() - diagnostic_started

            delayed = False
            if pre_click_delay > 0.0:
                delayed = True
                self.log(
                    f"  wait {pre_click_delay:g}s after eligible level check"
                )
                remaining_delay = max(0.0, pre_click_delay - diagnostic_elapsed)
                if self._sleep_until_stop(remaining_delay):
                    return True
                refreshed = self._refresh_click_matching_row_matches(step, action)
                if refreshed is None:
                    self.log(
                        f"  [skip] '{step.name}' conditions changed during pre-click delay"
                    )
                    self._retry_current_step = True
                    return True
                _points, refreshed_matches = refreshed
                original_selections = selections
                selections = self._revalidate_row_selections(
                    action,
                    selections,
                    refreshed_matches,
                )
                if not selections:
                    self._record_matching_row_diagnostic(
                        step,
                        action,
                        original_selections,
                        refreshed_matches,
                        "row_changed_during_delay",
                        min_interval=2.0,
                    )
                    self.log(
                        f"  [skip] '{step.name}' selected row changed during pre-click delay"
                    )
                    self._retry_current_step = True
                    return True

            clicked = False
            for selection in selections:
                if self._stop_requested():
                    return clicked or delayed
                target = selection["target"]
                x, y = target["center"]
                scale_x, scale_y = self._match_geometry_scale(target)
                x += round(action.offset_x * scale_x)
                y += round(action.offset_y * scale_y)
                if self._click_point(x, y, action.button) is False:
                    return clicked or delayed
                clicked = True
                self._pending_rally_level = selection.get("level")
                self.log(f"  click matching row ({x}, {y})")
            if clicked and pre_click_delay <= 0.0:
                # The evidence uses the already-frozen atomic snapshot, so it
                # remains accurate after the click while staying off the
                # time-critical selection-to-click path.
                self._record_matching_row_diagnostic(
                    step,
                    action,
                    selections,
                    matches,
                    "eligible_before_delay",
                )
            return clicked or delayed

        elif action.type == "select_rally_team":
            return self._run_select_rally_team_action(action, points, matches)

        elif action.type == "key":
            if action.hold > 0:
                try:
                    if self._stop_requested():
                        return False
                    keyboard.press(action.key)
                    stopped = self._sleep_until_stop(action.hold)
                finally:
                    keyboard.release(action.key)
                if stopped:
                    return True
            else:
                if self._stop_requested():
                    return False
                keyboard.send(action.key)
            self.log(f"  key '{action.key}'")
            return True

        elif action.type == "wait":
            stopped = self._sleep_until_stop(action.seconds)
            if stopped:
                return False
            self.log(f"  wait {action.seconds}s")
            return float(action.seconds) > 0.0

        elif action.type == "set_step":
            if self._stop_requested():
                return False
            step_lookup = getattr(self, "_step_lookup", {})
            scenario_step = step_lookup.get(action.step_name)
            if scenario_step is not None:
                scenario_step.enabled = action.set_enabled
                state = "enabled" if action.set_enabled else "disabled"
                self.log(f"  step '{action.step_name}' -> {state}")
        return False

    @staticmethod
    def _scaled_relative_region(anchor, region, scale_x, scale_y):
        anchor_x, anchor_y = anchor
        left, top, width, height = region
        return (
            round(anchor_x + left * scale_x),
            round(anchor_y + top * scale_y),
            max(1, round(width * scale_x)),
            max(1, round(height * scale_y)),
        )

    def _run_select_rally_team_action(self, action, points, matches):
        level = getattr(self, "_pending_rally_level", None)
        if level is None:
            self.log("  [skip] no carried rally level is available for team selection")
            self._retry_current_step = True
            return False

        anchor_index = action.on_condition_index
        anchor_matches = matches.get(anchor_index, []) if anchor_index is not None else []
        anchor_match = anchor_matches[0] if anchor_matches else None
        anchor = points.get(anchor_index) if anchor_index is not None else None
        if anchor is None or anchor_match is None:
            self.log("  [skip] rally team selector anchor is no longer available")
            self._retry_current_step = True
            return False

        scale_x, scale_y = self._match_geometry_scale(anchor_match)
        candidates = []
        # Team 3 has priority for lower mobs. Team 1 remains an immediate
        # fallback and is the only eligible team above Team 3's range.
        for team_number in (3, 1):
            maximum = getattr(action, f"team{team_number}_max_level")
            if maximum is not None and level > maximum:
                continue
            idle_region = getattr(action, f"team{team_number}_idle_region")
            click_offset = getattr(action, f"team{team_number}_click_offset")
            specific_template_path = getattr(
                action,
                f"team{team_number}_idle_template_path",
                "",
            )
            if idle_region is None or click_offset is None:
                continue
            candidates.append(
                {
                    "team": team_number,
                    "region": self._scaled_relative_region(
                        anchor,
                        idle_region,
                        scale_x,
                        scale_y,
                    ),
                    "click": (
                        round(anchor[0] + click_offset[0] * scale_x),
                        round(anchor[1] + click_offset[1] * scale_y),
                    ),
                    "max_level": maximum,
                    "template_path": (
                        specific_template_path
                        or action.team_idle_template_path
                    ),
                }
            )

        if not candidates:
            self.log(f"  [skip] no configured rally team accepts mob level {level}")
            self._retry_current_step = True
            return False

        left = min(candidate["region"][0] for candidate in candidates)
        top = min(candidate["region"][1] for candidate in candidates)
        right = max(
            candidate["region"][0] + candidate["region"][2]
            for candidate in candidates
        )
        bottom = max(
            candidate["region"][1] + candidate["region"][3]
            for candidate in candidates
        )
        capture_region = (left, top, right - left, bottom - top)
        window_rect = self._get_target_window_rect()
        if window_rect:
            capture_region = self._intersect_capture_region(capture_region, window_rect)
        frame, off_x, off_y = self._grab(capture_region)
        snapshot = _CaptureSnapshot(frame, int(off_x), int(off_y))

        template_scale = math.sqrt(scale_x * scale_y)
        candidate_matches = {}
        selected = None
        for index, candidate in enumerate(candidates):
            idle_template = self._load_template(candidate["template_path"])
            scaled_template = resize_template(
                idle_template,
                template_scale,
                cache=self._scaled_template_cache,
            )
            region = candidate["region"]
            crop = snapshot.crop(region)
            score = -1.0
            if crop is not None:
                score, _location = self._best_scaled_template_match(
                    crop,
                    scaled_template,
                )
            candidate["score"] = float(score)
            candidate_matches[index] = [
                {
                    "box": (
                        region[0],
                        region[1],
                        region[0] + region[2],
                        region[1] + region[3],
                    ),
                    "center": (
                        region[0] + region[2] // 2,
                        region[1] + region[3] // 2,
                    ),
                    "score": float(score),
                    "scale": template_scale,
                }
            ]
            if score >= action.team_idle_confidence:
                selected = candidate
                break

        evaluated_candidates = [
            candidate for candidate in candidates if "score" in candidate
        ]
        score_text = ", ".join(
            f"Team {candidate['team']}={candidate['score']:.2f}"
            for candidate in evaluated_candidates
        )
        if selected is None:
            if self._should_log_perf(("team:no-eligible", level)):
                self.log(
                    f"  [abort] no eligible idle team for mob level {level} "
                    f"({score_text})"
                )
                self._submit_rally_diagnostic(
                    "rally_team_no_eligible_idle_team",
                    {
                        "scenario": self.scenario.name,
                        "decision": "no_eligible_idle_team",
                        "level": level,
                        "idle_threshold": action.team_idle_confidence,
                        "candidates": candidates,
                    },
                    matches=candidate_matches,
                    key=f"team:no-eligible:{level}",
                    context_snapshot=snapshot,
                )
            recovery_x = anchor[0]
            recovery_y = round(anchor[1] - 400 * scale_y)
            recovery_clicked = bool(
                self._click_point(recovery_x, recovery_y, action.button)
            )
            if recovery_clicked:
                self.log(
                    "  [recovery] dismissed rally team selector at "
                    f"({recovery_x}, {recovery_y})"
                )
            else:
                self.log(
                    "  [recovery] could not dismiss rally team selector; "
                    "continuing state cleanup"
                )
            self._pending_rally_level = None
            self._pending_rally_team_availability = None
            self._retry_current_step = False
            self._cleanup_after_abort = True
            self._abort_current_step = True
            return recovery_clicked

        click_x, click_y = selected["click"]
        if self._click_point(click_x, click_y, action.button) is False:
            self._retry_current_step = True
            return False
        preferred_team_evaluated = any(
            candidate["team"] == 3 for candidate in evaluated_candidates
        )
        if selected["team"] == 1 and preferred_team_evaluated:
            self._submit_rally_diagnostic(
                "rally_team_preferred_fallback",
                {
                    "scenario": self.scenario.name,
                    "decision": "preferred_team_fallback",
                    "selected_team": 1,
                    "level": level,
                    "idle_threshold": action.team_idle_confidence,
                    "candidates": evaluated_candidates,
                },
                matches=candidate_matches,
                key=f"team:preferred-fallback:{level}",
                context_snapshot=snapshot,
            )
        self._pending_rally_level = None
        self.log(
            f"  select idle Team {selected['team']} for mob level {level} "
            f"({score_text})"
        )
        return True

    def _run_no_match_fallback(self, step: Step, action: Action, points: dict):
        if self._stop_requested():
            return False
        if action.no_match_condition_index is None and not action.no_match_disable_steps:
            return False

        clicked = False
        if action.no_match_condition_index is not None:
            point = points.get(action.no_match_condition_index)
            if point is None:
                self.log(
                    f"  [skip] '{step.name}' no-match fallback condition "
                    f"#{action.no_match_condition_index} has no target point"
                )
            else:
                x, y = point
                if self._click_point(x, y, action.button) is False:
                    return False
                clicked = True
                self.log(f"  [no-match] click condition #{action.no_match_condition_index} ({x}, {y})")

        for step_name in action.no_match_disable_steps:
            if self._stop_requested():
                return clicked
            step_lookup = getattr(self, "_step_lookup", {})
            scenario_step = step_lookup.get(step_name)
            if scenario_step is not None:
                scenario_step.enabled = False
                self.log(f"  [no-match] step '{step_name}' -> disabled")
        return clicked

    def _refresh_click_matching_row_matches(self, step: Step, action: Action):
        if self._stop_requested():
            return None
        if action.match_condition_index is None or action.on_condition_index is None:
            return None
        frame_cache = {}
        self._matching_row_snapshot = None
        self._window_rect_lookup_cache = {}
        evaluate_uses_frame_cache = getattr(self, "_evaluate_uses_frame_cache", None)
        if evaluate_uses_frame_cache is None:
            evaluate_uses_frame_cache = self._evaluate_step_supports_frame_cache(self._evaluate_step)
        if evaluate_uses_frame_cache:
            met, points, matches = self._evaluate_step(step, frame_cache=frame_cache)
        else:
            met, points, matches = self._evaluate_step(step)
        self._matching_row_snapshot = frame_cache.get(_MATCHING_ROW_SNAPSHOT_KEY)
        if not met:
            return None
        return points, matches

    def _click_point(self, x, y, button):
        if self._stop_requested():
            return False
        if not self._point_is_on_a_monitor(x, y):
            self.log(f"  [skip] click point ({x}, {y}) is outside every monitor")
            return False
        move_duration = getattr(self, "click_move_duration", 0.0)
        if move_duration:
            pyautogui.moveTo(x, y, duration=move_duration)
            if self._stop_requested():
                return False
            pyautogui.click(button=button)
        else:
            if self._stop_requested():
                return False
            pyautogui.click(x=x, y=y, button=button)
        return True

    def _point_is_on_a_monitor(self, x, y):
        sct = getattr(self, "sct", None)
        if sct is None:
            return True
        try:
            all_monitors = sct.monitors
        except (AttributeError, OSError, TypeError):
            return True
        monitors = all_monitors[1:] or all_monitors[:1]
        return any(
            monitor["left"] <= x < monitor["left"] + monitor["width"]
            and monitor["top"] <= y < monitor["top"] + monitor["height"]
            for monitor in monitors
        )

    def _cycle(self):
        now = time.monotonic()
        cycle_start = time.perf_counter()
        fired_any = False
        steps = self._refresh_step_caches()
        frame_cache = {}
        evaluate_step = self._evaluate_step
        if hasattr(self, "_evaluate_uses_frame_cache"):
            evaluate_uses_frame_cache = self._evaluate_uses_frame_cache
        else:
            evaluate_uses_frame_cache = self._evaluate_step_supports_frame_cache(evaluate_step)
        self._window_rect_lookup_cache = {}
        try:
            for step in steps:
                if self._stop_requested():
                    return fired_any
                if not step.enabled:
                    continue
                if now - self._last_fired.get(step.name, 0.0) < step.cooldown:
                    continue

                eval_start = time.perf_counter()
                if evaluate_uses_frame_cache:
                    met, points, matches = evaluate_step(step, frame_cache=frame_cache)
                else:
                    met, points, matches = evaluate_step(step)
                eval_elapsed = time.perf_counter() - eval_start
                if (
                    eval_elapsed >= getattr(self, "slow_step_threshold", 0.15)
                    and self._should_log_perf(("step", step.name), now)
                ):
                    self.log(
                        f"[perf] step '{step.name}' check took {eval_elapsed:.3f}s "
                        f"({len(step.conditions)} condition(s))"
                    )
                if not met:
                    continue   # condition not on screen right now -- skip this step, check the next one
                if self._stop_requested():
                    return fired_any

                if not self._prepare_rally_team_availability_for_entry(step):
                    continue

                self.log(f"[fire] {step.name}")
                fired_any = True
                retry_step = False
                self._abort_current_step = False
                self._cleanup_after_abort = False
                for action_index, action in enumerate(step.actions):
                    if self._stop_requested():
                        return fired_any
                    self._retry_current_step = False
                    snapshot = frame_cache.get(_MATCHING_ROW_SNAPSHOT_KEY)
                    can_reuse_matching_row_evaluation = (
                        action.type == "click_matching_row"
                        and snapshot is not None
                        and frame_cache.get(_MATCHING_ROW_SNAPSHOT_STEP_KEY) is step
                    )
                    self._matching_row_reuse_context = (
                        (step, action, snapshot)
                        if can_reuse_matching_row_evaluation
                        else None
                    )
                    try:
                        invalidates_frame = self._run_action(step, action, points, matches)
                    finally:
                        self._matching_row_reuse_context = None
                    if invalidates_frame:
                        frame_cache.clear()
                        self._window_rect_lookup_cache = {}
                    if getattr(self, "_abort_current_step", False):
                        if getattr(self, "_cleanup_after_abort", False):
                            for cleanup_action in step.actions[action_index + 1 :]:
                                if cleanup_action.type != "set_step":
                                    continue
                                if self._stop_requested():
                                    return fired_any
                                self._retry_current_step = False
                                self._run_action(step, cleanup_action, points, matches)
                            self._cleanup_after_abort = False
                        break
                    if getattr(self, "_retry_current_step", False):
                        retry_step = True
                        break
                    if self._stop_requested():
                        return fired_any

                if not retry_step:
                    self._last_fired[step.name] = now
                    if not step.repeatable:
                        step.enabled = False
            cycle_elapsed = time.perf_counter() - cycle_start
            if (
                cycle_elapsed >= getattr(self, "slow_cycle_threshold", 0.35)
                and self._should_log_perf(("cycle",), now)
            ):
                self.log(f"[perf] cycle took {cycle_elapsed:.3f}s")
            return fired_any
        except _StopRequested:
            return fired_any
        finally:
            self._matching_row_reuse_context = None
            self._window_rect_lookup_cache = None
