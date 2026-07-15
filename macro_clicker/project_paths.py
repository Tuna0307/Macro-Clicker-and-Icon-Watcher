"""Stable paths to project-owned settings and image assets."""

from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parent
SCENARIOS_DIR = PROJECT_ROOT / "scenarios"
MACRO_TEMPLATES_DIR = PROJECT_ROOT / "templates"
ALERTS_DIR = PROJECT_ROOT / "alerts"
ALERT_TEMPLATES_DIR = ALERTS_DIR / "templates"
ALERT_MANIFEST_PATH = ALERT_TEMPLATES_DIR / "manifest.json"
ALERT_SETTINGS_PATH = ALERTS_DIR / "settings.json"

