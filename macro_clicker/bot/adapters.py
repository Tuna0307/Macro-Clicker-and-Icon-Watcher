"""Translate simple BotConfig settings into the existing proven scenarios."""

from __future__ import annotations

import copy
from typing import Iterable

from ..models import Action, Scenario, load_scenario
from .config import BotConfig, GatherConfig, PositionsConfig, RallyConfig
from .controller import FEATURE_DEVELOPMENT, FEATURE_GATHER, FEATURE_RALLY, FEATURE_SCIENCE


# The three blue idle-Z regions are anchored to the same 1920x1080 dispatch
# panel geometry already proven by Rally's Team 1 / Team 3 selector. Team 2 is
# the middle card. The actual card click is 40px right and ~21px below the
# matched idle icon center, reproducing Rally's dispatch-card click points.
_GATHER_TEAM_IDLE = {
    1: ("templates/Team1Idle.png", [711, 938, 40, 36]),
    2: ("templates/Team2Idle.png", [837, 938, 40, 36]),
    3: ("templates/Team3Idle.png", [963, 938, 40, 36]),
}
_GATHER_REFERENCE_SIZE = [1920, 1080]
_GATHER_CARD_CLICK_OFFSET = (40, 21)
_GATHER_SELECTED_BUSY_STEP = "Gather - Selected Team Busy"
_GATHER_LEVEL_RESET_CLICKS = 15
_GATHER_LEVEL_DECREMENT_OFFSET = (-183, -74)
_GATHER_POST_DISPATCH_DISMISS_OFFSET = (500, -250)


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
    # effective maximum before the Join click. Clamp both to the global max.
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
    return (
        action.type == "wait"
        and abs(float(getattr(action, "seconds", 0.0)) - 0.05) < 1e-9
    )


def _replace_start_level_clicks(actions: Iterable, start_level: int) -> list:
    """Normalize remembered search level, then select the configured start."""

    actions = list(actions)
    increment_indices = [
        index for index, action in enumerate(actions) if _is_level_increment_click(action)
    ]
    if not increment_indices:
        raise ValueError("Gather backend no longer contains the level '+' click pattern.")

    first = increment_indices[0]
    last = increment_indices[-1]
    end = last + 1
    if end < len(actions) and _is_short_level_wait(actions[end]):
        end += 1

    plus_template = copy.deepcopy(actions[first])
    minus_template = copy.deepcopy(plus_template)
    minus_template.offset_x = _GATHER_LEVEL_DECREMENT_OFFSET[0]
    minus_template.offset_y = _GATHER_LEVEL_DECREMENT_OFFSET[1]
    wait_template = None
    if first + 1 < len(actions) and _is_short_level_wait(actions[first + 1]):
        wait_template = copy.deepcopy(actions[first + 1])

    replacement = []
    # Last War remembers the previous resource-search level. Clamp it to the
    # minimum before raising it so a configured Lv3 always starts at Lv3 even
    # when the popup was previously left at Lv9 or Lv12.
    for _ in range(_GATHER_LEVEL_RESET_CLICKS):
        replacement.append(copy.deepcopy(minus_template))
        if wait_template is not None:
            replacement.append(copy.deepcopy(wait_template))
    for _ in range(max(0, int(start_level) - 1)):
        replacement.append(copy.deepcopy(plus_template))
        if wait_template is not None:
            replacement.append(copy.deepcopy(wait_template))
    return actions[:first] + replacement + actions[end:]


def _window_ratio(region: list[int]) -> list[float]:
    width, height = _GATHER_REFERENCE_SIZE
    left, top, region_width, region_height = region
    return [
        left / width,
        top / height,
        region_width / width,
        region_height / height,
    ]


def _selected_team_idle_condition(dispatch_condition, team: int, *, negate=False):
    path, region = _GATHER_TEAM_IDLE[team]
    condition = copy.deepcopy(dispatch_condition)
    condition.template_path = path
    condition.confidence = 0.85
    condition.comparison_template_path = ""
    condition.comparison_margin = 0.03
    condition.match_mode = "static_picture"
    condition.use_grayscale = False
    condition.template_reference_size = list(_GATHER_REFERENCE_SIZE)
    condition.region = list(region)
    condition.region_mode = "window"
    condition.region_ratio = _window_ratio(region)
    condition.region_window_size = list(_GATHER_REFERENCE_SIZE)
    condition.negate = bool(negate)
    return condition


def _apply_selected_team_gather(scenario: Scenario, config: GatherConfig, team: int) -> Scenario:
    team = int(team)
    if team not in {1, 2, 3}:
        raise ValueError(f"Unsupported gathering team: {team!r}")
    if team not in config.teams_enabled:
        raise ValueError(f"Team {team} is not enabled for Auto Gather.")

    resource_found = _step_named(scenario, "Gather - Resource Found")
    no_free = _step_named(scenario, "Gather - No Free March")
    dispatch = _step_named(scenario, "Gather - Dispatch Ready")
    success = _step_named(scenario, "Gather - Success")
    if len(dispatch.conditions) != 1:
        raise ValueError("Gather dispatch backend expected exactly one Dispatch condition.")

    dispatch_button_condition = copy.deepcopy(dispatch.conditions[0])
    idle_condition = _selected_team_idle_condition(dispatch_button_condition, team)

    # Dispatch only when both the normal blue Dispatch button and this exact
    # team's idle Z are present. The first click selects the actual team card;
    # only then can the existing Dispatch click execute.
    dispatch.conditions.append(idle_condition)
    dispatch.condition_operator = "AND"
    old_dispatch_actions = list(dispatch.actions)
    dispatch.actions = [
        Action(
            type="click",
            on_condition_index=1,
            offset_x=_GATHER_CARD_CLICK_OFFSET[0],
            offset_y=_GATHER_CARD_CLICK_OFFSET[1],
        ),
        Action(type="wait", seconds=0.2),
        *old_dispatch_actions[:2],
        Action(
            type="set_step",
            step_name=_GATHER_SELECTED_BUSY_STEP,
            set_enabled=False,
        ),
        *old_dispatch_actions[2:],
    ]

    # The legacy scenario replaced a busy march when no free slot existed.
    # Continuous Bot gathering must never interrupt existing work. Close the
    # dispatch panel and fail closed instead; the visual tracker will decide
    # what is available on a later fresh world-map observation.
    no_free.actions = [
        Action(type="key", key="esc"),
        Action(type="wait", seconds=0.35),
        Action(type="stop"),
    ]

    # The base scenario checks the broad no-free banner before the Dispatch
    # button. During the resource-to-panel transition that banner template can
    # briefly resemble another brown notification and stop a valid attempt.
    # Prefer the stronger exact-team Dispatch AND idle-card proof whenever both
    # steps are enabled in the same cycle. A genuinely full queue still has no
    # Dispatch+idle match and therefore falls through to the fail-closed step.
    scenario.steps.remove(dispatch)
    scenario.steps.insert(scenario.steps.index(no_free), dispatch)

    # A stale world-map observation can say a team was idle just before it
    # becomes busy. Add a runtime-only guard that recognizes Dispatch + absence
    # of this exact team's idle icon, returns to the world map, and stops the
    # one-team attempt rather than letting the game auto-select another team.
    busy_step = copy.deepcopy(dispatch)
    busy_step.name = _GATHER_SELECTED_BUSY_STEP
    busy_step.conditions = [
        copy.deepcopy(dispatch_button_condition),
        _selected_team_idle_condition(dispatch_button_condition, team, negate=True),
    ]
    busy_step.condition_operator = "AND"
    busy_step.actions = [
        Action(type="key", key="esc"),
        Action(type="wait", seconds=0.35),
        Action(type="stop"),
    ]
    busy_step.enabled = False
    busy_step.repeatable = False
    scenario.steps.append(busy_step)

    # Enable the stale-team guard only after a resource was found and the game
    # opens the dispatch panel. This keeps world/search screens unaffected.
    insert_at = next(
        (
            index + 1
            for index, action in enumerate(resource_found.actions)
            if action.type == "set_step"
            and action.step_name == "Gather - Dispatch Ready"
            and action.set_enabled
        ),
        len(resource_found.actions),
    )
    resource_found.actions.insert(
        insert_at,
        Action(
            type="set_step",
            step_name=_GATHER_SELECTED_BUSY_STEP,
            set_enabled=True,
        ),
    )

    # One engine run represents exactly one verified team dispatch. The outer
    # ContinuousGatherService decides when another visually idle team should be
    # attempted, so no one-shot march target or replacement pointer remains.
    for action in _actions_of_type(scenario, "gather_control"):
        if getattr(action, "gather_command", "") == "record_success":
            action.gather_target_count = 1

    record_success_index = next(
        index
        for index, action in enumerate(success.actions)
        if action.type == "gather_control"
        and getattr(action, "gather_command", "") == "record_success"
    )
    # A confirmed dispatch leaves Last War's bottom team-card strip selected.
    # While that strip is open the normal deployment sidebar is hidden, so a
    # trusted world-map anchor plus blank sidebar can look like 0/3 and schedule
    # an incorrect fourth attempt. One neutral ground click dismisses the strip
    # before record_success stops this finite worker and hands control back to
    # the continuous coordinator. Anchor the point to the verified map Search
    # icon so scaling and signed multi-monitor coordinates remain window-relative.
    success.actions[record_success_index:record_success_index] = [
        Action(
            type="click",
            on_condition_index=0,
            offset_x=_GATHER_POST_DISPATCH_DISMISS_OFFSET[0],
            offset_y=_GATHER_POST_DISPATCH_DISMISS_OFFSET[1],
        ),
        Action(type="wait", seconds=0.35),
    ]
    scenario.name = f"{scenario.name} — Team {team}"
    return scenario


def apply_gather_config(
    scenario: Scenario,
    config: GatherConfig,
    *,
    selected_team: int | None = None,
) -> Scenario:
    """Apply Gather settings while preserving the proven search state machine."""

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

    if selected_team is not None:
        scenario = _apply_selected_team_gather(scenario, config, selected_team)
    return scenario


def apply_position_config(scenario: Scenario, config: PositionsConfig) -> Scenario:
    """Apply the normal-user Position retry policy to a runtime scenario copy."""

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


def configured_scenario(
    feature: str,
    config: BotConfig,
    *,
    gather_team: int | None = None,
) -> Scenario:
    """Load a bundled backend scenario and apply the user's simple settings."""

    feature = str(feature).strip().casefold()
    if feature == FEATURE_RALLY:
        scenario = load_scenario(config.rally.scenario_name)
        scenario.target_window_title = config.target_window_title
        return apply_rally_config(scenario, config.rally)
    if feature == FEATURE_GATHER:
        scenario = load_scenario(config.gather.scenario_name)
        scenario.target_window_title = config.target_window_title
        return apply_gather_config(
            scenario,
            config.gather,
            selected_team=gather_team,
        )
    if feature == FEATURE_DEVELOPMENT:
        scenario = load_scenario(config.positions.development_scenario)
        scenario.target_window_title = config.target_window_title
        return apply_position_config(scenario, config.positions)
    if feature == FEATURE_SCIENCE:
        scenario = load_scenario(config.positions.science_scenario)
        scenario.target_window_title = config.target_window_title
        return apply_position_config(scenario, config.positions)
    raise ValueError(f"Unknown bot feature: {feature!r}")
