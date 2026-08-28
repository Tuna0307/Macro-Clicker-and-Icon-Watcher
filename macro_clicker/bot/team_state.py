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
    RALLYING = "rallying"
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

    POST_DISPATCH_STABILIZATION_SECONDS = 5.0

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._states = {team: _StoredTeamState() for team in TEAM_NUMBERS}
        self._sidebar_visible = False
        self._last_sidebar_seen_at: float | None = None
        self._busy_count: int | None = None
        self._post_dispatch_stabilize_until = 0.0
        self._latest_update_at: float | None = None

    @property
    def sidebar_visible(self) -> bool:
        with self._lock:
            return self._sidebar_visible

    @property
    def last_sidebar_seen_at(self) -> float | None:
        with self._lock:
            return self._last_sidebar_seen_at

    @property
    def busy_count(self) -> int | None:
        """Return the current readable world-map busy count, otherwise None."""

        with self._lock:
            return self._busy_count if self._sidebar_visible else None

    def update(
        self,
        observations: Iterable[TeamObservation],
        *,
        sidebar_visible: bool,
        busy_count: int | None = None,
        observed_at: float | None = None,
    ) -> bool:
        """Store one visual observation and return whether meaningful state changed.

        When the march sidebar is not readable (for example while Rally or a
        dispatch panel is open), existing team states are retained instead of
        being converted to UNKNOWN. The current busy-count signal is cleared so
        an unrelated screen can never authorize a Gather decision.
        """

        now = time.monotonic() if observed_at is None else float(observed_at)
        with self._lock:
            if self._latest_update_at is not None and now < self._latest_update_at:
                # OCR can make a monitor pass finish several seconds after its
                # pixels were captured. Never let that older frame overwrite a
                # newer exact dispatch or a newer completed visual observation.
                return False
            self._latest_update_at = now
            visible = bool(sidebar_visible)
            normalized_count = None
            if visible and busy_count is not None:
                value = int(busy_count)
                if value not in {0, 1, 2, 3}:
                    raise ValueError(f"unsupported busy count: {busy_count!r}")
                normalized_count = value

            changed = (
                self._sidebar_visible != visible
                or self._busy_count != normalized_count
            )
            self._sidebar_visible = visible
            self._busy_count = normalized_count
            if not visible:
                return changed

            self._last_sidebar_seen_at = now
            by_team = {int(item.team): item for item in observations}
            for team in TEAM_NUMBERS:
                item = by_team.get(team)
                if item is None:
                    item = TeamObservation(team=team, activity=TeamActivity.UNKNOWN)
                prior = self._states[team]
                if (
                    now < self._post_dispatch_stabilize_until
                    and prior.activity
                    not in {TeamActivity.IDLE, TeamActivity.UNKNOWN}
                    and item.activity in {TeamActivity.IDLE, TeamActivity.UNKNOWN}
                ):
                    # The world map and its compressed deployment queue render
                    # independently after Dispatch closes. A briefly blank
                    # queue must not erase exact dispatch history or another
                    # team's recent busy state and immediately resend Team 1.
                    continue
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
            self._latest_update_at = max(self._latest_update_at or now, now)
            self._post_dispatch_stabilize_until = max(
                self._post_dispatch_stabilize_until,
                now + self.POST_DISPATCH_STABILIZATION_SECONDS,
            )
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
            return 1.0
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
