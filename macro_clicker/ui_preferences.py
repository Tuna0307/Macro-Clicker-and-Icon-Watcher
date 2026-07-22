"""Small, global interface preferences stored in the per-user data folder."""

import json
import os
from dataclasses import asdict, dataclass

from .atomic_io import atomic_write_json
from .runtime_paths import UI_PREFERENCES_PATH


@dataclass(eq=True)
class UiPreferences:
    sounds_enabled: bool = True
    animations_enabled: bool = True


def load_ui_preferences(path=UI_PREFERENCES_PATH):
    defaults = UiPreferences()
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError, UnicodeError):
        return defaults
    if not isinstance(data, dict):
        return defaults
    values = asdict(defaults)
    for key, default in values.items():
        value = data.get(key, default)
        values[key] = value if isinstance(value, bool) else default
    return UiPreferences(**values)


def save_ui_preferences(preferences, path=UI_PREFERENCES_PATH):
    if not isinstance(preferences, UiPreferences):
        raise TypeError("preferences must be UiPreferences")
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    atomic_write_json(path, asdict(preferences))
