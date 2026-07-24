"""Validated Icon Alert settings independent of the Tk user interface."""

import json
import math
import os
from dataclasses import asdict, dataclass
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
    if not os.path.exists(path):
        return AppSettings()
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError, UnicodeError):
        return AppSettings()
    if not isinstance(data, dict):
        return AppSettings()

    defaults = AppSettings()
    values = asdict(defaults)
    values.update({key: data[key] for key in values if key in data})

    values["scan_region"] = _whole_number_tuple(
        values["scan_region"], 4, positive_size_from=2
    )
    if values["scan_region_mode"] not in {"screen", "window", "monitor"}:
        values["scan_region_mode"] = "screen"
    ratio = _finite_float_tuple(values["scan_region_ratio"], 4)
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
    values["scan_region_ratio"] = ratio
    values["scan_region_window_size"] = _whole_number_tuple(
        values["scan_region_window_size"], 2, positive_size_from=0
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
        cooldown = float(values["cooldown_sec"])
        values["cooldown_sec"] = (
            max(0.0, cooldown) if math.isfinite(cooldown) else defaults.cooldown_sec
        )
    except (TypeError, ValueError, OverflowError):
        values["cooldown_sec"] = defaults.cooldown_sec
    try:
        volume = float(values["alert_volume"])
        values["alert_volume"] = (
            min(1.0, max(0.0, volume))
            if math.isfinite(volume)
            else defaults.alert_volume
        )
    except (TypeError, ValueError, OverflowError):
        values["alert_volume"] = defaults.alert_volume

    for key in ("grayscale", "debug", "minimize_to_tray"):
        if not isinstance(values[key], bool):
            values[key] = getattr(defaults, key)
    for key in (
        "monitor_choice",
        "target_window_title",
        "start_stop_hotkey",
        "test_alert_hotkey",
    ):
        if not isinstance(values[key], str):
            values[key] = getattr(defaults, key)
    if not values["monitor_choice"].strip():
        values["monitor_choice"] = defaults.monitor_choice
    if not values["start_stop_hotkey"].strip():
        values["start_stop_hotkey"] = defaults.start_stop_hotkey
    if not values["test_alert_hotkey"].strip():
        values["test_alert_hotkey"] = defaults.test_alert_hotkey
    return AppSettings(**values)


def save_settings(path, settings):
    data = asdict(settings)
    for key in ("scan_region", "scan_region_ratio", "scan_region_window_size"):
        if data[key] is not None:
            data[key] = list(data[key])
    atomic_write_json(path, data)
