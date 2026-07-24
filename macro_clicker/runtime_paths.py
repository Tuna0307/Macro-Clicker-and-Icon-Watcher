"""Writable per-user runtime paths shared by GUI and background components."""

import os

APP_FOLDER_NAME = "Macro Clicker and Icon Watcher"


def _default_user_data_dir():
    override = os.environ.get("MACRO_CLICKER_DATA_DIR")
    if override:
        return os.path.abspath(os.path.expanduser(override))
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_STATE_HOME") or os.path.join(
            os.path.expanduser("~"), ".local", "state"
        )
    return os.path.join(base, APP_FOLDER_NAME)


USER_DATA_DIR = _default_user_data_dir()
LOG_DIR = os.path.join(USER_DATA_DIR, "logs")
DIAGNOSTIC_DIR = os.path.join(LOG_DIR, "diagnostics")
STARTUP_ERROR_LOG = os.path.join(LOG_DIR, "startup_error.log")
INSTANCE_LOCK_PATH = os.path.join(USER_DATA_DIR, "app.lock")
UI_PREFERENCES_PATH = os.path.join(USER_DATA_DIR, "ui_preferences.json")
