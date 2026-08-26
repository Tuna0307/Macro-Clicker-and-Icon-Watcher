import sys
import types
from copy import deepcopy

try:
    import keyboard  # noqa: F401
except ImportError:
    sys.modules["keyboard"] = types.SimpleNamespace(parse_hotkey=lambda _value: ())

from macro_clicker.bot.adapters import apply_gather_config, apply_rally_config
from macro_clicker.bot.config import GatherConfig, RallyConfig
from macro_clicker.models import Action, Scenario, Step


def _rally_scenario():
    joining = Action(type="click_matching_row")
    selector = Action(type="select_rally_team")
    return Scenario(
        name="Rally gold mob_ 2 team",
        steps=[
            Step(name="Joining", actions=[joining]),
            Step(name="Attack Confirm", actions=[selector]),
        ],
    )


def test_rally_adapter_applies_user_level_settings_without_mutating_source():
    source = _rally_scenario()
    config = RallyConfig(
        min_level=25,
        max_level=80,
        team1_max_level=80,
        team3_max_level=60,
        join_delay=0.4,
    )

    configured = apply_rally_config(source, config)
    joining = configured.steps[0].actions[0]
    selector = configured.steps[1].actions[0]

    assert joining.min_level == 25
    assert joining.max_level == 80
    assert joining.pre_click_delay == 0.4
    assert selector.team1_max_level == 80
    assert selector.team3_max_level == 60
    assert source.steps[0].actions[0].min_level is None


def _gather_scenario():
    gold_select = Action(
        type="click",
        on_condition_index=0,
        offset_x=196,
        offset_y=-348,
    )
    wait = Action(type="wait", seconds=0.15)
    plus = Action(type="click", on_condition_index=0, offset_x=182, offset_y=-74)
    short_wait = Action(type="wait", seconds=0.05)
    search = Action(type="click", on_condition_index=0)
    prepare_actions = [gold_select, wait]
    for _ in range(12):
        prepare_actions.extend([deepcopy(plus), deepcopy(short_wait)])
    prepare_actions.append(search)

    select = Action(type="click")
    select.type = "gather_control"
    select.gather_command = "select_replacement"
    select.gather_replacement_order = [3, 2, 1]
    success = Action(type="click")
    success.type = "gather_control"
    success.gather_command = "record_success"
    success.gather_replacement_order = [3, 2, 1]
    success.gather_target_count = 3

    return Scenario(
        name="Gather Gold",
        steps=[
            Step(name="Gather - Prepare Gold Lv12", actions=prepare_actions),
            Step(name="Gather - No Free March", actions=[select]),
            Step(name="Gather - Success", actions=[success]),
        ],
    )


def test_gather_adapter_changes_start_level_count_and_target_marches():
    scenario = _gather_scenario()
    config = GatherConfig(
        start_level=8,
        march_count=2,
        replacement_order=[3, 1, 2],
    )

    configured = apply_gather_config(scenario, config)
    prepare = configured.steps[0]
    plus_clicks = [
        action
        for action in prepare.actions
        if action.type == "click"
        and action.offset_x == 182
        and action.offset_y == -74
    ]
    assert len(plus_clicks) == 8

    controls = [
        action
        for step in configured.steps
        for action in step.actions
        if action.type == "gather_control"
    ]
    assert all(action.gather_replacement_order == [3, 1, 2] for action in controls)
    success = next(
        action for action in controls if action.gather_command == "record_success"
    )
    assert success.gather_target_count == 2
