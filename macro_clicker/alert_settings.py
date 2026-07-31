"""Validated Icon Alert settings independent of the Tk user interface."""

import json
import math
import os
from dataclasses import asdict, dataclass
from difflib import get_close_matches
from typing import Optional, Tuple

from .atomic_io import atomic_write_json
from .project_paths import ALERT_SETTINGS_PATH


DEFAULT_COOLDOWN_SEC = 5.0
DEFAULT_ALERT_VOLUME = 1.0
DEFAULT_START_STOP_HOTKEY = "ctrl+shift+f8"
DEFAULT_TEST_ALERT_HOTKEY = "ctrl+shift+f9"
SETTINGS_PATH = str(ALERT_SETTINGS_PATH)


@dataclass(eq=True)
class AppSettings:
    monitor_choice: str = "All monitors"
    monitor_unique_id: Optional[str] = None
    grayscale: bool = True
    debug: bool = False
    cooldown_sec: float = DEFAULT_COOLDOWN_SEC
    alert_volume: float = DEFAULT_ALERT_VOLUME
    scan_region: Optional[Tuple[int, int, int, int]] = None
    scan_region_mode: str = "screen"
    scan_region_ratio: Optional[Tuple[float, float, float, float]] = None
    scan_region_window_size: Optional[Tuple[int, int]] = None
    target_window_title: str = ""
    start_stop_hotkey: str = DEFAULT_START_STOP_HOTKEY
    test_alert_hotkey: str = DEFAULT_TEST_ALERT_HOTKEY
    minimize_to_tray: bool = False


_SETTINGS_FIELDS = frozenset(AppSettings.__dataclass_fields__)


def _field_error(errors, field, message):
    errors.append(f"{field}: {message}")


def _unknown_field_error(field):
    suggestion = get_close_matches(field, _SETTINGS_FIELDS, n=1)
    suffix = f"; did you mean '{suggestion[0]}'?" if suggestion else ""
    return f"unknown setting '{field}'{suffix}"


def settings_load_errors(settings):
    """Return non-fatal validation errors attached by :func:`load_settings`."""
    return tuple(getattr(settings, "_load_errors", ()))


def _whole_number_tuple(value, length, positive_size_from=None):
    if value is None or isinstance(value, (str, bytes, dict)):
        return None
    try:
        items = tuple(value)
    except TypeError:
        return None
    if len(items) != length or any(isinstance(item, bool) for item in items):
        return None
    if any(
        isinstance(item, float) and (not math.isfinite(item) or not item.is_integer())
        for item in items
    ):
        return None
    try:
        result = tuple(int(item) for item in items)
    except (TypeError, ValueError, OverflowError):
        return None
    if positive_size_from is not None and any(
        item <= 0 for item in result[positive_size_from:]
    ):
        return None
    return result


def _finite_float_tuple(value, length):
    if value is None or isinstance(value, (str, bytes, dict)):
        return None
    try:
        items = tuple(value)
    except TypeError:
        return None
    if len(items) != length or any(isinstance(item, bool) for item in items):
        return None
    try:
        result = tuple(float(item) for item in items)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if all(math.isfinite(item) for item in result) else None


def load_settings(path=SETTINGS_PATH):
    errors = []
    if not os.path.exists(path):
        return AppSettings()
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError, UnicodeError) as exc:
        settings = AppSettings()
        object.__setattr__(
            settings,
            "_load_errors",
            (f"could not read settings: {exc}",),
        )
        return settings
    if not isinstance(data, dict):
        settings = AppSettings()
        object.__setattr__(
            settings,
            "_load_errors",
            ("settings file must contain a JSON object",),
        )
        return settings

    errors.extend(
        _unknown_field_error(str(key)) for key in data if key not in _SETTINGS_FIELDS
    )

    defaults = AppSettings()
    values = asdict(defaults)
    values.update({key: data[key] for key in values if key in data})

    raw_scan_region = values["scan_region"]
    values["scan_region"] = _whole_number_tuple(
        raw_scan_region, 4, positive_size_from=2
    )
    if raw_scan_region is not None and values["scan_region"] is None:
        _field_error(
            errors,
            "scan_region",
            "must contain four whole numbers with positive width and height",
        )
    raw_region_mode = values["scan_region_mode"]
    if not isinstance(values["scan_region_mode"], str) or values[
        "scan_region_mode"
    ] not in {"screen", "window", "monitor"}:
        values["scan_region_mode"] = "screen"
        _field_error(
            errors,
            "scan_region_mode",
            f"unknown region mode {raw_region_mode!r}",
        )
    raw_ratio = values["scan_region_ratio"]
    ratio = _finite_float_tuple(raw_ratio, 4)
    if raw_ratio is not None and ratio is None:
        _field_error(
            errors,
            "scan_region_ratio",
            "must contain four finite numbers",
        )
    if ratio is not None:
        x, y, width, height = ratio
        if (
            x < 0.0
            or y < 0.0
            or width <= 0.0
            or height <= 0.0
            or x + width > 1.001
            or y + height > 1.001
        ):
            ratio = None
            _field_error(
                errors,
                "scan_region_ratio",
                "must describe a positive box contained in its reference area",
            )
    values["scan_region_ratio"] = ratio
    raw_window_size = values["scan_region_window_size"]
    values["scan_region_window_size"] = _whole_number_tuple(
        raw_window_size, 2, positive_size_from=0
    )
    if raw_window_size is not None and values["scan_region_window_size"] is None:
        _field_error(
            errors,
            "scan_region_window_size",
            "must contain a positive whole-number width and height",
        )
    if (
        values["scan_region"] is not None
        and values["scan_region_mode"] in {"window", "monitor"}
        and (values["scan_region"][0] < 0 or values["scan_region"][1] < 0)
    ):
        _field_error(
            errors,
            "scan_region",
            "window/monitor-relative offsets cannot be negative",
        )
        values["scan_region"] = None
    if values["scan_region_mode"] == "screen" and (
        raw_ratio is not None or raw_window_size is not None
    ):
        _field_error(
            errors,
            "scan_region",
            "screen regions cannot contain relative resize metadata",
        )
    if (
        values["scan_region"] is not None
        and values["scan_region_mode"] in {"window", "monitor"}
        and (ratio is None) != (values["scan_region_window_size"] is None)
    ):
        _field_error(
            errors,
            "scan_region",
            "relative regions need both ratio and reference size",
        )
    if values["scan_region"] is None and (
        raw_ratio is not None or raw_window_size is not None
    ):
        _field_error(
            errors,
            "scan_region",
            "relative resize metadata requires a scan region",
        )
    if (
        values["scan_region"] is None
        or values["scan_region_mode"] == "screen"
        or (values["scan_region_ratio"] is None)
        != (values["scan_region_window_size"] is None)
    ):
        values["scan_region_ratio"] = None
        values["scan_region_window_size"] = None

    try:
        if isinstance(values["cooldown_sec"], bool):
            raise TypeError
        cooldown = float(values["cooldown_sec"])
        if not math.isfinite(cooldown):
            raise ValueError
        values["cooldown_sec"] = max(0.0, cooldown)
    except (TypeError, ValueError, OverflowError):
        values["cooldown_sec"] = defaults.cooldown_sec
        _field_error(errors, "cooldown_sec", "must be a finite number")
    try:
        if isinstance(values["alert_volume"], bool):
            raise TypeError
        volume = float(values["alert_volume"])
        if not math.isfinite(volume):
            raise ValueError
        values["alert_volume"] = min(1.0, max(0.0, volume))
    except (TypeError, ValueError, OverflowError):
        values["alert_volume"] = defaults.alert_volume
        _field_error(errors, "alert_volume", "must be a finite number")

    for key in ("grayscale", "debug", "minimize_to_tray"):
        if not isinstance(values[key], bool):
            values[key] = getattr(defaults, key)
            _field_error(errors, key, "must be true or false")
    for key in (
        "monitor_choice",
        "target_window_title",
        "start_stop_hotkey",
        "test_alert_hotkey",
    ):
        if not isinstance(values[key], str):
            values[key] = getattr(defaults, key)
            _field_error(errors, key, "must be text")
    if (
        values["scan_region_mode"] == "window"
        and not values["target_window_title"].strip()
    ):
        if values["scan_region"] is None:
            # Older UI versions could leave this stale mode behind after the
            # target was cleared. With no relative box, full-screen scanning is
            # the unambiguous behavior already shown by the UI.
            values["scan_region_mode"] = "screen"
        # A non-empty window-relative region without a target is an incomplete
        # but repairable UI state, not malformed data. Preserve the calibrated
        # region so the user can choose a target again. The watcher refuses to
        # start until that target is restored.
    if not values["monitor_choice"].strip():
        values["monitor_choice"] = defaults.monitor_choice
        _field_error(errors, "monitor_choice", "cannot be blank")
    elif values["monitor_choice"] != "All monitors":
        parts = values["monitor_choice"].split()
        if (
            len(parts) != 2
            or parts[0] != "Monitor"
            or not parts[1].isdigit()
            or int(parts[1]) <= 0
        ):
            values["monitor_choice"] = defaults.monitor_choice
            _field_error(
                errors,
                "monitor_choice",
                "must be 'All monitors' or 'Monitor N'",
            )
    raw_monitor_unique_id = values["monitor_unique_id"]
    if raw_monitor_unique_id is None:
        monitor_unique_id = None
    elif isinstance(raw_monitor_unique_id, bool) or not isinstance(
        raw_monitor_unique_id,
        (str, int),
    ):
        monitor_unique_id = None
        _field_error(
            errors,
            "monitor_unique_id",
            "must be non-empty text, an integer, or null",
        )
    else:
        monitor_unique_id = str(raw_monitor_unique_id).strip()
        if not monitor_unique_id:
            monitor_unique_id = None
            _field_error(
                errors,
                "monitor_unique_id",
                "must be non-empty text, an integer, or null",
            )
    if values["monitor_choice"] == "All monitors" and monitor_unique_id is not None:
        monitor_unique_id = None
        _field_error(
            errors,
            "monitor_unique_id",
            "must be null when all monitors are selected",
        )
    values["monitor_unique_id"] = monitor_unique_id
    if not values["start_stop_hotkey"].strip():
        values["start_stop_hotkey"] = defaults.start_stop_hotkey
        _field_error(errors, "start_stop_hotkey", "cannot be blank")
    if not values["test_alert_hotkey"].strip():
        values["test_alert_hotkey"] = defaults.test_alert_hotkey
        _field_error(errors, "test_alert_hotkey", "cannot be blank")
    settings = AppSettings(**values)
    if errors:
        object.__setattr__(settings, "_load_errors", tuple(errors))
    return settings


def save_settings(path, settings):
    data = asdict(settings)
    for key in ("scan_region", "scan_region_ratio", "scan_region_window_size"):
        if data[key] is not None:
            data[key] = list(data[key])
    atomic_write_json(path, data)
