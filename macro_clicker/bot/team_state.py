"""Shared normal-user team state for continuous bot automation.

Visual game state remains authoritative. Countdown values are scheduling hints:
when a countdown reaches zero the tracker requests another visual observation;
it never promotes a team to IDLE on elapsed time alone.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Iterable

TEAM_NUMBERS = (1, 2, 3)


class TeamActivity(str, Enum):
    UNKNOWN = "unknown"
    IDLE = "idle"
    TRAVELLING = "travelling"
    GATHERING = "gathering"
    RETURNING = "returning"
    BUSY = "busy"


@dataclass(frozen=True)
class TeamObservation:
    team: int
    activity: TeamActivity
    remaining_seconds: int | None = None
    confidence: float | None = None


@dataclass(frozen=True)
class TeamSnapshot:
    team: int
    activity: TeamActivity
    remaining_seconds: int | None
    last_seen_age: float | None
    confidence: float | None
    sidebar_visible: bool
    needs_visual_refresh: bool


@dataclass
class _StoredTeamState:
    activity: TeamActivity = TeamActivity.UNKNOWN
    remaining_seconds: int | None = None
    observed_at: float | None = None
    confidence: float | None = None


class TeamStateTracker:
    """Thread-safe team state shared by monitoring, scheduling, and the Dashboard."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._states = {team: _StoredTeamState() for team in TEAM_NUMBERS}
        self._sidebar_visible = False
        self._last_sidebar_seen_at: float | None = None

    @property
    def sidebar_visible(self) -> bool:
        with self._lock:
            return self._sidebar_visible

    @property
    def last_sidebar_seen_at(self) -> float | None:
        with self._lock:
            return self._last_sidebar_seen_at

    def update(
        self,
        observations: Iterable[TeamObservation],
        *,
        sidebar_visible: bool,
        observed_at: float | None = None,
    ) -> bool:
        """Store one visual observation and return whether meaningful state changed.

        When the march sidebar is not visible (for example while Rally or a
        dispatch panel is open), existing states are retained instead of being
        converted to UNKNOWN. This lets the Bot keep useful last-known timing
        without mistaking a different game screen for three idle teams.
        """

        now = time.monotonic() if observed_at is None else float(observed_at)
        with self._lock:
            changed = self._sidebar_visible != bool(sidebar_visible)
            self._sidebar_visible = bool(sidebar_visible)
            if not sidebar_visible:
                return changed

            self._last_sidebar_seen_at = now
            by_team = {int(item.team): item for item in observations}
            for team in TEAM_NUMBERS:
                item = by_team.get(team)
                if item is None:
                    item = TeamObservation(team=team, activity=TeamActivity.UNKNOWN)
                prior = self._states[team]
                remaining = (
                    None
                    if item.remaining_seconds is None
                    else max(0, int(item.remaining_seconds))
                )
                if (
                    prior.activity != item.activity
                    or prior.remaining_seconds != remaining
                    or prior.confidence != item.confidence
                ):
                    changed = True
                self._states[team] = _StoredTeamState(
                    activity=item.activity,
                    remaining_seconds=remaining,
                    observed_at=now,
                    confidence=item.confidence,
                )
            return changed

    def mark_dispatched(
        self,
        team: int,
        *,
        travel_seconds: int | None = None,
        observed_at: float | None = None,
    ) -> None:
        """Immediately make a just-clicked team non-idle until visuals confirm it."""

        team = int(team)
        if team not in TEAM_NUMBERS:
            raise ValueError(f"unsupported team: {team!r}")
        now = time.monotonic() if observed_at is None else float(observed_at)
        with self._lock:
            self._states[team] = _StoredTeamState(
                activity=TeamActivity.TRAVELLING,
                remaining_seconds=(
                    None if travel_seconds is None else max(0, int(travel_seconds))
                ),
                observed_at=now,
                confidence=None,
            )

    @staticmethod
    def _effective_remaining(state: _StoredTeamState, now: float) -> int | None:
        if state.remaining_seconds is None:
            return None
        if state.observed_at is None:
            return max(0, int(state.remaining_seconds))
        elapsed = max(0.0, now - state.observed_at)
        return max(0, int(round(state.remaining_seconds - elapsed)))

    def snapshots(self, *, now: float | None = None) -> tuple[TeamSnapshot, ...]:
        current = time.monotonic() if now is None else float(now)
        with self._lock:
            result = []
            for team in TEAM_NUMBERS:
                state = self._states[team]
                remaining = self._effective_remaining(state, current)
                age = (
                    None
                    if state.observed_at is None
                    else max(0.0, current - state.observed_at)
                )
                needs_refresh = (
                    state.activity not in {TeamActivity.IDLE, TeamActivity.UNKNOWN}
                    and state.remaining_seconds is not None
                    and remaining == 0
                )
                result.append(
                    TeamSnapshot(
                        team=team,
                        activity=state.activity,
                        remaining_seconds=remaining,
                        last_seen_age=age,
                        confidence=state.confidence,
                        sidebar_visible=self._sidebar_visible,
                        needs_visual_refresh=needs_refresh,
                    )
                )
            return tuple(result)

    def snapshot(self, team: int, *, now: float | None = None) -> TeamSnapshot:
        team = int(team)
        if team not in TEAM_NUMBERS:
            raise ValueError(f"unsupported team: {team!r}")
        return self.snapshots(now=now)[team - 1]

    def available_teams(
        self,
        configured_teams: Iterable[int] = TEAM_NUMBERS,
        *,
        now: float | None = None,
    ) -> tuple[int, ...]:
        allowed = {int(team) for team in configured_teams if int(team) in TEAM_NUMBERS}
        return tuple(
            item.team
            for item in self.snapshots(now=now)
            if item.team in allowed and item.activity == TeamActivity.IDLE
        )

    def next_visual_check_delay(
        self,
        configured_teams: Iterable[int] = TEAM_NUMBERS,
        *,
        now: float | None = None,
    ) -> float:
        """Choose an efficient poll delay without trusting timers as completion proof."""

        allowed = {int(team) for team in configured_teams if int(team) in TEAM_NUMBERS}
        states = [item for item in self.snapshots(now=now) if item.team in allowed]
        if not states:
            return 5.0
        if any(item.activity == TeamActivity.IDLE for item in states):
            return 1.0
        if any(item.activity == TeamActivity.UNKNOWN for item in states):
            return 3.0
        if any(item.needs_visual_refresh for item in states):
            return 1.0

        remaining = [
            item.remaining_seconds
            for item in states
            if item.remaining_seconds is not None and item.remaining_seconds > 0
        ]
        if not remaining:
            return 5.0
        soonest = min(remaining)
        if soonest <= 10:
            return 1.0
        if soonest <= 60:
            return 3.0
        if soonest <= 300:
            return 10.0
        return 30.0


def format_remaining(seconds: int | None) -> str:
    if seconds is None:
        return "--:--:--"
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"
