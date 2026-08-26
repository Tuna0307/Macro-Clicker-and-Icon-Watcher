"""Continuous Auto Gather decisions built on visually observed team state.

This coordinator intentionally owns no screen detection and no direct clicks.  The
TeamStatusMonitor is responsible for observations and the existing MacroEngine is
responsible for a single selected-team Gather attempt.  This layer only decides
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
    """Dispatch whichever configured team is freshly observed as idle.

    There is deliberately no user-facing team priority.  If several teams are
    idle at once, the first idle team in the stable Team 1/2/3 snapshot is used;
    after that dispatch is visually/engine-confirmed it becomes non-idle and the
    next currently idle team can be used.

    A timer reaching zero never makes a team available.  Only TeamStateTracker's
    visual IDLE state can start an attempt.
    """

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

    def start(self) -> bool:
        if self.status.active:
            self.status.last_message = "Auto Gather is already running"
            return True
        self.status.active = True
        self.status.in_flight_team = None
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
        configured = set(self._configured_teams())
        for snapshot in self.tracker.snapshots():
            if snapshot.team not in configured:
                continue
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
            self.status.last_message = "Waiting for the team-status sidebar"
            return False

        team = self.next_idle_team()
        if team is None:
            self.status.last_message = "All configured teams are busy; waiting"
            return False

        if not self._start_team(team):
            self.stop(f"Auto Gather paused: could not start Team {team} dispatch")
            return False

        self.status.in_flight_team = team
        self.status.last_message = f"Dispatching available Team {team}"
        return True

    def complete_attempt(self, *, success: bool) -> None:
        """Record one finished engine attempt.

        A successful attempt immediately marks the selected team travelling so
        a stale sidebar frame cannot dispatch it twice.  A failed exact-team
        verification pauses the service.  That fail-closed behavior also means
        F12 cannot accidentally be followed by an automatic restart.
        """

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
        self.status.successful_dispatches += 1
        self.status.last_message = (
            f"Team {team} dispatched; watching for the next available team"
        )
