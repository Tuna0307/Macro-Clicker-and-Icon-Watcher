import json
from pathlib import Path

from macro_clicker.models import Scenario

ROOT = Path(__file__).resolve().parents[1]
SCENARIO_PATH = ROOT / "scenarios" / "Gather Gold.json"


def load_scenario():
    return json.loads(SCENARIO_PATH.read_text(encoding="utf-8"))


def step_map(data):
    return {step["name"]: step for step in data["steps"]}


def test_auto_gather_scenario_is_compact_and_assets_exist():
    data = load_scenario()
    assert data["name"] == "Gather Gold"
    assert len(data["steps"]) == 10
    assert not any(" S1 " in step["name"] or " P3 " in step["name"] for step in data["steps"])
    assert data["target_window_title"] == "Last War-Survival Game"
    assert data["kill_switch"] == "f12"
    for step in data["steps"]:
        for condition in step.get("conditions", []):
            path = condition.get("template_path")
            if path:
                assert (ROOT / path).is_file(), path


def test_auto_gather_model_loads_new_control_actions():
    scenario = Scenario.from_dict(load_scenario())
    assert scenario.name == "Gather Gold"
    controls = [
        action
        for step in scenario.steps
        for action in step.actions
        if action.type == "gather_control"
    ]
    assert [action.gather_command for action in controls] == [
        "select_replacement",
        "cancel_retry",
        "record_success",
    ]


def test_auto_gather_forces_gold_and_max_level_before_search():
    steps = step_map(load_scenario())
    actions = steps["Gather - Prepare Gold Lv12"]["actions"]
    clicks = [action for action in actions if action["type"] == "click"]
    assert clicks[0]["offset_x"] == 196
    assert clicks[0]["offset_y"] == -348
    plus_clicks = [
        action
        for action in clicks[1:-1]
        if action.get("offset_x") == 182 and action.get("offset_y") == -74
    ]
    assert len(plus_clicks) == 12
    assert clicks[-1].get("on_condition_index") == 0


def test_auto_gather_falls_back_12_to_11_to_10():
    steps = step_map(load_scenario())
    for name in ("Gather - Lv12 unavailable", "Gather - Lv11 unavailable"):
        clicks = [a for a in steps[name]["actions"] if a["type"] == "click"]
        assert (clicks[0]["offset_x"], clicks[0]["offset_y"]) == (-183, -74)
        assert clicks[1].get("on_condition_index") == 0
    assert steps["Gather - Lv10 unavailable"]["actions"] == [{"type": "stop"}]


def test_auto_gather_replacement_is_one_stateful_action():
    steps = step_map(load_scenario())
    actions = steps["Gather - No Free March"]["actions"]
    control = next(action for action in actions if action["type"] == "gather_control")
    assert control["gather_command"] == "select_replacement"
    assert control["gather_replacement_order"] == [3, 2, 1]
    assert control["on_condition_index"] == 0


def test_taken_resource_retries_without_advancing_state():
    steps = step_map(load_scenario())
    actions = steps["Gather - Resource Taken"]["actions"]
    assert actions[0]["type"] == "click"
    control = next(action for action in actions if action["type"] == "gather_control")
    assert control["gather_command"] == "cancel_retry"
    assert not any(action["type"] == "stop" for action in actions)


def test_success_records_three_verified_dispatches_and_loops_until_complete():
    steps = step_map(load_scenario())
    success = steps["Gather - Success"]
    assert success["condition_operator"] == "AND"
    assert success["conditions"][1]["template_path"].endswith("GatherTakenCancel.jpg")
    assert success["conditions"][1]["negate"] is True
    control = next(action for action in success["actions"] if action["type"] == "gather_control")
    assert control["gather_command"] == "record_success"
    assert control["gather_target_count"] == 3
    assert control["gather_replacement_order"] == [3, 2, 1]
    assert any(
        action["type"] == "set_step" and action["step_name"] == "Gather - Open Search"
        for action in success["actions"]
    )
