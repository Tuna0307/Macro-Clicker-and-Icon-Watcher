import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCENARIO_PATH = ROOT / "scenarios" / "Gather Gold - Supervised Test.json"


def load_scenario():
    return json.loads(SCENARIO_PATH.read_text(encoding="utf-8"))


def step_map(data):
    return {step["name"]: step for step in data["steps"]}


def test_auto_gather_supervised_scenario_assets_exist():
    data = load_scenario()
    assert data["name"] == "Gather Gold - Supervised Test"
    assert data["target_window_title"] == "Last War-Survival Game"
    assert data["kill_switch"] == "f12"
    for step in data["steps"]:
        for condition in step.get("conditions", []):
            path = condition.get("template_path")
            if path:
                assert (ROOT / path).is_file(), path


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
    assert "offset_x" not in clicks[-1]


def test_auto_gather_falls_back_12_to_11_to_10():
    steps = step_map(load_scenario())
    for name in ("Gather - Lv12 unavailable", "Gather - Lv11 unavailable"):
        clicks = [a for a in steps[name]["actions"] if a["type"] == "click"]
        assert clicks[0]["offset_x"] == -183
        assert clicks[0]["offset_y"] == -74
        assert clicks[1].get("on_condition_index") == 0
        assert "offset_x" not in clicks[1]
    assert steps["Gather - Lv10 unavailable"]["actions"] == [{"type": "stop"}]


def test_auto_gather_busy_path_selects_march_three():
    steps = step_map(load_scenario())
    actions = steps["Gather - No Free March"]["actions"]
    click = next(action for action in actions if action["type"] == "click")
    assert click["offset_x"] == 63
    assert click["offset_y"] == 630


def test_auto_gather_taken_resource_retries_same_logical_dispatch():
    steps = step_map(load_scenario())
    actions = steps["Gather - Resource Taken"]["actions"]
    assert actions[0]["type"] == "click"  # Cancel
    assert not any(action["type"] == "stop" for action in actions)
    assert any(
        action["type"] == "set_step"
        and action["step_name"] == "Gather - Open Search"
        and action["set_enabled"] is True
        for action in actions
    )


def test_auto_gather_success_requires_taken_warning_absent():
    steps = step_map(load_scenario())
    success = steps["Gather - Success"]
    assert success["condition_operator"] == "AND"
    assert len(success["conditions"]) == 2
    assert success["conditions"][1]["template_path"].endswith("GatherTakenCancel.jpg")
    assert success["conditions"][1]["negate"] is True
    assert success["actions"] == [{"type": "stop"}]
