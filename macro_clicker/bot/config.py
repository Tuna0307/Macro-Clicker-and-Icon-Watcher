"""User-facing configuration for the dedicated bot interface.

The advanced Scenario/Step model remains the automation implementation.  This
module contains only settings a normal bot user should have to understand.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import asdict, dataclass, field
from typing import Any

from ..atomic_io import atomic_write_json
from ..runtime_paths import BOT_CONFIG_PATH

BOT_CONFIG_VERSION = 1


@dataclass
class RallyConfig:
    enabled: bool = True
    scenario_name: str = "Rally gold mob_ 2 team"
    min_level: int = 1
    max_level: int = 70
    team1_max_level: int = 70
    team3_max_level: int = 60
    join_delay: float = 0.0


@dataclass
class GatherConfig:
    enabled: bool = False
    scenario_name: str = "Gather Gold"
    resource: str = "Gold"
    start_level: int = 12
    march_count: int = 3
    replacement_order: list[int] = field(default_factory=lambda: [3, 2, 1])
    search_until_found: bool = True


@dataclass
class PositionsConfig:
    development_enabled: bool = False
    science_enabled: bool = False
    development_scenario: str = "Apply Development Position"
    science_scenario: str = "Apply Science Position"


@dataclass
class AlertsConfig:
    enabled: bool = False
    digs_enabled: bool = True
    secret_task_enabled: bool = True
    sound_enabled: bool = True
    volume_percent: int = 70


@dataclass
class ScheduleConfig:
    enabled: bool = False
    start_time: str = "06:00"
    stop_time: str = "23:00"
    days: list[str] = field(
        default_factory=lambda: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    )


@dataclass
class BotConfig:
    version: int = BOT_CONFIG_VERSION
    target_window_title: str = "Last War-Survival Game"
    rally: RallyConfig = field(default_factory=RallyConfig)
    gather: GatherConfig = field(default_factory=GatherConfig)
    positions: PositionsConfig = field(default_factory=PositionsConfig)
    alerts: AlertsConfig = field(default_factory=AlertsConfig)
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class BotConfigError(ValueError):
    """Raised when user-facing bot settings are internally inconsistent."""


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _bool(value: Any, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def _int(value: Any, default: int, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return min(maximum, max(minimum, parsed))


def _float(value: Any, default: float, *, minimum: float, maximum: float) -> float:
    if isinstance(value, bool):
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    if not math.isfinite(parsed):
        return default
    return min(maximum, max(minimum, parsed))


def _text(value: Any, default: str) -> str:
    if not isinstance(value, str):
        return default
    stripped = value.strip()
    return stripped or default


def _clock(value: Any, default: str) -> str:
    if not isinstance(value, str):
        return default
    text = value.strip()
    try:
        hour_text, minute_text = text.split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
    except (ValueError, TypeError):
        return default
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return default
    return f"{hour:02d}:{minute:02d}"


def _parse_replacement_order(value: Any) -> list[int]:
    if isinstance(value, str):
        chunks = value.replace("→", ",").replace("->", ",").split(",")
        value = [chunk.strip() for chunk in chunks if chunk.strip()]
    if not isinstance(value, (list, tuple)):
        return [3, 2, 1]
    parsed: list[int] = []
    for item in value:
        if isinstance(item, bool):
            continue
        try:
            march = int(item)
        except (TypeError, ValueError, OverflowError):
            continue
        if march in {1, 2, 3} and march not in parsed:
            parsed.append(march)
    return parsed if len(parsed) == 3 and set(parsed) == {1, 2, 3} else [3, 2, 1]


def _parse_days(value: Any) -> list[str]:
    valid = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
    if not isinstance(value, (list, tuple)):
        return list(valid)
    result = [day for day in valid if day in value]
    return result or list(valid)


def bot_config_from_dict(data: Any) -> BotConfig:
    """Load a tolerant config while keeping dangerous values bounded."""

    root = _as_dict(data)
    defaults = BotConfig()
    rally_data = _as_dict(root.get("rally"))
    gather_data = _as_dict(root.get("gather"))
    positions_data = _as_dict(root.get("positions"))
    alerts_data = _as_dict(root.get("alerts"))
    schedule_data = _as_dict(root.get("schedule"))

    rally = RallyConfig(
        enabled=_bool(rally_data.get("enabled"), defaults.rally.enabled),
        scenario_name=_text(
            rally_data.get("scenario_name"), defaults.rally.scenario_name
        ),
        min_level=_int(
            rally_data.get("min_level"),
            defaults.rally.min_level,
            minimum=0,
            maximum=999,
        ),
        max_level=_int(
            rally_data.get("max_level"),
            defaults.rally.max_level,
            minimum=0,
            maximum=999,
        ),
        team1_max_level=_int(
            rally_data.get("team1_max_level"),
            defaults.rally.team1_max_level,
            minimum=0,
            maximum=999,
        ),
        team3_max_level=_int(
            rally_data.get("team3_max_level"),
            defaults.rally.team3_max_level,
            minimum=0,
            maximum=999,
        ),
        join_delay=_float(
            rally_data.get("join_delay"),
            defaults.rally.join_delay,
            minimum=0.0,
            maximum=30.0,
        ),
    )
    if rally.max_level < rally.min_level:
        rally.max_level = rally.min_level
    rally.team1_max_level = min(
        rally.max_level,
        max(rally.min_level, rally.team1_max_level),
    )
    rally.team3_max_level = min(
        rally.max_level,
        max(rally.min_level, rally.team3_max_level),
    )

    gather = GatherConfig(
        enabled=_bool(gather_data.get("enabled"), defaults.gather.enabled),
        scenario_name=_text(
            gather_data.get("scenario_name"), defaults.gather.scenario_name
        ),
        resource=_text(gather_data.get("resource"), defaults.gather.resource),
        start_level=_int(
            gather_data.get("start_level"),
            defaults.gather.start_level,
            minimum=1,
            maximum=99,
        ),
        march_count=_int(
            gather_data.get("march_count"),
            defaults.gather.march_count,
            minimum=1,
            maximum=3,
        ),
        replacement_order=_parse_replacement_order(
            gather_data.get("replacement_order")
        ),
        search_until_found=_bool(
            gather_data.get("search_until_found"), defaults.gather.search_until_found
        ),
    )

    positions = PositionsConfig(
        development_enabled=_bool(
            positions_data.get("development_enabled"),
            defaults.positions.development_enabled,
        ),
        science_enabled=_bool(
            positions_data.get("science_enabled"), defaults.positions.science_enabled
        ),
        development_scenario=_text(
            positions_data.get("development_scenario"),
            defaults.positions.development_scenario,
        ),
        science_scenario=_text(
            positions_data.get("science_scenario"),
            defaults.positions.science_scenario,
        ),
    )

    alerts = AlertsConfig(
        enabled=_bool(alerts_data.get("enabled"), defaults.alerts.enabled),
        digs_enabled=_bool(
            alerts_data.get("digs_enabled"), defaults.alerts.digs_enabled
        ),
        secret_task_enabled=_bool(
            alerts_data.get("secret_task_enabled"), defaults.alerts.secret_task_enabled
        ),
        sound_enabled=_bool(
            alerts_data.get("sound_enabled"), defaults.alerts.sound_enabled
        ),
        volume_percent=_int(
            alerts_data.get("volume_percent"),
            defaults.alerts.volume_percent,
            minimum=0,
            maximum=100,
        ),
    )

    schedule = ScheduleConfig(
        enabled=_bool(schedule_data.get("enabled"), defaults.schedule.enabled),
        start_time=_clock(schedule_data.get("start_time"), defaults.schedule.start_time),
        stop_time=_clock(schedule_data.get("stop_time"), defaults.schedule.stop_time),
        days=_parse_days(schedule_data.get("days")),
    )

    return BotConfig(
        version=BOT_CONFIG_VERSION,
        target_window_title=_text(
            root.get("target_window_title"), defaults.target_window_title
        ),
        rally=rally,
        gather=gather,
        positions=positions,
        alerts=alerts,
        schedule=schedule,
    )


def validate_bot_config(config: BotConfig) -> None:
    if config.rally.max_level < config.rally.min_level:
        raise BotConfigError("Rally maximum level cannot be below the minimum level.")
    for label, value in (
        ("Team 1 max level", config.rally.team1_max_level),
        ("Team 3 max level", config.rally.team3_max_level),
    ):
        if value < config.rally.min_level:
            raise BotConfigError(f"{label} cannot be below the Rally minimum level.")
        if value > config.rally.max_level:
            raise BotConfigError(f"{label} cannot exceed the Rally maximum level.")
    if config.gather.resource.casefold() != "gold":
        raise BotConfigError(
            "Only Gold gathering is implemented in the current backend."
        )
    if len(set(config.gather.replacement_order)) != len(
        config.gather.replacement_order
    ):
        raise BotConfigError("Gather replacement order cannot contain duplicates.")
    if set(config.gather.replacement_order) != {1, 2, 3}:
        raise BotConfigError(
            "Gather replacement order must contain marches 1, 2, and 3 exactly once."
        )
    for label, value in (
        ("start", config.schedule.start_time),
        ("stop", config.schedule.stop_time),
    ):
        if _clock(value, "__invalid__") != value:
            raise BotConfigError(f"Schedule {label} time must use 24-hour HH:MM format.")
    valid_days = {"Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"}
    if not config.schedule.days or any(
        day not in valid_days for day in config.schedule.days
    ):
        raise BotConfigError("Schedule must contain at least one valid weekday.")


def load_bot_config(path: str = BOT_CONFIG_PATH) -> BotConfig:
    if not os.path.exists(path):
        return BotConfig()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError, UnicodeError):
        return BotConfig()
    return bot_config_from_dict(data)


def save_bot_config(config: BotConfig, path: str = BOT_CONFIG_PATH) -> str:
    validate_bot_config(config)
    atomic_write_json(path, config.to_dict())
    return path
