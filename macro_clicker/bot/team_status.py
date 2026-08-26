"""Read-only visual detection for Team 1/2/3 world-map march state."""

from __future__ import annotations

import os
import re
import threading
import time
from collections.abc import Callable
from typing import Any

import cv2
import mss
import numpy as np

from ..detection_core import capture_bgr, region_for_capture
from ..models import project_path
from ..window_locator import find_window_rect
from .team_state import (
    TEAM_NUMBERS,
    TeamActivity,
    TeamObservation,
    TeamStateTracker,
)

REFERENCE_SIZE = (1920, 1080)
SIDEBAR_REGION = (0, 230, 220, 250)
HEADER_THRESHOLD = 0.88
PORTRAIT_THRESHOLD = 0.90
STATE_THRESHOLD = 0.82

PORTRAIT_TEMPLATES = {
    1: "templates/Team1MarchPortrait.png",
    2: "templates/Team2MarchPortrait.png",
    3: "templates/Team3MarchPortrait.png",
}
STATE_TEMPLATES = {
    TeamActivity.GATHERING: "templates/TeamStatusGathering.png",
    TeamActivity.RETURNING: "templates/TeamStatusReturning.png",
    TeamActivity.TRAVELLING: "templates/TeamStatusTravelling.png",
}
HEADER_TEMPLATE = "templates/TeamStatusSidebarHeader.png"
_TIMER_PATTERN = re.compile(r"(?<!\d)(\d{1,3})\s*[:：]\s*(\d{2})\s*[:：]\s*(\d{2})(?!\d)")


def parse_duration_text(text: str) -> int | None:
    """Parse one OCR timer string as seconds, tolerating common digit confusions."""

    normalized = str(text or "").strip()
    normalized = normalized.translate(
        str.maketrans(
            {
                "O": "0",
                "o": "0",
                "I": "1",
                "l": "1",
                "|": "1",
                "；": ":",
                ";": ":",
            }
        )
    )
    match = _TIMER_PATTERN.search(normalized)
    if match is None:
        compact = re.sub(r"\D", "", normalized)
        if len(compact) == 6:
            match_values = (compact[:2], compact[2:4], compact[4:])
        elif len(compact) == 7:
            match_values = (compact[:3], compact[3:5], compact[5:])
        else:
            return None
    else:
        match_values = match.groups()

    hours, minutes, seconds = (int(value) for value in match_values)
    if minutes >= 60 or seconds >= 60:
        return None
    return hours * 3600 + minutes * 60 + seconds


class TeamTimerReader:
    """Lazy Paddle text recognition for the small expedition countdown crop."""

    def __init__(self) -> None:
        self._engine: Any = None
        self._init_error: str | None = None
        self._lock = threading.Lock()

    @property
    def init_error(self) -> str | None:
        return self._init_error

    def _get_engine(self):
        if self._engine is not None:
            return self._engine
        if self._init_error is not None:
            return None
        try:
            os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
            from paddleocr import TextRecognition
        except Exception as exc:
            self._init_error = f"PaddleOCR TextRecognition import failed: {exc}"
            return None

        last_error: Exception | None = None
        for kwargs in ({"model_name": "PP-OCRv6_medium_rec"}, {}):
            try:
                self._engine = TextRecognition(**kwargs)
                return self._engine
            except Exception as exc:
                last_error = exc
        self._init_error = f"PaddleOCR TextRecognition init failed: {last_error}"
        return None

    @staticmethod
    def _extract_strings(raw: Any, depth: int = 0) -> list[str]:
        if raw is None or depth > 7:
            return []
        if isinstance(raw, str):
            return [raw]
        if isinstance(raw, dict):
            result = []
            for key in ("rec_text", "text"):
                value = raw.get(key)
                if isinstance(value, str):
                    result.append(value)
            for key in ("rec_texts", "texts"):
                value = raw.get(key)
                if isinstance(value, np.ndarray):
                    value = value.tolist()
                if isinstance(value, (list, tuple)):
                    result.extend(str(item) for item in value if item is not None)
            if result:
                return result
            for value in raw.values():
                result.extend(TeamTimerReader._extract_strings(value, depth + 1))
            return result
        if isinstance(raw, np.ndarray):
            return TeamTimerReader._extract_strings(raw.tolist(), depth + 1)
        if isinstance(raw, (list, tuple)):
            result = []
            for value in raw:
                result.extend(TeamTimerReader._extract_strings(value, depth + 1))
            return result
        for attribute in ("json", "res"):
            if not hasattr(raw, attribute):
                continue
            try:
                value = getattr(raw, attribute)
                value = value() if callable(value) else value
            except Exception:
                continue
            return TeamTimerReader._extract_strings(value, depth + 1)
        return []

    def read_seconds(self, crop: np.ndarray) -> int | None:
        if crop is None or crop.size == 0:
            return None
        with self._lock:
            engine = self._get_engine()
            if engine is None:
                return None
            enlarged = cv2.resize(crop, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
            blurred = cv2.GaussianBlur(enlarged, (0, 0), 0.8)
            sharpened = cv2.addWeighted(enlarged, 1.5, blurred, -0.5, 0)
            try:
                raw = engine.predict(sharpened)
            except Exception:
                return None
            for text in self._extract_strings(raw):
                seconds = parse_duration_text(text)
                if seconds is not None:
                    return seconds
            return None


class TeamStatusDetector:
    """Classify world-map expedition rows using stable portrait and label templates."""

    def __init__(self, timer_reader: TeamTimerReader | None = None) -> None:
        self.timer_reader = timer_reader or TeamTimerReader()
        self._templates: dict[str, np.ndarray] = {}

    def _template(self, path: str) -> np.ndarray:
        cached = self._templates.get(path)
        if cached is not None:
            return cached
        image = cv2.imread(project_path(path), cv2.IMREAD_COLOR)
        if image is None or image.size == 0:
            raise FileNotFoundError(f"team-status template is unavailable: {path}")
        self._templates[path] = image
        return image

    @staticmethod
    def _best_match(frame: np.ndarray, template: np.ndarray) -> tuple[float, tuple[int, int]]:
        if (
            frame is None
            or frame.size == 0
            or frame.shape[0] < template.shape[0]
            or frame.shape[1] < template.shape[1]
        ):
            return -1.0, (0, 0)
        scores = cv2.matchTemplate(frame, template, cv2.TM_CCOEFF_NORMED)
        _minimum, maximum, _min_loc, max_loc = cv2.minMaxLoc(scores)
        return float(maximum), (int(max_loc[0]), int(max_loc[1]))

    @staticmethod
    def _row_crop(sidebar: np.ndarray, portrait_top: int) -> np.ndarray:
        top = max(0, int(portrait_top) - 4)
        bottom = min(sidebar.shape[0], int(portrait_top) + 30)
        return sidebar[top:bottom, 58:178]

    @staticmethod
    def _timer_crop(sidebar: np.ndarray, portrait_top: int) -> np.ndarray:
        top = max(0, int(portrait_top) + 25)
        bottom = min(sidebar.shape[0], int(portrait_top) + 51)
        return sidebar[top:bottom, 66:174]

    def _classify_row(self, sidebar: np.ndarray, portrait_top: int) -> TeamActivity:
        row = self._row_crop(sidebar, portrait_top)
        best_activity = TeamActivity.BUSY
        best_score = STATE_THRESHOLD
        for activity, path in STATE_TEMPLATES.items():
            score, _location = self._best_match(row, self._template(path))
            if score >= best_score:
                best_score = score
                best_activity = activity
        return best_activity

    def detect_sidebar(
        self,
        sidebar: np.ndarray,
        *,
        read_timers: bool = True,
    ) -> tuple[bool, tuple[TeamObservation, ...]]:
        """Detect from a normalized 220x250 sidebar crop (used by runtime and tests)."""

        if sidebar is None or sidebar.size == 0:
            return False, ()
        if sidebar.shape[:2] != (SIDEBAR_REGION[3], SIDEBAR_REGION[2]):
            sidebar = cv2.resize(
                sidebar,
                (SIDEBAR_REGION[2], SIDEBAR_REGION[3]),
                interpolation=cv2.INTER_AREA,
            )

        header_score, _header_location = self._best_match(
            sidebar,
            self._template(HEADER_TEMPLATE),
        )
        if header_score < HEADER_THRESHOLD:
            return False, ()

        observations = []
        for team in TEAM_NUMBERS:
            portrait = self._template(PORTRAIT_TEMPLATES[team])
            portrait_score, portrait_location = self._best_match(sidebar, portrait)
            if portrait_score < PORTRAIT_THRESHOLD:
                # The expedition list contains busy teams only. Absence of this
                # known captain portrait is therefore an idle candidate; the
                # Gather dispatch panel still re-verifies the blue Z idle icon
                # before any click, so this read-only hint cannot dispatch a
                # falsely-idle team by itself.
                observations.append(
                    TeamObservation(
                        team=team,
                        activity=TeamActivity.IDLE,
                        confidence=max(0.0, 1.0 - portrait_score),
                    )
                )
                continue

            portrait_top = portrait_location[1]
            activity = self._classify_row(sidebar, portrait_top)
            remaining = None
            if read_timers:
                remaining = self.timer_reader.read_seconds(
                    self._timer_crop(sidebar, portrait_top)
                )
            observations.append(
                TeamObservation(
                    team=team,
                    activity=activity,
                    remaining_seconds=remaining,
                    confidence=portrait_score,
                )
            )
        return True, tuple(observations)

    def detect(self, frame: np.ndarray, *, read_timers: bool = True):
        """Normalize the expedition sidebar from a full target-window capture."""

        if frame is None or frame.size == 0:
            return False, ()
        height, width = frame.shape[:2]
        ref_width, ref_height = REFERENCE_SIZE
        left = round(SIDEBAR_REGION[0] / ref_width * width)
        top = round(SIDEBAR_REGION[1] / ref_height * height)
        right = round((SIDEBAR_REGION[0] + SIDEBAR_REGION[2]) / ref_width * width)
        bottom = round((SIDEBAR_REGION[1] + SIDEBAR_REGION[3]) / ref_height * height)
        left = max(0, min(left, width - 1))
        top = max(0, min(top, height - 1))
        right = max(left + 1, min(right, width))
        bottom = max(top + 1, min(bottom, height))
        sidebar = frame[top:bottom, left:right]
        sidebar = cv2.resize(
            sidebar,
            (SIDEBAR_REGION[2], SIDEBAR_REGION[3]),
            interpolation=cv2.INTER_AREA,
        )
        return self.detect_sidebar(sidebar, read_timers=read_timers)


class TeamStatusMonitor:
    """Background visual observer. It owns no mouse/keyboard input."""

    def __init__(
        self,
        target_title_provider: Callable[[], str],
        tracker: TeamStateTracker,
        *,
        configured_teams_provider: Callable[[], tuple[int, ...]] | None = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self._target_title_provider = target_title_provider
        self._configured_teams_provider = configured_teams_provider or (
            lambda: TEAM_NUMBERS
        )
        self.tracker = tracker
        self.detector = TeamStatusDetector()
        self.log = log or (lambda _message: None)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_error: str | None = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.is_running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="team-status-monitor",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self._thread = None

    def _record_error(self, message: str) -> None:
        if message == self._last_error:
            return
        self._last_error = message
        self.log(f"[team] {message}")

    def _run(self) -> None:
        try:
            capture = mss.MSS()
        except Exception as exc:
            self._record_error(f"status monitor could not open screen capture: {exc}")
            return
        try:
            while not self._stop_event.is_set():
                delay = 3.0
                try:
                    title = str(self._target_title_provider() or "").strip()
                    rect = find_window_rect(title) if title else None
                    if rect is None:
                        self.tracker.update((), sidebar_visible=False)
                        delay = 3.0
                    else:
                        left, top, width, height = rect
                        frame = capture_bgr(
                            capture,
                            region_for_capture((left, top, width, height)),
                        )
                        visible, observations = self.detector.detect(frame)
                        changed = self.tracker.update(
                            observations,
                            sidebar_visible=visible,
                        )
                        if changed and visible:
                            summary = ", ".join(
                                f"T{item.team}={item.activity.value}"
                                for item in observations
                            )
                            self.log(f"[team] {summary}")
                        configured = self._configured_teams_provider()
                        delay = self.tracker.next_visual_check_delay(configured)
                        self._last_error = None
                except Exception as exc:
                    self.tracker.update((), sidebar_visible=False)
                    self._record_error(
                        "status observation failed: "
                        f"{type(exc).__name__}: {exc}"
                    )
                    delay = 5.0
                self._stop_event.wait(max(0.5, min(30.0, float(delay))))
        finally:
            try:
                capture.close()
            except Exception:
                pass
