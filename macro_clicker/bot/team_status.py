"""Read-only visual detection for Team 1/2/3 world-map march availability.

The game's left deployment queue shows busy marches only.  On a trusted world-map
view, no busy-count/status rows therefore means 0/3 busy teams: all three teams are
Idle candidates.  The selected-team Gather dispatch panel still performs the final
blue-idle-icon check before any Dispatch click.

This module deliberately classifies map-side availability as IDLE/BUSY/UNKNOWN.
Detailed Travelling/Gathering/Returning labels and timer OCR can be layered back in
after real, committed screen fixtures exist; they are not required to decide whether
continuous Auto Gather may safely start one exact-team attempt.
"""

from __future__ import annotations

import os
import re
import threading
from collections.abc import Callable
from typing import Any

import cv2
import mss
import numpy as np

from ..detection_core import capture_bgr, region_for_capture
from ..models import project_path
from ..window_locator import find_window_rect
from .team_state import TEAM_NUMBERS, TeamActivity, TeamObservation, TeamStateTracker

REFERENCE_SIZE = (1920, 1080)

# The normal world-map search control is present on the real map screen even when
# there are zero busy teams. It prevents a blank queue on another screen from being
# interpreted as three idle teams.
WORLD_MAP_ANCHOR_REGION = (0, 780, 110, 150)
WORLD_MAP_TEMPLATE = "templates/GatherSearchIcon.jpg"
WORLD_MAP_THRESHOLD = 0.90

# One normalized crop contains both the march-count indicator and the compressed
# busy-team queue.  The queue contains busy teams only and compresses upward.
SIDEBAR_REGION = (0, 230, 220, 250)
COUNTER_REGION = (154, 236, 51, 28)
QUEUE_REGION = (0, 265, 220, 215)
COUNTER_IN_SIDEBAR = (154, 6, 51, 28)
QUEUE_IN_SIDEBAR = (0, 35, 220, 215)

BUSY_COUNT_TEMPLATES = {
    1: "templates/1_3Squad.png",
    2: "templates/2_3Squad.png",
    3: "templates/FullSquad3_3.png",
}
BUSY_COUNT_THRESHOLDS = {
    1: 0.88,
    2: 0.78,
    3: 0.78,
}
BUSY_IDENTITY_TEMPLATES = {
    1: "templates/Team1Busy.png",
    3: "templates/Team3Busy.png",
}
BUSY_IDENTITY_THRESHOLD = 0.85

_TIMER_PATTERN = re.compile(
    r"(?<!\d)(\d{1,3})\s*[:：]\s*(\d{2})\s*[:：]\s*(\d{2})(?!\d)"
)


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
    """Retained lazy OCR reader for future richer team-state fixtures."""

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
            enlarged = cv2.resize(
                crop, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC
            )
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
    """Classify map-side Team 1/2/3 availability from existing proven assets."""

    def __init__(self, timer_reader: TeamTimerReader | None = None) -> None:
        # Retain the injected reader API for compatibility. Current safe map-side
        # availability classification does not depend on OCR timers.
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
    def _best_match(
        frame: np.ndarray,
        template: np.ndarray,
    ) -> tuple[float, tuple[int, int]]:
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
    def _crop_local(frame: np.ndarray, region: tuple[int, int, int, int]) -> np.ndarray:
        left, top, width, height = region
        right = min(frame.shape[1], left + width)
        bottom = min(frame.shape[0], top + height)
        if left < 0 or top < 0 or left >= right or top >= bottom:
            return np.empty((0, 0, 3), dtype=np.uint8)
        return frame[top:bottom, left:right]

    @staticmethod
    def _crop_reference_region(
        frame: np.ndarray,
        region: tuple[int, int, int, int],
    ) -> np.ndarray:
        """Crop a 1920x1080 reference region from an arbitrary window resolution."""

        if frame is None or frame.size == 0:
            return np.empty((0, 0, 3), dtype=np.uint8)
        height, width = frame.shape[:2]
        ref_width, ref_height = REFERENCE_SIZE
        left, top, region_width, region_height = region
        scaled_left = round(left / ref_width * width)
        scaled_top = round(top / ref_height * height)
        scaled_right = round((left + region_width) / ref_width * width)
        scaled_bottom = round((top + region_height) / ref_height * height)
        scaled_left = max(0, min(scaled_left, width - 1))
        scaled_top = max(0, min(scaled_top, height - 1))
        scaled_right = max(scaled_left + 1, min(scaled_right, width))
        scaled_bottom = max(scaled_top + 1, min(scaled_bottom, height))
        crop = frame[scaled_top:scaled_bottom, scaled_left:scaled_right]
        if crop.shape[:2] != (region_height, region_width):
            crop = cv2.resize(
                crop,
                (region_width, region_height),
                interpolation=cv2.INTER_AREA,
            )
        return crop

    def _detect_busy_count(self, counter: np.ndarray) -> tuple[int, float]:
        """Return 0 when no committed 1/3, 2/3, or 3/3 indicator is present."""

        qualified: list[tuple[float, int]] = []
        best_score = -1.0
        for count, path in BUSY_COUNT_TEMPLATES.items():
            score, _location = self._best_match(counter, self._template(path))
            best_score = max(best_score, score)
            if score >= BUSY_COUNT_THRESHOLDS[count]:
                qualified.append((score, count))
        if not qualified:
            # On a trusted world-map view this is the game's 0/3 state: there is
            # no busy-team status to display.
            return 0, max(0.0, best_score)
        score, count = max(qualified)
        return count, score

    def _busy_identity(self, queue: np.ndarray, team: int) -> tuple[bool, float]:
        score, _location = self._best_match(
            queue,
            self._template(BUSY_IDENTITY_TEMPLATES[team]),
        )
        return score >= BUSY_IDENTITY_THRESHOLD, score

    @staticmethod
    def _observations_from_busy_evidence(
        busy_count: int,
        *,
        team1_busy: bool,
        team3_busy: bool,
        confidence: float | None = None,
    ) -> tuple[TeamObservation, ...]:
        """Infer Team 2 from count + known Team 1/3 portraits.

        Team 2 intentionally has no portrait template.  Because the queue contains
        exactly the busy teams, its state can be inferred by elimination once the
        1/3, 2/3, or 3/3 count is known.

        Contradictory evidence fails closed as UNKNOWN for all teams.
        """

        count = int(busy_count)
        inconsistent = False
        busy_teams: set[int]

        if count == 0:
            if team1_busy or team3_busy:
                inconsistent = True
                busy_teams = set()
            else:
                busy_teams = set()
        elif count == 1:
            if team1_busy and team3_busy:
                inconsistent = True
                busy_teams = set()
            elif team1_busy:
                busy_teams = {1}
            elif team3_busy:
                busy_teams = {3}
            else:
                busy_teams = {2}
        elif count == 2:
            if team1_busy and team3_busy:
                busy_teams = {1, 3}
            elif team1_busy:
                busy_teams = {1, 2}
            elif team3_busy:
                busy_teams = {2, 3}
            else:
                inconsistent = True
                busy_teams = set()
        elif count == 3:
            busy_teams = {1, 2, 3}
        else:
            inconsistent = True
            busy_teams = set()

        if inconsistent:
            return tuple(
                TeamObservation(
                    team=team,
                    activity=TeamActivity.UNKNOWN,
                    confidence=confidence,
                )
                for team in TEAM_NUMBERS
            )

        return tuple(
            TeamObservation(
                team=team,
                activity=(
                    TeamActivity.BUSY if team in busy_teams else TeamActivity.IDLE
                ),
                confidence=confidence,
            )
            for team in TEAM_NUMBERS
        )

    def detect_sidebar(
        self,
        sidebar: np.ndarray,
        *,
        read_timers: bool = True,
    ) -> tuple[bool, tuple[TeamObservation, ...]]:
        """Detect from the normalized 220x250 map-side team-status crop.

        ``read_timers`` is retained for API compatibility.  The current safe
        availability detector intentionally does not use timer OCR.
        """

        del read_timers
        if sidebar is None or sidebar.size == 0:
            return False, ()
        if sidebar.shape[:2] != (SIDEBAR_REGION[3], SIDEBAR_REGION[2]):
            sidebar = cv2.resize(
                sidebar,
                (SIDEBAR_REGION[2], SIDEBAR_REGION[3]),
                interpolation=cv2.INTER_AREA,
            )

        counter = self._crop_local(sidebar, COUNTER_IN_SIDEBAR)
        queue = self._crop_local(sidebar, QUEUE_IN_SIDEBAR)
        busy_count, count_score = self._detect_busy_count(counter)
        team1_busy, team1_score = self._busy_identity(queue, 1)
        team3_busy, team3_score = self._busy_identity(queue, 3)

        confidence = count_score
        if busy_count in {1, 2}:
            confidence = min(
                count_score,
                max(team1_score, team3_score, 0.0),
            )
        observations = self._observations_from_busy_evidence(
            busy_count,
            team1_busy=team1_busy,
            team3_busy=team3_busy,
            confidence=max(0.0, confidence),
        )
        return True, observations

    def detect(
        self,
        frame: np.ndarray,
        *,
        read_timers: bool = True,
    ) -> tuple[bool, tuple[TeamObservation, ...]]:
        """Observe availability only when a trusted world-map anchor is visible."""

        if frame is None or frame.size == 0:
            return False, ()

        anchor = self._crop_reference_region(frame, WORLD_MAP_ANCHOR_REGION)
        anchor_score, _anchor_location = self._best_match(
            anchor,
            self._template(WORLD_MAP_TEMPLATE),
        )
        if anchor_score < WORLD_MAP_THRESHOLD:
            return False, ()

        sidebar = self._crop_reference_region(frame, SIDEBAR_REGION)
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
