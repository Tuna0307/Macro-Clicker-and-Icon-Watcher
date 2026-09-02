"""Pure rally-team membership, level-cap, and selection policy."""

from __future__ import annotations

from collections.abc import Mapping

RALLY_TEAM_IDLE = "IDLE"
RALLY_TEAM_BUSY = "BUSY"
RALLY_TEAM_UNKNOWN = "UNKNOWN"

LEGACY_RALLY_TEAM_PRIORITY = (3, 1)
THREE_TEAM_RALLY_PRIORITY = (3, 2, 1)
VALID_RALLY_TEAM_NUMBERS = frozenset((1, 2, 3))
RALLY_TEAM_LEVEL_CAP_UNBOUNDED = "unbounded"


def validate_rally_team_priority(priority: object) -> list[int] | None:
    """Validate persisted priority while preserving ``None`` as legacy mode."""
    if priority is None:
        return None
    if not isinstance(priority, list):
        raise ValueError("team_priority must be a non-empty array of team numbers")
    if not priority:
        raise ValueError("team_priority must not be empty")
    if any(isinstance(team, bool) or not isinstance(team, int) for team in priority):
        raise ValueError("team_priority must contain only team numbers 1, 2, or 3")
    invalid = [team for team in priority if team not in VALID_RALLY_TEAM_NUMBERS]
    if invalid:
        raise ValueError("team_priority must contain only team numbers 1, 2, or 3")
    if len(set(priority)) != len(priority):
        raise ValueError("team_priority must not contain duplicates")
    return list(priority)


def effective_rally_team_priority(priority: object) -> tuple[int, ...]:
    """Return the configured membership/order, defaulting old actions to [3, 1]."""
    validated = validate_rally_team_priority(priority)
    if validated is None:
        return LEGACY_RALLY_TEAM_PRIORITY
    return tuple(validated)


def _eligible_idle_teams(
    statuses: Mapping[int, str],
    limits: Mapping[int, int | None],
    priority: object,
) -> list[tuple[int, int | None]]:
    eligible = []
    for team_number in effective_rally_team_priority(priority):
        # Missing status or limit evidence fails closed. BUSY and UNKNOWN remain
        # distinct in the caller's status mapping but are equally ineligible.
        if statuses.get(team_number) != RALLY_TEAM_IDLE or team_number not in limits:
            continue
        maximum = limits[team_number]
        if maximum is not None and (
            isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 0
        ):
            raise ValueError(
                f"Team {team_number} maximum level must be a non-negative integer or None"
            )
        eligible.append((team_number, maximum))
    return eligible


def available_rally_team_level_cap(
    statuses: Mapping[int, str],
    limits: Mapping[int, int | None],
    priority: object = None,
) -> int | str | None:
    """Return the highest useful level, ``unbounded``, or ``None`` if unavailable."""
    idle_teams = _eligible_idle_teams(statuses, limits, priority)
    if not idle_teams:
        return None
    if any(maximum is None for _team_number, maximum in idle_teams):
        return RALLY_TEAM_LEVEL_CAP_UNBOUNDED
    finite_limits = [
        maximum for _team_number, maximum in idle_teams if maximum is not None
    ]
    return max(finite_limits)


def eligible_rally_teams_for_level(
    level: int,
    statuses: Mapping[int, str],
    limits: Mapping[int, int | None],
    priority: object = None,
) -> tuple[int, ...]:
    """Return capable IDLE teams by smallest capacity, then configured priority."""
    if isinstance(level, bool) or not isinstance(level, int) or level < 0:
        raise ValueError("rally level must be a non-negative integer")
    capable = [
        (team_number, maximum)
        for team_number, maximum in _eligible_idle_teams(statuses, limits, priority)
        if maximum is None or level <= maximum
    ]
    # A lower maximum represents the least-capacity team that can safely handle
    # this level. Stable sorting preserves configured priority for equal limits;
    # unlimited teams sort after every finite capable team.
    capable.sort(
        key=lambda candidate: (
            candidate[1] is None,
            candidate[1] if candidate[1] is not None else 0,
        )
    )
    return tuple(team_number for team_number, _maximum in capable)


def select_rally_team_for_level(
    level: int,
    statuses: Mapping[int, str],
    limits: Mapping[int, int | None],
    priority: object = None,
) -> int | None:
    """Choose the least-capacity eligible team, using priority to break ties."""
    eligible = eligible_rally_teams_for_level(level, statuses, limits, priority)
    return eligible[0] if eligible else None
