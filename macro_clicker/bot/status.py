"""Read-only normal-user status summaries for the dedicated Bot UI."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from .config import BotConfig
from .controller import (
    FEATURE_DEVELOPMENT,
    FEATURE_LABELS,
    FEATURE_RALLY,
    FEATURE_SCIENCE,
)
from .team_state import TeamActivity, format_remaining


@dataclass(frozen=True)
class DashboardSnapshot:
    overall: str
    current_task: str
    next_task: str
    last_status: str
    rally: str
    gather: str
    positions: str
    alerts: str
    schedule: str
    team_status: dict[int, str]


def _latest_fired_step(engine: Any) -> str | None:
    fired = getattr(engine, "_last_fired", None)
    if not isinstance(fired, dict):
        return None
    best_name = None
    best_time = 0.0
    for name, value in fired.items():
        try:
            timestamp = float(value)
        except (TypeError, ValueError):
            continue
        if timestamp > best_time:
            best_time = timestamp
            best_name = str(name)
    return best_name


def _next_task(pending_features: Iterable[str]) -> str:
    for feature in pending_features:
        return FEATURE_LABELS.get(feature, feature.title())
    return "None"


def _rally_status(config: BotConfig, active_feature: str | None, engine: Any) -> str:
    if active_feature != FEATURE_RALLY:
        if not config.rally.enabled:
            return "Disabled"
        return f"Enabled — eligible Lv{config.rally.min_level}–{config.rally.max_level}"

    selected = getattr(engine, "_pending_rally_team_selected", None)
    if isinstance(selected, dict):
        level = selected.get("level")
        team = selected.get("team")
        if level is not None and team is not None:
            return f"Running — Lv{level} with Team {team}"
    pending_level = getattr(engine, "_pending_rally_level", None)
    if pending_level is not None:
        return f"Running — evaluating Lv{pending_level}"
    latest_step = _latest_fired_step(engine)
    if latest_step == "Back if no slot":
        return "Running — no slot, returning to rally list"
    if latest_step == "Back if wrong mob":
        return "Running — recovering from wrong target"
    return f"Running — monitoring Lv{config.rally.min_level}–{config.rally.max_level} mobs"


def _gather_phase(engine: Any, config: BotConfig) -> str:
    latest_step = _latest_fired_step(engine)
    phases = {
        "Gather - Open Search": "opening resource search",
        "Gather - Prepare Gold Lv12": f"searching Gold from Lv{config.gather.start_level}",
        "Gather - Search unavailable": "lowering level and searching again",
        "Gather - Resource Found": "resource found",
        "Gather - No Free March": "game reports no free march",
        "Gather - Dispatch Ready": "selecting team and dispatching",
        "Gather - Selected Team Busy": "selected team changed to busy",
        "Gather - Resource Taken": "resource taken — retrying",
        "Gather - Success": "verifying successful dispatch",
    }
    return phases.get(latest_step, "searching for Gold")


def _team_status_map(team_tracker: Any) -> dict[int, str]:
    if team_tracker is None:
        return {1: "Not monitored", 2: "Not monitored", 3: "Not monitored"}
    result: dict[int, str] = {}
    try:
        snapshots = team_tracker.snapshots()
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return {1: "Unknown", 2: "Unknown", 3: "Unknown"}
    labels = {
        TeamActivity.IDLE: "Idle",
        TeamActivity.TRAVELLING: "Travelling",
        TeamActivity.GATHERING: "Gathering",
        TeamActivity.RETURNING: "Returning",
        TeamActivity.RALLYING: "Rallying",
        TeamActivity.BUSY: "Busy",
        TeamActivity.UNKNOWN: "Unknown",
    }
    for snapshot in snapshots:
        text = labels.get(snapshot.activity, str(snapshot.activity).title())
        if snapshot.activity not in {TeamActivity.IDLE, TeamActivity.UNKNOWN}:
            if snapshot.remaining_seconds is not None:
                text += f" — {format_remaining(snapshot.remaining_seconds)}"
        if not snapshot.sidebar_visible and snapshot.activity != TeamActivity.UNKNOWN:
            text += " — last known"
        result[int(snapshot.team)] = text
    for team in (1, 2, 3):
        result.setdefault(team, "Unknown")
    return result


def _gather_status(
    config: BotConfig,
    engine: Any,
    team_tracker: Any,
    continuous_gather: Any,
) -> str:
    teams = ", ".join(f"Team {team}" for team in config.gather.teams_enabled)
    service_status = getattr(continuous_gather, "status", None)
    active = bool(getattr(service_status, "active", False))
    in_flight = getattr(service_status, "in_flight_team", None)
    if not active:
        if not config.gather.enabled:
            return "Disabled"
        return f"Enabled — Gold Lv{config.gather.start_level} — {teams}"

    if in_flight is not None:
        return f"Running — Team {in_flight} — {_gather_phase(engine, config)}"
    if team_tracker is None or not bool(getattr(team_tracker, "sidebar_visible", False)):
        return "Watching — waiting for a readable world-map team view"

    try:
        idle = team_tracker.available_teams(config.gather.teams_enabled)
    except (AttributeError, TypeError, ValueError):
        idle = ()
    if idle:
        names = ", ".join(f"Team {team}" for team in idle)
        return f"Watching — available: {names}"

    try:
        all_snapshots = [
            item
            for item in team_tracker.snapshots()
            if item.team in config.gather.teams_enabled
        ]
    except (AttributeError, TypeError, ValueError):
        all_snapshots = []
    if any(item.activity == TeamActivity.UNKNOWN for item in all_snapshots):
        return "Watching — waiting for team identity/status confirmation"

    snapshots = [
        item
        for item in all_snapshots
        if item.remaining_seconds is not None and item.remaining_seconds > 0
    ]
    if snapshots:
        soonest = min(snapshots, key=lambda item: item.remaining_seconds)
        return (
            "Waiting — all configured teams busy — next timer: "
            f"Team {soonest.team} {format_remaining(soonest.remaining_seconds)}"
        )
    return "Waiting — all configured teams busy"


def _positions_status(config: BotConfig, active_feature: str | None) -> str:
    retry = "auto retry on" if config.positions.retry_automatically else "auto retry off"
    if active_feature == FEATURE_DEVELOPMENT:
        return f"Running — Development Position — {retry}"
    if active_feature == FEATURE_SCIENCE:
        return f"Running — Science Position — {retry}"
    enabled = []
    if config.positions.development_enabled:
        enabled.append("Development")
    if config.positions.science_enabled:
        enabled.append("Science")
    if not enabled:
        return "Disabled"
    return "Enabled — " + " + ".join(enabled) + f" — {retry}"


def _alerts_status(config: BotConfig, alerts_running: bool) -> str:
    groups = []
    if config.alerts.digs_enabled:
        groups.append("Digs")
    if config.alerts.secret_task_enabled:
        groups.append("Secret Task")
    detail = " + ".join(groups) if groups else "no alert groups enabled"
    if alerts_running:
        return f"Watching — {detail}"
    if config.alerts.enabled:
        return f"Enabled — idle — {detail}"
    return "Disabled"


def _schedule_status(config: BotConfig) -> str:
    schedule = config.schedule
    if not schedule.enabled:
        return "Disabled"
    days = " ".join(schedule.days)
    return f"Active — {schedule.start_time}–{schedule.stop_time} — {days}"


def _live_last_status(
    config: BotConfig,
    active_feature: str | None,
    engine: Any,
    fallback: str,
) -> str:
    latest_step = _latest_fired_step(engine)
    if not latest_step:
        return fallback
    if active_feature == FEATURE_RALLY:
        selected = getattr(engine, "_pending_rally_team_selected", None)
        if isinstance(selected, dict):
            level = selected.get("level")
            team = selected.get("team")
            if level is not None and team is not None:
                return f"Selected Team {team} for Lv{level} rally"
        labels = {
            "Joining": "Rally join attempt",
            "Back if no slot": "No rally slot; returned to the rally list",
            "Back if wrong mob": "Wrong target; returned to the rally list",
        }
        return labels.get(latest_step, fallback)
    if active_feature == FEATURE_DEVELOPMENT:
        if latest_step == "Complete - Apply Available":
            return "Development Position application is available"
        if latest_step == "Retry - Apply Unavailable":
            return (
                "Development Position unavailable; retrying"
                if config.positions.retry_automatically
                else "Development Position unavailable; finishing this task"
            )
    if active_feature == FEATURE_SCIENCE:
        if latest_step == "Complete - Apply Available":
            return "Science Position application is available"
        if latest_step == "Retry - Apply Unavailable":
            return (
                "Science Position unavailable; retrying"
                if config.positions.retry_automatically
                else "Science Position unavailable; finishing this task"
            )
    return fallback


def build_dashboard_snapshot(
    *,
    config: BotConfig,
    controller: Any,
    engine: Any = None,
    alerts_running: bool = False,
    team_tracker: Any = None,
    continuous_gather: Any = None,
) -> DashboardSnapshot:
    """Build one safe, read-only Dashboard snapshot from current runtime state."""

    status = getattr(controller, "status", None)
    active_feature = getattr(status, "active_feature", None)
    controller_message = str(getattr(status, "last_message", "Ready"))
    pending = getattr(controller, "pending_features", ())
    gather_status = getattr(continuous_gather, "status", None)
    gather_active = bool(getattr(gather_status, "active", False))
    gather_in_flight = getattr(gather_status, "in_flight_team", None)

    labels = []
    if active_feature:
        labels.append(FEATURE_LABELS.get(active_feature, str(active_feature).title()))
    elif gather_in_flight is not None:
        labels.append(f"Auto Gather — Team {gather_in_flight}")
    elif gather_active:
        labels.append("Auto Gather")
    if alerts_running:
        labels.append("Alerts")
    overall = "● Running — " + " + ".join(labels) if labels else "● Stopped"

    if active_feature:
        current_task = FEATURE_LABELS.get(active_feature, str(active_feature).title())
    elif gather_active:
        current_task = "Auto Gather"
    else:
        current_task = "None"

    next_task = _next_task(pending)
    if next_task == "None" and active_feature is not None and gather_active:
        next_task = "Auto Gather"

    if active_feature is not None:
        last_status = _live_last_status(config, active_feature, engine, controller_message)
    elif gather_active:
        last_status = str(getattr(gather_status, "last_message", "Watching team status"))
    else:
        last_status = controller_message

    return DashboardSnapshot(
        overall=overall,
        current_task=current_task,
        next_task=next_task,
        last_status=last_status,
        rally=_rally_status(config, active_feature, engine),
        gather=_gather_status(config, engine, team_tracker, continuous_gather),
        positions=_positions_status(config, active_feature),
        alerts=_alerts_status(config, alerts_running),
        schedule=_schedule_status(config),
        team_status=_team_status_map(team_tracker),
    )
