"""Read-only world-map Team 1/2/3 state detection for continuous Auto Gather."""

from __future__ import annotations

import itertools
import os
import re
import threading
from collections.abc import Callable, Iterable
from typing import Any

import cv2
import mss
import numpy as np

from ..detection_core import capture_bgr, region_for_capture
from ..models import project_path
from ..runtime_paths import USER_DATA_DIR
from ..window_locator import find_window_rect
from .team_state import TEAM_NUMBERS, TeamActivity, TeamObservation, TeamStateTracker

REFERENCE_SIZE = (1920, 1080)
WORLD_MAP_ANCHOR_REGION = (0, 780, 110, 150)
WORLD_MAP_TEMPLATE = "templates/GatherSearchIcon.jpg"
WORLD_MAP_THRESHOLD = 0.90

SIDEBAR_REGION = (0, 230, 220, 250)
COUNTER_IN_SIDEBAR = (154, 6, 51, 28)
BUSY_ROW_REGIONS = ((8, 45, 205, 60), (8, 106, 205, 60), (8, 167, 205, 60))
ROW_PORTRAIT_REGION = (6, 3, 54, 52)
ROW_TIMER_REGION = (70, 25, 100, 30)

BUSY_COUNT_TEMPLATES = {
    1: "templates/1_3Squad.png",
    2: "templates/2_3Squad.png",
    3: "templates/FullSquad3_3.png",
}
BUSY_COUNT_THRESHOLDS = {1: 0.88, 2: 0.78, 3: 0.78}

ACTIVITY_TEMPLATES = {
    TeamActivity.GATHERING: "templates/TeamStatusGathering.png",
    TeamActivity.RETURNING: "templates/TeamStatusReturning.png",
    TeamActivity.TRAVELLING: "templates/TeamStatusTravelling.png",
    TeamActivity.RALLYING: "templates/TeamStatusRallying.png",
}
ACTIVITY_THRESHOLD = 0.72

# Hero portraits change when the user changes a team leader. These two old Rally
# templates are positive bootstrap hints only. Absence NEVER implies Team 2.
BOOTSTRAP_IDENTITY_TEMPLATES = {
    1: "templates/Team1Busy.png",
    3: "templates/Team3Busy.png",
}
BOOTSTRAP_IDENTITY_THRESHOLD = 0.90
PORTRAIT_CACHE_DIR = os.path.join(USER_DATA_DIR, "team_portraits")
PORTRAIT_MATCH_MIN_GOOD = 7
PORTRAIT_MATCH_MARGIN = 3

_TIMER_PATTERN = re.compile(
    r"(?<!\d)(\d{1,3})\s*[:：]\s*(\d{2})\s*[:：]\s*(\d{2})(?!\d)"
)


def parse_duration_text(text: str) -> int | None:
    normalized = str(text or "").strip().translate(
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
    if match:
        values = match.groups()
    else:
        compact = re.sub(r"\D", "", normalized)
        if len(compact) == 6:
            values = (compact[:2], compact[2:4], compact[4:])
        elif len(compact) == 7:
            values = (compact[:3], compact[3:5], compact[5:])
        else:
            return None
    hours, minutes, seconds = (int(value) for value in values)
    if minutes >= 60 or seconds >= 60:
        return None
    return hours * 3600 + minutes * 60 + seconds


class TeamTimerReader:
    """Lazy OCR for numeric HH:MM:SS timer crops."""

    def __init__(self) -> None:
        self._engine: Any = None
        self._error: str | None = None
        self._lock = threading.Lock()

    @staticmethod
    def _strings(value: Any, depth: int = 0) -> list[str]:
        if value is None or depth > 6:
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, np.ndarray):
            value = value.tolist()
        if isinstance(value, dict):
            direct = [
                item
                for key in ("rec_text", "text")
                if isinstance((item := value.get(key)), str)
            ]
            if direct:
                return direct
            result: list[str] = []
            for item in value.values():
                result.extend(TeamTimerReader._strings(item, depth + 1))
            return result
        if isinstance(value, (list, tuple)):
            result: list[str] = []
            for item in value:
                result.extend(TeamTimerReader._strings(item, depth + 1))
            return result
        for attr in ("json", "res"):
            if hasattr(value, attr):
                try:
                    item = getattr(value, attr)
                    return TeamTimerReader._strings(
                        item() if callable(item) else item,
                        depth + 1,
                    )
                except Exception:
                    pass
        return []

    def _get_engine(self):
        if self._engine is not None or self._error is not None:
            return self._engine
        try:
            os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
            from paddleocr import TextRecognition

            last_error: Exception | None = None
            for kwargs in ({"model_name": "PP-OCRv6_medium_rec"}, {}):
                try:
                    self._engine = TextRecognition(**kwargs)
                    return self._engine
                except Exception as exc:
                    last_error = exc
            self._error = str(last_error)
        except Exception as exc:
            self._error = str(exc)
        return self._engine

    def read_seconds(self, crop: np.ndarray) -> int | None:
        if crop is None or crop.size == 0:
            return None
        with self._lock:
            engine = self._get_engine()
            if engine is None:
                return None
            enlarged = cv2.resize(
                crop,
                None,
                fx=4,
                fy=4,
                interpolation=cv2.INTER_CUBIC,
            )
            try:
                raw = engine.predict(enlarged)
            except Exception:
                return None
            for text in self._strings(raw):
                seconds = parse_duration_text(text)
                if seconds is not None:
                    return seconds
        return None


class TeamStatusDetector:
    """Interpret the compressed sidebar without binding row N to Team N."""

    def __init__(
        self,
        timer_reader: TeamTimerReader | None = None,
        *,
        portrait_cache_dir: str | None = PORTRAIT_CACHE_DIR,
    ) -> None:
        self.timer_reader = timer_reader or TeamTimerReader()
        self._templates: dict[str, np.ndarray] = {}
        self._portrait_cache_dir = portrait_cache_dir
        self._team_portraits: dict[int, np.ndarray] = {}
        self._missing_activity_templates: set[str] = set()
        self.last_busy_count: int | None = None
        self.last_identity_complete = False
        self._load_portraits()

    @property
    def missing_activity_templates(self) -> tuple[str, ...]:
        """Return optional detailed-status assets missing from this runtime."""

        return tuple(sorted(self._missing_activity_templates))

    def _template(self, path: str) -> np.ndarray:
        image = self._templates.get(path)
        if image is None:
            image = cv2.imread(project_path(path), cv2.IMREAD_COLOR)
            if image is None or image.size == 0:
                raise FileNotFoundError(
                    f"team-status template is unavailable: {path}"
                )
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
    def _crop_local(
        frame: np.ndarray,
        region: tuple[int, int, int, int],
    ) -> np.ndarray:
        left, top, width, height = region
        return frame[top : top + height, left : left + width]

    @staticmethod
    def _crop_reference_region(
        frame: np.ndarray,
        region: tuple[int, int, int, int],
    ) -> np.ndarray:
        if frame is None or frame.size == 0:
            return np.empty((0, 0, 3), dtype=np.uint8)
        height, width = frame.shape[:2]
        ref_width, ref_height = REFERENCE_SIZE
        left, top, region_width, region_height = region
        x1 = round(left / ref_width * width)
        y1 = round(top / ref_height * height)
        x2 = round((left + region_width) / ref_width * width)
        y2 = round((top + region_height) / ref_height * height)
        crop = frame[
            max(0, y1) : min(height, y2),
            max(0, x1) : min(width, x2),
        ]
        if crop.shape[:2] != (region_height, region_width):
            crop = cv2.resize(
                crop,
                (region_width, region_height),
                interpolation=cv2.INTER_AREA,
            )
        return crop

    def _busy_count(self, counter: np.ndarray) -> tuple[int, float]:
        matches = []
        best = -1.0
        for count, path in BUSY_COUNT_TEMPLATES.items():
            score, _loc = self._best_match(counter, self._template(path))
            best = max(best, score)
            if score >= BUSY_COUNT_THRESHOLDS[count]:
                matches.append((score, count))
        if not matches:
            return 0, max(0.0, best)
        score, count = max(matches)
        return count, score

    def _activity(self, row: np.ndarray) -> tuple[TeamActivity, float]:
        matches: list[tuple[float, TeamActivity]] = []
        for activity, path in ACTIVITY_TEMPLATES.items():
            try:
                template = self._template(path)
            except FileNotFoundError:
                # Detailed labels improve scheduling/UI but are not authority for
                # whether a row is busy. A missing optional crop must not destroy
                # the whole availability observation.
                self._missing_activity_templates.add(path)
                continue
            self._missing_activity_templates.discard(path)
            score, _loc = self._best_match(row, template)
            matches.append((score, activity))

        if not matches:
            return TeamActivity.BUSY, 0.0
        score, activity = max(matches)
        if score < ACTIVITY_THRESHOLD:
            return TeamActivity.BUSY, max(0.0, score)
        return activity, score

    def _cache_path(self, team: int) -> str | None:
        if not self._portrait_cache_dir:
            return None
        return os.path.join(self._portrait_cache_dir, f"team_{team}.png")

    def _load_portraits(self) -> None:
        for team in TEAM_NUMBERS:
            path = self._cache_path(team)
            if path and os.path.isfile(path):
                image = cv2.imread(path, cv2.IMREAD_COLOR)
                if image is not None and image.size:
                    self._team_portraits[team] = image

    def _remember_portrait(self, team: int, portrait: np.ndarray) -> None:
        if portrait is None or portrait.size == 0:
            return
        portrait = cv2.resize(portrait, (54, 52), interpolation=cv2.INTER_AREA)
        self._team_portraits[int(team)] = portrait
        path = self._cache_path(int(team))
        if not path:
            return
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            cv2.imwrite(path, portrait)
        except (OSError, cv2.error):
            pass

    @staticmethod
    def _portrait_good_matches(left: np.ndarray, right: np.ndarray) -> int:
        if left is None or right is None or left.size == 0 or right.size == 0:
            return 0
        left = cv2.cvtColor(left, cv2.COLOR_BGR2GRAY)
        right = cv2.cvtColor(right, cv2.COLOR_BGR2GRAY)
        if hasattr(cv2, "SIFT_create"):
            detector = cv2.SIFT_create(
                nfeatures=160,
                contrastThreshold=0.01,
                edgeThreshold=10,
            )
            norm, ratio = cv2.NORM_L2, 0.72
        else:
            detector = cv2.ORB_create(
                nfeatures=220,
                scaleFactor=1.1,
                edgeThreshold=5,
                patchSize=15,
                fastThreshold=5,
            )
            norm, ratio = cv2.NORM_HAMMING, 0.75
        _left_keys, left_desc = detector.detectAndCompute(left, None)
        _right_keys, right_desc = detector.detectAndCompute(right, None)
        if left_desc is None or right_desc is None:
            return 0
        pairs = cv2.BFMatcher(norm).knnMatch(left_desc, right_desc, k=2)
        return sum(
            1
            for first, second in pairs
            if first.distance < ratio * second.distance
        )

    def _dynamic_identity(self, portrait: np.ndarray) -> int | None:
        scores = sorted(
            (
                (self._portrait_good_matches(portrait, cached), team)
                for team, cached in self._team_portraits.items()
            ),
            reverse=True,
        )
        if not scores:
            return None
        best_score, best_team = scores[0]
        second = scores[1][0] if len(scores) > 1 else 0
        if (
            best_score >= PORTRAIT_MATCH_MIN_GOOD
            and best_score - second >= PORTRAIT_MATCH_MARGIN
        ):
            return int(best_team)
        return None

    def _bootstrap_identity(self, row: np.ndarray) -> int | None:
        matches = []
        for team, path in BOOTSTRAP_IDENTITY_TEMPLATES.items():
            score, _loc = self._best_match(row, self._template(path))
            if score >= BOOTSTRAP_IDENTITY_THRESHOLD:
                matches.append((score, team))
        if not matches:
            return None
        matches.sort(reverse=True)
        if len(matches) > 1 and matches[0][0] - matches[1][0] < 0.03:
            return None
        return int(matches[0][1])

    def _row_identity(self, row: np.ndarray, portrait: np.ndarray) -> int | None:
        dynamic = self._dynamic_identity(portrait)
        if dynamic is not None:
            return dynamic
        return self._bootstrap_identity(row)

    @staticmethod
    def _candidate_assignments(
        busy_count: int,
        row_identities: Iterable[int | None],
        known_busy_teams: Iterable[int] = (),
    ) -> list[tuple[int, ...]]:
        count = int(busy_count)
        identities = tuple(row_identities)
        if count not in {0, 1, 2, 3} or len(identities) != count:
            return []
        candidates = [
            candidate
            for candidate in itertools.combinations(TEAM_NUMBERS, count)
            if all(
                identity is None or candidate[index] == int(identity)
                for index, identity in enumerate(identities)
            )
        ]
        known = {
            int(team)
            for team in known_busy_teams
            if int(team) in TEAM_NUMBERS
        }
        if known and len(known) <= count:
            narrowed = [
                candidate
                for candidate in candidates
                if known.issubset(candidate)
            ]
            if narrowed:
                candidates = narrowed
        return candidates

    def _observations(
        self,
        busy_count: int,
        rows: list[np.ndarray],
        identities: list[int | None],
        activities: list[TeamActivity],
        timers: list[int | None],
        *,
        known_busy_teams: Iterable[int],
        confidence: float,
    ) -> tuple[TeamObservation, ...]:
        if busy_count == 0:
            self.last_identity_complete = True
            return tuple(
                TeamObservation(team, TeamActivity.IDLE, confidence=confidence)
                for team in TEAM_NUMBERS
            )
        candidates = self._candidate_assignments(
            busy_count,
            identities,
            known_busy_teams,
        )
        if not candidates:
            self.last_identity_complete = False
            return tuple(
                TeamObservation(team, TeamActivity.UNKNOWN)
                for team in TEAM_NUMBERS
            )

        self.last_identity_complete = len(candidates) == 1
        fixed_rows: dict[int, int] = {}
        for index in range(busy_count):
            possible = {candidate[index] for candidate in candidates}
            if len(possible) == 1:
                fixed_rows[index] = next(iter(possible))

        always_busy = set.intersection(
            *(set(candidate) for candidate in candidates)
        )
        possibly_busy = set.union(*(set(candidate) for candidate in candidates))
        result: dict[int, TeamObservation] = {}
        for index, team in fixed_rows.items():
            result[team] = TeamObservation(
                team,
                activities[index],
                remaining_seconds=timers[index],
                confidence=confidence,
            )
            self._remember_portrait(
                team,
                self._crop_local(rows[index], ROW_PORTRAIT_REGION),
            )

        for team in TEAM_NUMBERS:
            if team in result:
                continue
            if team in always_busy:
                activity = TeamActivity.BUSY
            elif team not in possibly_busy:
                activity = TeamActivity.IDLE
            else:
                activity = TeamActivity.UNKNOWN
            result[team] = TeamObservation(
                team,
                activity,
                confidence=confidence,
            )
        return tuple(result[team] for team in TEAM_NUMBERS)

    def detect_sidebar(
        self,
        sidebar: np.ndarray,
        *,
        read_timers: bool = True,
        known_busy_teams: Iterable[int] = (),
    ) -> tuple[bool, tuple[TeamObservation, ...]]:
        if sidebar is None or sidebar.size == 0:
            self.last_busy_count = None
            self.last_identity_complete = False
            return False, ()
        if sidebar.shape[:2] != (250, 220):
            sidebar = cv2.resize(
                sidebar,
                (220, 250),
                interpolation=cv2.INTER_AREA,
            )

        count, count_score = self._busy_count(
            self._crop_local(sidebar, COUNTER_IN_SIDEBAR)
        )
        self.last_busy_count = count
        rows = [
            self._crop_local(sidebar, BUSY_ROW_REGIONS[index])
            for index in range(count)
        ]
        identities: list[int | None] = []
        activities: list[TeamActivity] = []
        timers: list[int | None] = []
        activity_scores: list[float] = []
        for row in rows:
            portrait = self._crop_local(row, ROW_PORTRAIT_REGION)
            identities.append(self._row_identity(row, portrait))
            activity, score = self._activity(row)
            activities.append(activity)
            activity_scores.append(score)
            timer = (
                self.timer_reader.read_seconds(
                    self._crop_local(row, ROW_TIMER_REGION)
                )
                if read_timers
                else None
            )
            timers.append(timer)

        confidence = max(0.0, count_score)
        if activity_scores:
            confidence = min(confidence, max(activity_scores))
        return True, self._observations(
            count,
            rows,
            identities,
            activities,
            timers,
            known_busy_teams=known_busy_teams,
            confidence=confidence,
        )

    def detect(
        self,
        frame: np.ndarray,
        *,
        read_timers: bool = True,
        known_busy_teams: Iterable[int] = (),
    ) -> tuple[bool, tuple[TeamObservation, ...]]:
        if frame is None or frame.size == 0:
            return False, ()
        anchor = self._crop_reference_region(frame, WORLD_MAP_ANCHOR_REGION)
        score, _loc = self._best_match(
            anchor,
            self._template(WORLD_MAP_TEMPLATE),
        )
        if score < WORLD_MAP_THRESHOLD:
            self.last_busy_count = None
            self.last_identity_complete = False
            return False, ()
        return self.detect_sidebar(
            self._crop_reference_region(frame, SIDEBAR_REGION),
            read_timers=read_timers,
            known_busy_teams=known_busy_teams,
        )


class TeamStatusMonitor:
    """Background observer; it never owns mouse or keyboard input."""

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
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None

    def _error(self, message: str) -> None:
        if message != self._last_error:
            self._last_error = message
            self.log(f"[team] {message}")

    def _known_busy(self) -> tuple[int, ...]:
        non_idle = {
            TeamActivity.TRAVELLING,
            TeamActivity.GATHERING,
            TeamActivity.RETURNING,
            TeamActivity.RALLYING,
            TeamActivity.BUSY,
        }
        return tuple(
            item.team
            for item in self.tracker.snapshots()
            if item.activity in non_idle
        )

    @staticmethod
    def _summary(items: Iterable[TeamObservation]) -> str:
        parts = []
        for item in items:
            text = f"T{item.team}={item.activity.value}"
            if item.remaining_seconds is not None:
                total = max(0, int(item.remaining_seconds))
                hours, rem = divmod(total, 3600)
                minutes, seconds = divmod(rem, 60)
                text += f" {hours:02d}:{minutes:02d}:{seconds:02d}"
            parts.append(text)
        return ", ".join(parts)

    def _run(self) -> None:
        try:
            capture = mss.MSS()
        except Exception as exc:
            self._error(f"status monitor could not open screen capture: {exc}")
            return
        try:
            while not self._stop_event.is_set():
                delay = 3.0
                try:
                    title = str(self._target_title_provider() or "").strip()
                    rect = find_window_rect(title) if title else None
                    if rect is None:
                        self.tracker.update(
                            (),
                            sidebar_visible=False,
                            busy_count=None,
                        )
                    else:
                        left, top, width, height = rect
                        frame = capture_bgr(
                            capture,
                            region_for_capture((left, top, width, height)),
                        )
                        visible, observations = self.detector.detect(
                            frame,
                            known_busy_teams=self._known_busy(),
                        )
                        changed = self.tracker.update(
                            observations,
                            sidebar_visible=visible,
                            busy_count=(
                                self.detector.last_busy_count
                                if visible
                                else None
                            ),
                        )
                        if changed and visible:
                            self.log(f"[team] {self._summary(observations)}")
                        delay = self.tracker.next_visual_check_delay(
                            self._configured_teams_provider()
                        )
                        self._last_error = None
                except Exception as exc:
                    self.tracker.update(
                        (),
                        sidebar_visible=False,
                        busy_count=None,
                    )
                    self._error(
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
