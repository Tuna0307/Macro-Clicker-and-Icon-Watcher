"""Continuous Auto Gather decisions built on visually observed team state.

This coordinator intentionally owns no screen detection and no direct clicks. The
TeamStatusMonitor is responsible for observations and the existing MacroEngine is
responsible for a single selected-team Gather attempt. This layer only decides
*when* it is safe to ask for another attempt.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .config import BotConfig
from .team_state import TeamActivity, TeamStateTracker


@dataclass
class ContinuousGatherStatus:
    active: bool = False
    in_flight_team: int | None = None
    successful_dispatches: int = 0
    last_message: str = "Auto Gather stopped"


class ContinuousGatherService:
    """Dispatch whichever configured team is freshly observed as idle."""

    MAX_IDLE_OBSERVATION_AGE_SECONDS = 5.0

    def __init__(
        self,
        config_provider: Callable[[], BotConfig],
        tracker: TeamStateTracker,
        start_team: Callable[[int], bool],
    ) -> None:
        self._config_provider = config_provider
        self.tracker = tracker
        self._start_team = start_team
        self.status = ContinuousGatherStatus()
        self._last_dispatched_team: int | None = None

    def start(self) -> bool:
        if self.status.active:
            self.status.last_message = "Auto Gather is already running"
            return True
        self.status.active = True
        self.status.in_flight_team = None
        self._last_dispatched_team = None
        self.status.last_message = "Watching team status for an available team"
        return True

    def stop(self, message: str = "Auto Gather stopped") -> None:
        self.status.active = False
        self.status.in_flight_team = None
        self.status.last_message = message

    def _configured_teams(self) -> tuple[int, ...]:
        config = self._config_provider()
        teams = getattr(config.gather, "teams_enabled", (1, 2, 3))
        return tuple(int(team) for team in teams if int(team) in {1, 2, 3})

    def next_idle_team(self) -> int | None:
        """Return one fresh visually-idle team, or None when the Bot must wait."""

        if not self.tracker.sidebar_visible:
            return None
        configured = tuple(sorted(set(self._configured_teams())))
        if self._last_dispatched_team in configured:
            pivot = configured.index(self._last_dispatched_team) + 1
            configured = configured[pivot:] + configured[:pivot]
        snapshots = {snapshot.team: snapshot for snapshot in self.tracker.snapshots()}
        for team in configured:
            snapshot = snapshots[team]
            if snapshot.activity != TeamActivity.IDLE:
                continue
            if snapshot.last_seen_age is None:
                continue
            if snapshot.last_seen_age > self.MAX_IDLE_OBSERVATION_AGE_SECONDS:
                continue
            return snapshot.team
        return None

    def try_start_next(self, *, input_engine_busy: bool) -> bool:
        """Start one exact-team attempt when current visual state allows it."""

        if not self.status.active or self.status.in_flight_team is not None:
            return False
        if input_engine_busy:
            self.status.last_message = "Waiting for the current automation to finish"
            return False
        if not self.tracker.sidebar_visible:
            self.status.last_message = "Waiting for a readable world-map team view"
            return False

        team = self.next_idle_team()
        if team is None:
            configured = set(self._configured_teams())
            snapshots = [
                item for item in self.tracker.snapshots() if item.team in configured
            ]
            if any(item.activity == TeamActivity.UNKNOWN for item in snapshots):
                self.status.last_message = (
                    "Waiting for team identity/status confirmation"
                )
            else:
                self.status.last_message = "All configured teams are busy; waiting"
            return False

        if not self._start_team(team):
            self.stop(f"Auto Gather paused: could not start Team {team} dispatch")
            return False

        self.status.in_flight_team = team
        self.status.last_message = f"Dispatching available Team {team}"
        return True

    def complete_attempt(self, *, success: bool) -> None:
        """Record one finished exact-team engine attempt."""

        team = self.status.in_flight_team
        if team is None:
            return
        self.status.in_flight_team = None
        if not success:
            self.stop(
                f"Auto Gather paused: Team {team} dispatch was not confirmed"
            )
            return

        self.tracker.mark_dispatched(team)
        self._last_dispatched_team = team
        self.status.successful_dispatches += 1
        self.status.last_message = (
            f"Team {team} dispatched; watching for the next available team"
        )
