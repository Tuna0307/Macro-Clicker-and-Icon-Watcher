import sys
import types
from copy import deepcopy

import cv2

try:
    import keyboard  # noqa: F401
except ImportError:
    sys.modules["keyboard"] = types.SimpleNamespace(parse_hotkey=lambda _value: ())

from macro_clicker.bot.adapters import (
    apply_gather_config,
    apply_position_config,
    apply_rally_config,
    configured_scenario,
)
from macro_clicker.bot.config import BotConfig, GatherConfig, PositionsConfig, RallyConfig
from macro_clicker.bot.controller import FEATURE_GATHER
from macro_clicker.models import Action, Scenario, Step, project_path, validate_scenario


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


def test_gather_adapter_normalizes_level_and_changes_target_marches():
    scenario = _gather_scenario()
    config = GatherConfig(
        start_level=8,
        march_count=2,
        replacement_order=[3, 1, 2],
    )

    configured = apply_gather_config(scenario, config)
    prepare = configured.steps[0]
    minus_clicks = [
        action
        for action in prepare.actions
        if action.type == "click"
        and action.offset_x == -183
        and action.offset_y == -74
    ]
    plus_clicks = [
        action
        for action in prepare.actions
        if action.type == "click"
        and action.offset_x == 182
        and action.offset_y == -74
    ]
    assert len(minus_clicks) == 15
    assert len(plus_clicks) == 7

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


def test_selected_team_gather_starts_lv3_after_remembered_level_reset():
    config = BotConfig()
    config.gather.enabled = True
    config.gather.start_level = 3

    scenario = configured_scenario(FEATURE_GATHER, config, gather_team=1)
    prepare = next(
        step for step in scenario.steps if step.name == "Gather - Prepare Gold Lv12"
    )
    clicks = [action for action in prepare.actions if action.type == "click"]
    level_offsets = [
        (action.offset_x, action.offset_y)
        for action in clicks
        if action.offset_y == -74
    ]

    assert (clicks[0].offset_x, clicks[0].offset_y) == (0, -480)
    assert (clicks[1].offset_x, clicks[1].offset_y) == (196, -348)
    assert level_offsets == [(-183, -74)] * 15 + [(182, -74)] * 2
    assert clicks[-1].on_condition_index == 0


def test_selected_team_gather_clicks_exact_team_before_dispatch_and_never_replaces_busy():
    config = BotConfig()
    config.gather.enabled = True
    config.gather.teams_enabled = [1, 2, 3]

    scenario = configured_scenario(FEATURE_GATHER, config, gather_team=2)
    dispatch = next(step for step in scenario.steps if step.name == "Gather - Dispatch Ready")
    no_free = next(step for step in scenario.steps if step.name == "Gather - No Free March")
    busy = next(step for step in scenario.steps if step.name == "Gather - Selected Team Busy")
    success = next(step for step in scenario.steps if step.name == "Gather - Success")

    step_names = [step.name for step in scenario.steps]
    assert step_names.index("Gather - Dispatch Ready") < step_names.index(
        "Gather - No Free March"
    )

    assert dispatch.condition_operator == "AND"
    assert len(dispatch.conditions) == 2
    assert dispatch.conditions[1].template_path == "templates/Team2Idle.png"
    assert dispatch.conditions[1].region == [837, 938, 40, 36]

    # The exact Team 2 card selection must happen before the pre-existing
    # Dispatch-button action, so the game cannot silently choose another team.
    first = dispatch.actions[0]
    assert first.type == "click"
    assert first.on_condition_index == 1
    assert (first.offset_x, first.offset_y) == (40, 21)
    assert dispatch.actions[1].type == "wait"

    # If no slot exists, continuous Gather leaves existing work alone. It
    # closes/stops rather than invoking the legacy replacement controller.
    assert [action.type for action in no_free.actions] == ["key", "wait", "stop"]
    assert no_free.actions[0].key == "esc"
    assert not any(action.type == "gather_control" for action in no_free.actions)

    # A stale world-map idle observation gets re-verified on the dispatch panel.
    assert busy.condition_operator == "AND"
    assert busy.conditions[1].negate is True
    assert busy.conditions[1].template_path == "templates/Team2Idle.png"
    assert [action.type for action in busy.actions] == ["key", "wait", "stop"]

    # Restore the normal deployment sidebar before the finite one-team worker
    # records success and stops. The outer continuous service then observes the
    # real busy rows instead of treating the selected bottom card strip as 0/3.
    dismiss, settle, record = success.actions[:3]
    assert dismiss.type == "click"
    assert dismiss.on_condition_index == 0
    assert (dismiss.offset_x, dismiss.offset_y) == (500, -250)
    assert settle.type == "wait"
    assert settle.seconds == 0.35
    assert record.type == "gather_control"
    assert record.gather_command == "record_success"


def test_each_selected_team_gather_idle_template_decodes_and_validates():
    config = BotConfig()
    config.gather.enabled = True
    config.gather.teams_enabled = [1, 2, 3]

    for team in config.gather.teams_enabled:
        scenario = configured_scenario(FEATURE_GATHER, config, gather_team=team)
        validate_scenario(scenario, require_files=True)
        dispatch = next(
            step for step in scenario.steps if step.name == "Gather - Dispatch Ready"
        )
        idle_path = dispatch.conditions[1].template_path
        idle_image = cv2.imread(project_path(idle_path), cv2.IMREAD_COLOR)

        assert idle_image is not None, idle_path
        assert idle_image.size > 0, idle_path


def test_team3_gather_idle_template_passes_supervised_live_region():
    live_region = cv2.imread(
        project_path("tests/fixtures/gather_team3_idle_region_20260828.png"),
        cv2.IMREAD_COLOR,
    )
    template = cv2.imread(project_path("templates/Team3Idle.png"), cv2.IMREAD_COLOR)

    assert live_region is not None
    assert template is not None
    score = cv2.minMaxLoc(
        cv2.matchTemplate(live_region, template, cv2.TM_CCOEFF_NORMED)
    )[1]

    assert score >= 0.85


def _position_scenario():
    retry = Step(
        name="Retry - Apply Unavailable",
        actions=[
            Action(type="key", key="esc"),
            Action(type="wait", seconds=0.1),
            Action(type="key", key="esc"),
            Action(
                type="set_step",
                step_name="Complete - Apply Available",
                set_enabled=False,
            ),
            Action(
                type="set_step",
                step_name="Retry - Apply Unavailable",
                set_enabled=False,
            ),
            Action(type="set_step", step_name="Open #2212", set_enabled=True),
        ],
    )
    return Scenario(
        name="Apply Development Position",
        steps=[Step(name="Open #2212"), retry],
    )


def test_position_adapter_preserves_existing_retry_loop_by_default():
    source = _position_scenario()

    configured = apply_position_config(source, PositionsConfig())
    retry = next(
        step for step in configured.steps if step.name == "Retry - Apply Unavailable"
    )

    assert retry.actions[-1].type == "set_step"
    assert retry.actions[-1].step_name == "Open #2212"
    assert source.steps[-1].actions[-1].type == "set_step"


def test_position_adapter_can_stop_after_unavailable_cleanup():
    source = _position_scenario()
    config = PositionsConfig(retry_automatically=False)

    configured = apply_position_config(source, config)
    retry = next(
        step for step in configured.steps if step.name == "Retry - Apply Unavailable"
    )

    assert [action.type for action in retry.actions[:3]] == ["key", "wait", "key"]
    assert retry.actions[-1].type == "stop"
    assert not any(
        action.type == "set_step"
        and action.step_name == "Open #2212"
        and action.set_enabled
        for action in retry.actions
    )
    assert source.steps[-1].actions[-1].type == "set_step"
