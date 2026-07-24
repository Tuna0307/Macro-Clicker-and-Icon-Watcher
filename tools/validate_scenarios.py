"""Validate every saved scenario and all of its referenced template files."""

from __future__ import annotations

import sys
from pathlib import Path

from macro_clicker.models import list_scenarios, load_scenario, validate_scenario
from macro_clicker.project_paths import SCENARIOS_DIR


def validate_all_scenarios(folder: Path = SCENARIOS_DIR) -> list[str]:
    """Load and validate all JSON scenarios in *folder*."""
    scenario_names = list_scenarios(str(folder))
    if not scenario_names:
        raise ValueError(f"No scenario JSON files found in {folder}.")

    for name in scenario_names:
        scenario = load_scenario(name, folder=str(folder))
        validate_scenario(scenario, require_files=True)

    return scenario_names


def main() -> int:
    try:
        scenario_names = validate_all_scenarios()
    except ValueError as exc:
        print(f"Scenario validation failed: {exc}", file=sys.stderr)
        return 1

    print(
        f"Validated {len(scenario_names)} scenario(s) and their referenced "
        "template files: " + ", ".join(scenario_names)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
