"""Translate simple BotConfig settings into the existing proven scenarios."""

from __future__ import annotations

import copy
from typing import Iterable

from ..models import Action, Scenario, load_scenario
from .config import BotConfig, GatherConfig, PositionsConfig, RallyConfig
from .controller import FEATURE_DEVELOPMENT, FEATURE_GATHER, FEATURE_RALLY, FEATURE_SCIENCE


def _actions_of_type(scenario: Scenario, action_type: str) -> list:
    return [
        action
        for step in scenario.steps
        for action in step.actions
        if action.type == action_type
    ]


def _step_named(scenario: Scenario, name: str):
    for step in scenario.steps:
        if step.name == name:
            return step
    raise ValueError(f"Scenario '{scenario.name}' is missing required step '{name}'.")


def apply_rally_config(scenario: Scenario, config: RallyConfig) -> Scenario:
    """Apply user-facing Rally settings without rewriting Rally implementation."""

    scenario = copy.deepcopy(scenario)
    joining = _step_named(scenario, "Joining")
    row_actions = [a for a in joining.actions if a.type == "click_matching_row"]
    selectors = _actions_of_type(scenario, "select_rally_team")
    if len(row_actions) != 1:
        raise ValueError(
            f"Rally backend expected one Joining row action; found {len(row_actions)}."
        )
    if len(selectors) != 1:
        raise ValueError(
            f"Rally backend expected one team selector; found {len(selectors)}."
        )

    row_action = row_actions[0]
    selector = selectors[0]
    row_action.min_level = int(config.min_level)
    row_action.max_level = int(config.max_level)
    row_action.pre_click_delay = float(config.join_delay)

    # Smart team availability uses the selector's per-team caps as the real
    # effective maximum before the Join click.  Clamp both to the global max.
    selector.team1_max_level = min(int(config.team1_max_level), int(config.max_level))
    selector.team3_max_level = min(int(config.team3_max_level), int(config.max_level))
    return scenario


def _is_level_increment_click(action) -> bool:
    return (
        action.type == "click"
        and getattr(action, "offset_x", None) == 182
        and getattr(action, "offset_y", None) == -74
    )


def _is_short_level_wait(action) -> bool:
    return action.type == "wait" and abs(float(getattr(action, "seconds", 0.0)) - 0.05) < 1e-9


def _replace_start_level_clicks(actions: Iterable, start_level: int) -> list:
    """Replace the repeated '+' click/wait pairs used by the proven Gather flow."""

    actions = list(actions)
    increment_indices = [
        index for index, action in enumerate(actions) if _is_level_increment_click(action)
    ]
    if not increment_indices:
        raise ValueError("Gather backend no longer contains the level '+' click pattern.")

    first = increment_indices[0]
    last = increment_indices[-1]
    # The proven scenario has a 0.05s wait after each plus click. Include the
    # final matching wait in the replaced slice when present.
    end = last + 1
    if end < len(actions) and _is_short_level_wait(actions[end]):
        end += 1

    plus_template = copy.deepcopy(actions[first])
    wait_template = None
    if first + 1 < len(actions) and _is_short_level_wait(actions[first + 1]):
        wait_template = copy.deepcopy(actions[first + 1])

    replacement = []
    for _ in range(int(start_level)):
        replacement.append(copy.deepcopy(plus_template))
        if wait_template is not None:
            replacement.append(copy.deepcopy(wait_template))
    return actions[:first] + replacement + actions[end:]


def apply_gather_config(scenario: Scenario, config: GatherConfig) -> Scenario:
    """Apply Gather settings while preserving the eight-step state machine."""

    scenario = copy.deepcopy(scenario)
    if config.resource.casefold() != "gold":
        raise ValueError("The current Gather backend supports Gold only.")

    prepare = _step_named(scenario, "Gather - Prepare Gold Lv12")
    prepare.actions = _replace_start_level_clicks(prepare.actions, config.start_level)

    controls = _actions_of_type(scenario, "gather_control")
    if not controls:
        raise ValueError("Gather backend is missing its state-controller actions.")
    for action in controls:
        action.gather_replacement_order = list(config.replacement_order)
        if getattr(action, "gather_command", "") == "record_success":
            action.gather_target_count = int(config.march_count)
    return scenario


def apply_position_config(scenario: Scenario, config: PositionsConfig) -> Scenario:
    """Apply the normal-user Position retry policy to a runtime scenario copy.

    The bundled Development/Science scenarios retry by closing the unavailable
    modal/page and then re-enabling their initial Open step. When automatic retry
    is disabled, preserve that cleanup but replace only the final loop-back with
    `stop` so a serialized Bot cycle can safely advance to its next task.
    """

    scenario = copy.deepcopy(scenario)
    if config.retry_automatically:
        return scenario

    retry = _step_named(scenario, "Retry - Apply Unavailable")
    loop_back_indices = [
        index
        for index, action in enumerate(retry.actions)
        if action.type == "set_step"
        and action.set_enabled
        and str(action.step_name).startswith("Open #")
    ]
    if len(loop_back_indices) != 1:
        raise ValueError(
            f"Position backend expected one retry loop-back action; found "
            f"{len(loop_back_indices)}."
        )
    retry.actions[loop_back_indices[0]] = Action(type="stop")
    return scenario


def configured_scenario(feature: str, config: BotConfig) -> Scenario:
    """Load a bundled backend scenario and apply the user's simple settings."""

    feature = str(feature).strip().casefold()
    if feature == FEATURE_RALLY:
        scenario = load_scenario(config.rally.scenario_name)
        scenario.target_window_title = config.target_window_title
        return apply_rally_config(scenario, config.rally)
    if feature == FEATURE_GATHER:
        scenario = load_scenario(config.gather.scenario_name)
        scenario.target_window_title = config.target_window_title
        return apply_gather_config(scenario, config.gather)
    if feature == FEATURE_DEVELOPMENT:
        scenario = load_scenario(config.positions.development_scenario)
        scenario.target_window_title = config.target_window_title
        return apply_position_config(scenario, config.positions)
    if feature == FEATURE_SCIENCE:
        scenario = load_scenario(config.positions.science_scenario)
        scenario.target_window_title = config.target_window_title
        return apply_position_config(scenario, config.positions)
    raise ValueError(f"Unknown bot feature: {feature!r}")
