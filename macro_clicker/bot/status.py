"""Read-only normal-user status summaries for the dedicated Bot UI.

This module deliberately does not drive the automation. It translates the
controller/config and already-existing engine state into concise, user-facing
status text for the Dashboard.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from .config import BotConfig
from .controller import (
    FEATURE_DEVELOPMENT,
    FEATURE_GATHER,
    FEATURE_LABELS,
    FEATURE_RALLY,
    FEATURE_SCIENCE,
)


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
    if latest_step == "Joining":
        return (
            f"Running — scanning eligible Lv{config.rally.min_level}–"
            f"{config.rally.max_level} mobs"
        )
    return (
        f"Running — monitoring Lv{config.rally.min_level}–"
        f"{config.rally.max_level} mobs"
    )


def _gather_phase(engine: Any, config: BotConfig) -> str:
    latest_step = _latest_fired_step(engine)
    phases = {
        "Gather - Open Search": "opening resource search",
        "Gather - Prepare Gold Lv12": (
            f"searching Gold from Lv{config.gather.start_level}"
        ),
        "Gather - Search unavailable": "lowering level and searching again",
        "Gather - Resource Found": "resource found",
        "Gather - No Free March": "all marches busy",
        "Gather - Dispatch Ready": "dispatching march",
        "Gather - Resource Taken": "resource taken — retrying",
        "Gather - Success": "verifying successful dispatch",
    }
    return phases.get(latest_step, "searching for Gold")


def _gather_status(config: BotConfig, active_feature: str | None, engine: Any) -> str:
    target = int(config.gather.march_count)
    if active_feature != FEATURE_GATHER:
        if not config.gather.enabled:
            return "Disabled"
        return (
            f"Enabled — Gold Lv{config.gather.start_level}, "
            f"{target} march{'es' if target != 1 else ''}"
        )

    controller = getattr(engine, "_gather_controller", None)
    try:
        successful = max(0, int(getattr(controller, "successful_dispatches", 0)))
    except (TypeError, ValueError):
        successful = 0

    next_replacement = None
    current_replacement = getattr(controller, "current_replacement", None)
    if callable(current_replacement):
        try:
            next_replacement = current_replacement(config.gather.replacement_order)
        except (TypeError, ValueError):
            next_replacement = None

    text = f"Running — {successful}/{target} successful — {_gather_phase(engine, config)}"
    if successful < target and next_replacement is not None:
        text += f" — next busy replacement: March {next_replacement}"
    return text


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
    """Translate the latest already-fired backend step into normal-user wording."""

    latest_step = _latest_fired_step(engine)
    if not latest_step:
        return fallback

    if active_feature == FEATURE_GATHER:
        labels = {
            "Gather - Open Search": "Opened resource search",
            "Gather - Prepare Gold Lv12": "Started Gold search",
            "Gather - Search unavailable": "Lowered resource level and searched again",
            "Gather - Resource Found": "Found a Gold resource",
            "Gather - No Free March": "No free march; selecting a busy march to replace",
            "Gather - Dispatch Ready": "Clicked Dispatch",
            "Gather - Resource Taken": "Resource was taken; retrying",
            "Gather - Success": "Verified a gathering dispatch",
        }
        return labels.get(latest_step, fallback)

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
) -> DashboardSnapshot:
    """Build one safe, read-only Dashboard snapshot from current runtime state."""

    status = getattr(controller, "status", None)
    active_feature = getattr(status, "active_feature", None)
    last_message = str(getattr(status, "last_message", "Ready"))
    pending = getattr(controller, "pending_features", ())

    labels = []
    if active_feature:
        labels.append(FEATURE_LABELS.get(active_feature, str(active_feature).title()))
    if alerts_running:
        labels.append("Alerts")
    overall = "● Running — " + " + ".join(labels) if labels else "● Stopped"

    return DashboardSnapshot(
        overall=overall,
        current_task=(
            FEATURE_LABELS.get(active_feature, str(active_feature).title())
            if active_feature
            else "None"
        ),
        next_task=_next_task(pending),
        last_status=_live_last_status(config, active_feature, engine, last_message),
        rally=_rally_status(config, active_feature, engine),
        gather=_gather_status(config, active_feature, engine),
        positions=_positions_status(config, active_feature),
        alerts=_alerts_status(config, alerts_running),
        schedule=_schedule_status(config),
    )
