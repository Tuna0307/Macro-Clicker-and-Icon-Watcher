"""
Runtime engine. Evaluates a Scenario's steps every polling cycle and
executes their actions -- the same model as game_macro.py, but driven
by a Scenario object and running on a background thread (with a log
callback instead of print) so it plays nicely with a GUI.
"""
import os
import re
import inspect
import threading
import time
from typing import Callable, Optional

import cv2
import numpy as np
import mss
import pyautogui
import keyboard
from PIL import Image

from level_ocr import LevelOcrReader
from models import Scenario, Step, ImageCondition, Action, project_path, validate_scenario
from runtime_paths import LEVEL_DEBUG_DIR
from window_locator import find_window_rect, resolve_window_region


_WINDOW_UNAVAILABLE = object()


class _StopRequested(Exception):
    """Internal control-flow signal used to leave expensive engine work quietly."""


class MacroEngine:
    TEMPLATE_SCALE_FACTORS = (1.0, 0.95, 1.05, 0.9, 1.1, 0.85, 1.15, 0.8, 1.2)

    def __init__(self, scenario: Scenario, log: Optional[Callable[[str], None]] = None):
        self.scenario = Scenario.from_dict(scenario.to_dict())
        self.log = log or (lambda msg: None)
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._last_fired = {s.name: 0.0 for s in self.scenario.steps}
        self._template_cache = {}
        self._scaled_template_cache = {}
        self._digit_template_cache = {}
        self._missing_digit_template_warnings = set()
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
        self._all_match_indices = {}
        self._step_lookup = {}
        self._step_names_snapshot = ()
        self._evaluate_uses_frame_cache = self._evaluate_step_supports_frame_cache(self._evaluate_step)

    # ---- public control ----
    @property
    def is_running(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        if self.is_running:
            return
        validate_scenario(self.scenario, require_files=True)
        if getattr(self, "_sct_closed", False):
            self.sct = mss.MSS()
            self._sct_closed = False
        self._stop_event.clear()
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
        self._thread.start()
        if not uses_level_ocr:
            self.log(f"Scenario '{self.scenario.name}' started. Kill switch: {self.scenario.kill_switch.upper()}")

    def stop(self):
        running = self.is_running
        was_active = running or self._hotkey_handle is not None or getattr(self, "_ever_started", False)
        self._stop_event.set()
        self._remove_hotkey()
        thread = getattr(self, "_thread", None)
        if running and thread is not None and threading.current_thread() is not thread:
            thread.join(timeout=2.0)
            running = thread.is_alive()
        if not running:
            self._close_capture()
        if was_active:
            self.log("Scenario stopped.")
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
            self.log("[ocr] continuing with digit-template fallback")
        self.log(
            f"Scenario '{self.scenario.name}' started. "
            f"Kill switch: {self.scenario.kill_switch.upper()}"
        )
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

    def _grab(self, region=None):
        self._raise_if_stopped()
        if region:
            left, top, width, height = region
            monitor = {"left": left, "top": top, "width": width, "height": height}
        else:
            monitors = self.sct.monitors
            requested = self.scenario.monitor_index
            if 0 <= requested < len(monitors):
                monitor_index = requested
            else:
                monitor_index = 1 if len(monitors) > 1 else 0
                if getattr(self, "_monitor_index_warning_logged", None) != requested:
                    self.log(
                        f"[warn] monitor #{requested} is unavailable; "
                        f"using monitor #{monitor_index}"
                    )
                    self._monitor_index_warning_logged = requested
            monitor = monitors[monitor_index]
        attempts = max(1, int(getattr(self, "capture_retry_attempts", 3)))
        backoff = max(0.0, float(getattr(self, "capture_retry_backoff", 0.05)))
        for attempt in range(attempts):
            self._raise_if_stopped()
            try:
                raw = self.sct.grab(monitor)
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
        frame = np.array(raw)[:, :, :3]
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

        cache_key = ("monitor", self.scenario.monitor_index) if region is None else tuple(region)
        if frame_cache is not None and cache_key in frame_cache:
            return region, *frame_cache[cache_key]

        frame, off_x, off_y = self._grab(region)
        if frame_cache is not None:
            frame_cache[cache_key] = (frame, off_x, off_y)
        return region, frame, off_x, off_y

    def _resolve_capture_region(self, cond: ImageCondition):
        window_rect = None
        if self.scenario.target_window_title.strip() or cond.region_mode == "window":
            window_rect = self._get_target_window_rect()
            if not window_rect:
                return _WINDOW_UNAVAILABLE

        if cond.region:
            if cond.region_mode == "window":
                assert window_rect is not None
                return resolve_window_region(
                    cond.region,
                    window_rect,
                    cond.region_ratio,
                    cond.region_window_size,
                )
            return tuple(cond.region)

        if self.scenario.target_window_title.strip():
            return window_rect

        return None

    def preview_step(self, step: Step):
        results, matches = [], []
        preview_image = None
        condition_previews = []

        previous_cache = getattr(self, "_window_rect_lookup_cache", None)
        self._window_rect_lookup_cache = {}
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
        ok, matches, image = self._preview_template_condition(index, cond, frame, off_x, off_y, image)
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
            template_matches = self._find_template_matches_in_frame(frame, template, cond.confidence, collect_all=True)
            found = bool(template_matches)
            ok = found
            return ok, self._template_matches_to_runtime_matches(index, cond, template_matches, off_x, off_y)

        template_matches = self._find_template_matches_in_frame(
            frame,
            template,
            cond.confidence,
            collect_all=False,
            allow_coarse=not cond.negate,
        )
        found = bool(template_matches)
        ok = (not found) if cond.negate else found
        if not found or cond.negate:
            return ok, []

        return ok, self._template_matches_to_runtime_matches(index, cond, template_matches, off_x, off_y)

    def _evaluate_competing_template_condition(
        self, index, cond, frame, template, off_x, off_y, collect_all=False
    ):
        rival_template = self._load_template(cond.comparison_template_path)
        margin = max(0.0, float(cond.comparison_margin or 0.0))

        # Runtime and Preview deliberately use the same location-local comparison.
        # A strong rival elsewhere on screen must not disqualify a valid target.
        target_matches = self._find_template_matches_in_frame(
            frame, template, cond.confidence, collect_all=True
        )
        accepted = []
        rival_scores = []
        for target_match in target_matches:
            self._raise_if_stopped()
            rival_match = self._find_best_template_match_near(
                frame, rival_template, target_match
            )
            rival_score = rival_match[4] if rival_match else -1.0
            if target_match[4] >= rival_score + margin:
                accepted.append(target_match)
                rival_scores.append(rival_score)

        found = bool(accepted)
        ok = (not found) if cond.negate else found
        if not found or cond.negate:
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

    def _find_best_template_match_near(self, frame, template, target_match):
        self._raise_if_stopped()
        x, y, width, height = target_match[:4]
        padding = max(4, round(max(width, height) * 0.25))
        frame_height, frame_width = frame.shape[:2]
        left = max(0, x - padding)
        top = max(0, y - padding)
        right = min(frame_width, x + width + padding)
        bottom = min(frame_height, y + height + padding)
        local_match = self._find_best_template_match_in_frame(
            frame[top:bottom, left:right], template
        )
        if local_match is None:
            return None
        local_x, local_y, match_width, match_height, score, scale = local_match
        return (left + local_x, top + local_y, match_width, match_height, score, scale)

    def _preview_template_condition(self, index, cond, frame, off_x, off_y, image):
        template = self._load_template(cond.template_path)
        if cond.comparison_template_path:
            ok, matches = self._evaluate_competing_template_condition(
                index, cond, frame, template, off_x, off_y, collect_all=True
            )
            return ok, matches, image
        template_matches = self._find_template_matches_in_frame(frame, template, cond.confidence, collect_all=True)
        found = bool(template_matches)
        ok = (not found) if cond.negate else found
        if not found:
            return ok, [], image

        matches = self._template_matches_to_runtime_matches(index, cond, template_matches, off_x, off_y)
        return ok, matches, image

    def _template_matches_to_runtime_matches(self, index, cond, template_matches, off_x, off_y):
        matches = []
        for x, y, w, h, score, scale in template_matches:
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
                "box": box,
                "image_box": image_box,
                "center": center,
            })
        return matches

    def _find_best_template_match_in_frame(self, frame, template):
        matches = self._find_template_matches_in_frame(
            frame, template, confidence=-1.0, collect_all=False
        )
        return matches[0] if matches else None

    def _find_template_matches_in_frame(
        self, frame, template, confidence, collect_all=True, allow_coarse=True
    ):
        if (
            not collect_all
            and allow_coarse
            and frame.shape[0] * frame.shape[1] >= 500_000
            and min(template.shape[:2]) >= 20
        ):
            return self._find_best_template_match_coarse(frame, template, confidence)
        candidates = []
        for scale in self.TEMPLATE_SCALE_FACTORS:
            self._raise_if_stopped()
            scaled_matches = self._find_template_matches_at_scale(frame, template, confidence, scale, collect_all)
            candidates.extend(scaled_matches)

        self._raise_if_stopped()
        if not collect_all:
            if not candidates:
                return []
            best = max(
                candidates,
                key=lambda item: (item[4], -abs(item[5] - 1.0)),
            )
            return [best]

        candidates.sort(key=lambda item: item[4], reverse=True)
        candidate_limit = max(1, int(getattr(self, "max_multiscale_candidates", 512)))
        if len(candidates) > candidate_limit:
            candidates = candidates[:candidate_limit]
        kept = []
        for x, y, w, h, score, scale in candidates:
            self._raise_if_stopped()
            box = (x, y, x + w, y + h)
            if any(self._box_iou(box, (kx, ky, kx + kw, ky + kh)) > 0.3 for kx, ky, kw, kh, _, _ in kept):
                continue
            kept.append((x, y, w, h, score, scale))

        kept.sort(key=lambda item: (item[1], item[0]))
        return kept

    def _template_spatial_deviation(self, template):
        if template.ndim == 3:
            return float(
                np.max(np.std(template.astype(np.float32), axis=(0, 1)))
            )
        return float(np.std(template))

    def _best_scaled_template_match(self, frame, scaled_template):
        height, width = scaled_template.shape[:2]
        if height > frame.shape[0] or width > frame.shape[1]:
            return -1.0, None
        low_variance = self._template_spatial_deviation(scaled_template) < float(
            getattr(self, "low_variance_threshold", 1.0)
        )
        if low_variance:
            raw_result = cv2.matchTemplate(frame, scaled_template, cv2.TM_SQDIFF)
            channels = scaled_template.shape[2] if scaled_template.ndim == 3 else 1
            worst = float(width * height * channels * (255 ** 2))
            result = np.clip(1.0 - (raw_result / worst), 0.0, 1.0)
        else:
            raw_result = cv2.matchTemplate(
                frame, scaled_template, cv2.TM_CCOEFF_NORMED
            )
            result = np.nan_to_num(
                raw_result, nan=-1.0, posinf=1.0, neginf=-1.0
            )
        self._raise_if_stopped()
        _, max_val, _, max_loc = cv2.minMaxLoc(result)
        if low_variance and result.size > 1:
            if float(max_val) - float(result.min()) <= 1e-6:
                return -1.0, None
        return float(max_val), max_loc

    def _find_best_template_match_coarse(self, frame, template, confidence):
        """Accelerate large positive searches, then verify in the original pixels."""
        self._raise_if_stopped()
        factor = 0.5 if min(template.shape[:2]) >= 30 else 0.67
        small_frame = cv2.resize(
            frame, None, fx=factor, fy=factor, interpolation=cv2.INTER_AREA
        )
        coarse_records = []
        for scale in self.TEMPLATE_SCALE_FACTORS:
            self._raise_if_stopped()
            scaled = self._scaled_template(template, scale)
            height, width = scaled.shape[:2]
            small_scaled = cv2.resize(
                scaled,
                (max(1, round(width * factor)), max(1, round(height * factor))),
                interpolation=cv2.INTER_AREA,
            )
            score, loc = self._best_scaled_template_match(small_frame, small_scaled)
            if loc is not None:
                coarse_records.append((score, loc, scale))

        best = None
        for _coarse_score, coarse_loc, scale in sorted(
            coarse_records, key=lambda item: item[0], reverse=True
        ):
            self._raise_if_stopped()
            scaled = self._scaled_template(template, scale)
            height, width = scaled.shape[:2]
            expected_x = round(coarse_loc[0] / factor)
            expected_y = round(coarse_loc[1] / factor)
            margin = max(8, round(max(width, height) * 0.35))
            left = max(0, expected_x - margin)
            top = max(0, expected_y - margin)
            right = min(frame.shape[1], expected_x + width + margin)
            bottom = min(frame.shape[0], expected_y + height + margin)
            local = frame[top:bottom, left:right]
            score, loc = self._best_scaled_template_match(local, scaled)
            if loc is None:
                continue
            candidate = (
                left + loc[0],
                top + loc[1],
                width,
                height,
                score,
                scale,
            )
            if best is None or (candidate[4], -abs(scale - 1.0)) > (
                best[4],
                -abs(best[5] - 1.0),
            ):
                best = candidate
        if best is None or best[4] < confidence:
            return []
        return [best]

    def _find_template_matches_at_scale(self, frame, template, confidence, scale, collect_all):
        self._raise_if_stopped()
        scaled_template = self._scaled_template(template, scale)

        h, w = scaled_template.shape[:2]
        frame_h, frame_w = frame.shape[:2]
        if h > frame_h or w > frame_w:
            return []

        spatial_deviation = self._template_spatial_deviation(scaled_template)
        low_variance = spatial_deviation < float(
            getattr(self, "low_variance_threshold", 1.0)
        )
        if low_variance:
            raw_result = cv2.matchTemplate(frame, scaled_template, cv2.TM_SQDIFF)
            channels = scaled_template.shape[2] if scaled_template.ndim == 3 else 1
            worst = float(w * h * channels * (255 ** 2))
            result = np.clip(1.0 - (raw_result / worst), 0.0, 1.0)
        else:
            raw_result = cv2.matchTemplate(frame, scaled_template, cv2.TM_CCOEFF_NORMED)
            result = np.nan_to_num(raw_result, nan=-1.0, posinf=1.0, neginf=-1.0)
        self._raise_if_stopped()

        _, max_val, _, max_loc = cv2.minMaxLoc(result)
        if max_val < confidence:
            return []

        # A flat response has no defensible location. This is common with a
        # solid template on a solid background and used to produce thousands
        # of equally perfect, unsafe click targets.
        if low_variance and result.size > 1:
            min_val = float(result.min())
            if float(max_val) - min_val <= 1e-6:
                return []

        if not collect_all:
            return [(max_loc[0], max_loc[1], w, h, float(max_val), scale)]

        candidates = self._bounded_local_peaks(result, confidence, w, h, scale)

        kept = []
        for x, y, width, height, score, match_scale in candidates:
            self._raise_if_stopped()
            box = (x, y, x + width, y + height)
            if any(self._box_iou(box, (kx, ky, kx + kw, ky + kh)) > 0.3 for kx, ky, kw, kh, _, _ in kept):
                continue
            kept.append((x, y, width, height, score, match_scale))

        kept.sort(key=lambda item: (item[1], item[0]))
        return kept

    def _bounded_local_peaks(self, scores, confidence, width, height, scale):
        """Return a bounded set of spatial peaks without sorting every hit."""
        self._raise_if_stopped()
        kernel_width = max(1, min(width, scores.shape[1]))
        kernel_height = max(1, min(height, scores.shape[0]))
        kernel = np.ones((kernel_height, kernel_width), dtype=np.uint8)
        local_max = cv2.dilate(scores, kernel)
        peak_mask = (scores >= confidence) & (scores >= local_max - 1e-6)
        flat_indices = np.flatnonzero(peak_mask)
        if flat_indices.size == 0:
            return []

        limit = max(1, int(getattr(self, "max_matches_per_scale", 128)))
        flat_scores = scores.reshape(-1)
        if flat_indices.size > limit:
            candidate_scores = flat_scores[flat_indices]
            chosen = np.argpartition(candidate_scores, -limit)[-limit:]
            flat_indices = flat_indices[chosen]

        result_width = scores.shape[1]
        candidates = []
        for flat_index in flat_indices:
            self._raise_if_stopped()
            y, x = divmod(int(flat_index), result_width)
            candidates.append(
                (x, y, width, height, float(flat_scores[flat_index]), scale)
            )
        candidates.sort(key=lambda item: (-item[4], item[1], item[0]))
        return candidates

    def _scaled_template(self, template, scale):
        if abs(scale - 1.0) < 0.001:
            return template
        cache = getattr(self, "_scaled_template_cache", None)
        if cache is None:
            cache = {}
            self._scaled_template_cache = cache
        key = (id(template), scale)
        cached = cache.get(key)
        if cached is not None and cached[0] is template:
            return cached[1]
        template_h, template_w = template.shape[:2]
        width = max(1, round(template_w * scale))
        height = max(1, round(template_h * scale))
        scaled = cv2.resize(template, (width, height), interpolation=cv2.INTER_LINEAR)
        cache[key] = (template, scaled)
        return scaled

    def _box_iou(self, a, b):
        ax1, ay1, ax2, ay2 = a
        bx1, by1, bx2, by2 = b
        ix1, iy1 = max(ax1, bx1), max(ay1, by1)
        ix2, iy2 = min(ax2, bx2), min(ay2, by2)
        iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
        intersection = iw * ih
        if intersection == 0:
            return 0.0
        area_a = (ax2 - ax1) * (ay2 - ay1)
        area_b = (bx2 - bx1) * (by2 - by1)
        return intersection / float(area_a + area_b - intersection)

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
                return False, points, matches
        met = any(results) if step.condition_operator == "OR" else all(results)
        return met, points, matches

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
            if action.x is not None and action.y is not None:
                x, y = action.x, action.y
            elif action.on_condition_index is not None and action.on_condition_index in points:
                x, y = points[action.on_condition_index]
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
                x, y = next(iter(points.values()))
            else:
                self.log(f"  [skip] '{step.name}' click action has no target point")
                return False
            x += action.offset_x
            y += action.offset_y
            if self._click_point(x, y, action.button) is False:
                return False
            self.log(f"  click ({x}, {y})")
            return True

        elif action.type == "click_matching_row":
            refreshed = self._refresh_click_matching_row_matches(step, action)
            if refreshed is None:
                self.log(f"  [skip] '{step.name}' conditions changed before row click")
                return False
            points, matches = refreshed
            targets = self._find_matching_row_targets(action, matches)
            if not targets:
                self.log(f"  [skip] '{step.name}' no valid matching row target")
                return self._run_no_match_fallback(step, action, points)
            clicked = False
            for x, y in targets:
                if self._stop_requested():
                    return clicked
                x += action.offset_x
                y += action.offset_y
                if self._click_point(x, y, action.button) is False:
                    return clicked
                clicked = True
                self.log(f"  click matching row ({x}, {y})")
            return clicked

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
        evaluate_uses_frame_cache = getattr(self, "_evaluate_uses_frame_cache", None)
        if evaluate_uses_frame_cache is None:
            evaluate_uses_frame_cache = self._evaluate_step_supports_frame_cache(self._evaluate_step)
        if evaluate_uses_frame_cache:
            met, points, matches = self._evaluate_step(step, frame_cache=frame_cache)
        else:
            met, points, matches = self._evaluate_step(step)
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

    def _find_matching_row_targets(self, action: Action, matches: dict):
        reference_index = action.match_condition_index
        target_index = action.on_condition_index
        if reference_index is None or target_index is None:
            return []

        reference_matches = matches.get(reference_index, [])
        remaining_targets = list(matches.get(target_index, []))
        selected = []
        for reference in sorted(reference_matches, key=lambda m: m["center"][1]):
            if self._stop_requested():
                break
            ref_y = reference["center"][1]
            row_targets = [
                target for target in remaining_targets
                if abs(target["center"][1] - ref_y) <= action.row_tolerance
            ]
            if not row_targets:
                continue
            if not self._row_level_allowed(action, reference):
                continue
            chosen = self._choose_row_target(reference, row_targets, action.target_choice)
            selected.append(chosen["center"])
            remaining_targets.remove(chosen)
            if action.row_mode != "all":
                break
        return selected

    def _row_level_allowed(self, action: Action, reference: dict):
        if self._stop_requested():
            return False
        if action.min_level is None and action.max_level is None:
            return True

        level = self._read_level_for_row(action, reference)
        center = tuple(reference.get("center", ()))
        limits = self._level_limit_text(action)
        if level is None:
            self.log(f"  [skip] row center={center} level unread; cannot compare with {limits}")
            return False
        if action.min_level is not None and level < action.min_level:
            self.log(f"  [skip] row center={center} level read {level}; {level} < min {action.min_level}")
            return False
        if action.max_level is not None and level > action.max_level:
            self.log(f"  [skip] row center={center} level read {level}; {level} > max {action.max_level}")
            return False
        self.log(f"  [level] row center={center} level read {level}; within {limits} => accepted")
        return True

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
        roi = action.level_roi or [-90, -45, 220, 100]
        window_rect = self._get_target_window_rect()
        roi_text = tuple(roi)
        center_text = tuple(reference["center"])
        min_digits = max(1, int(getattr(action, "level_min_digits", 1) or 1))
        digit_templates = self._load_digit_templates(action.level_digit_template_dir)
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
        for attempt_index, rect in enumerate(self._level_crop_rects(action, reference, window_rect)):
            if self._stop_requested():
                return None
            frame, _, _ = self._grab(rect)
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
                if fallback_level is not None and fallback_level != ocr_result.level:
                    if self._should_ignore_digit_fallback_conflict(ocr_result, fallback_level):
                        self.log(
                            f"  [level] ignored digit_fallback={fallback_level} for row center={center_text}; "
                            f"matches OCR level {ocr_result.level} with extra digit noise"
                        )
                        if attempt_index:
                            self.log(f"  [level] recovered with alternate crop rect={rect}")
                        return ocr_result.level
                    attempt["status"] = "conflict"
                    continue
                if attempt_index:
                    self.log(f"  [level] recovered with alternate crop rect={rect}")
                return ocr_result.level

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
                return fallback_level

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
            path = self._save_level_debug_crop(conflict_attempt["frame"], rect, reference)
            if path:
                self.log(f"  [debug] saved level conflict crop: {path}")
            return None

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
            path = self._save_level_debug_crop(debug_attempt["frame"], rect, reference)
            if path:
                self.log(f"  [debug] saved level crop: {path}")
        return None

    def _level_crop_rects(self, action: Action, reference: dict, window_rect=None):
        roi = action.level_roi or [-90, -45, 220, 100]
        center_x, center_y = reference["center"]
        offsets = (0, 8, 16, 24, -8, -16)
        rects = []
        seen = set()
        for y_offset in offsets:
            left = int(center_x + roi[0])
            top = int(center_y + roi[1] + y_offset)
            width = int(roi[2])
            height = int(roi[3])
            rect = self._constrain_level_rect((left, top, width, height), window_rect)
            if rect in seen:
                continue
            seen.add(rect)
            rects.append(rect)
        return rects

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

    def _save_level_debug_crop(self, frame, rect, reference):
        if frame is None:
            return None
        try:
            os.makedirs(LEVEL_DEBUG_DIR, exist_ok=True)
            left, top, width, height = rect
            center_x, center_y = reference["center"]
            stamp = f"{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns() % 1_000_000_000:09d}"
            filename = f"level_{stamp}_{left}_{top}_{width}x{height}_row{center_x}_{center_y}.png"
            path = os.path.join(LEVEL_DEBUG_DIR, filename)
            if not cv2.imwrite(path, frame):
                raise OSError(f"could not write {path}")
            return path
        except Exception as e:
            self.log(f"  [debug] could not save level crop: {e}")
            return None

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

                self.log(f"[fire] {step.name}")
                fired_any = True
                for action in step.actions:
                    if self._stop_requested():
                        return fired_any
                    invalidates_frame = self._run_action(step, action, points, matches)
                    if invalidates_frame:
                        frame_cache.clear()
                        self._window_rect_lookup_cache = {}
                    if self._stop_requested():
                        return fired_any

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
            self._window_rect_lookup_cache = None
