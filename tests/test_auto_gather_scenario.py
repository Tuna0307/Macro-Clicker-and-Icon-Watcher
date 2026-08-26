import json
from pathlib import Path

from macro_clicker.models import Scenario


ROOT = Path(__file__).resolve().parents[1]
SCENARIO_PATH = ROOT / "scenarios" / "Gather Gold.json"


def load_raw_scenario():
    with SCENARIO_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def step_map(data):
    return {step["name"]: step for step in data["steps"]}


def test_auto_gather_scenario_is_compact_and_assets_exist():
    data = load_raw_scenario()
    assert data["name"] == "Gather Gold"
    assert len(data["steps"]) == 8
    assert not any(" S1 " in step["name"] or " P3 " in step["name"] for step in data["steps"])
    assert data["target_window_title"] == "Last War-Survival Game"
    assert data["kill_switch"] == "f12"

    for step in data["steps"]:
        for condition in step.get("conditions", []):
            template = condition.get("template_path")
            if template:
                assert (ROOT / template).is_file(), template


def test_auto_gather_model_loads_new_control_actions():
    # Parse the already-loaded scenario data through the model layer. This tests
    # the gather_control schema without confusing the public name-based
    # load_scenario() API with a filesystem path.
    scenario = Scenario.from_dict(load_raw_scenario())
    assert scenario.name == "Gather Gold"
    commands = [
        action.gather_command
        for step in scenario.steps
        for action in step.actions
        if action.type == "gather_control"
    ]
    assert commands == [
        "select_replacement",
        "cancel_retry",
        "record_success",
    ]


def test_auto_gather_forces_gold_and_max_level_before_search():
    steps = step_map(load_raw_scenario())
    actions = steps["Gather - Prepare Gold Lv12"]["actions"]
    clicks = [action for action in actions if action["type"] == "click"]
    assert clicks[0]["offset_x"] == 196
    assert clicks[0]["offset_y"] == -348
    plus_clicks = [
        action
        for action in clicks[1:-1]
        if action.get("offset_x") == 182 and action.get("offset_y") == -74
    ]
    # The bundled backend may intentionally overshoot the '+' button because
    # the game clamps at its maximum. Normal Bot runs replace this whole
    # repeated sequence with exactly the configured starting-level count in
    # apply_gather_config(), so the raw scenario only needs enough clicks to
    # guarantee the maximum rather than an exact historical count.
    assert len(plus_clicks) >= 12
    assert clicks[-1].get("on_condition_index") == 0


def test_auto_gather_keeps_lowering_and_searching_until_found():
    steps = step_map(load_raw_scenario())
    assert "Gather - Lv12 unavailable" not in steps
    assert "Gather - Lv11 unavailable" not in steps
    assert "Gather - Lv10 unavailable" not in steps

    fallback = steps["Gather - Search unavailable"]
    assert fallback.get("repeatable", True) is True
    clicks = [action for action in fallback["actions"] if action["type"] == "click"]
    assert (clicks[0]["offset_x"], clicks[0]["offset_y"]) == (-183, -74)
    assert clicks[1].get("on_condition_index") == 0
    assert not any(action["type"] == "stop" for action in fallback["actions"])

    prepare_actions = steps["Gather - Prepare Gold Lv12"]["actions"]
    assert any(
        action["type"] == "set_step"
        and action["step_name"] == "Gather - Search unavailable"
        for action in prepare_actions
    )
    found_actions = steps["Gather - Resource Found"]["actions"]
    assert any(
        action["type"] == "set_step"
        and action["step_name"] == "Gather - Search unavailable"
        and action.get("set_enabled") is False
        for action in found_actions
    )


def test_auto_gather_replacement_is_one_stateful_action():
    steps = step_map(load_raw_scenario())
    actions = steps["Gather - No Free March"]["actions"]
    controls = [action for action in actions if action["type"] == "gather_control"]
    assert len(controls) == 1
    assert controls[0]["gather_command"] == "select_replacement"
    assert controls[0]["gather_replacement_order"] == [3, 2, 1]
    assert controls[0]["on_condition_index"] == 0


def test_taken_resource_retries_without_advancing_state():
    steps = step_map(load_raw_scenario())
    actions = steps["Gather - Resource Taken"]["actions"]
    controls = [action for action in actions if action["type"] == "gather_control"]
    assert len(controls) == 1
    assert controls[0]["gather_command"] == "cancel_retry"
    assert not any(action["type"] == "stop" for action in actions)
    assert any(
        action["type"] == "set_step"
        and action["step_name"] == "Gather - Open Search"
        for action in actions
    )


def test_success_records_three_verified_dispatches_and_loops_until_complete():
    steps = step_map(load_raw_scenario())
    success = steps["Gather - Success"]
    assert success["condition_operator"] == "AND"
    assert success["conditions"][1]["template_path"].endswith("GatherTakenCancel.jpg")
    assert success["conditions"][1]["negate"] is True

    controls = [
        action for action in success["actions"] if action["type"] == "gather_control"
    ]
    assert len(controls) == 1
    assert controls[0]["gather_command"] == "record_success"
    assert controls[0]["gather_target_count"] == 3
    assert controls[0]["gather_replacement_order"] == [3, 2, 1]
    assert any(
        action["type"] == "set_step"
        and action["step_name"] == "Gather - Open Search"
        for action in success["actions"]
    )
