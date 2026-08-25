import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCENARIO_PATH = ROOT / "scenarios" / "Gather Gold.json"


def load_scenario():
    return json.loads(SCENARIO_PATH.read_text(encoding="utf-8"))


def step_map(data):
    return {step["name"]: step for step in data["steps"]}


def enabled_targets(actions):
    return {
        action["step_name"]
        for action in actions
        if action["type"] == "set_step" and action.get("set_enabled", True) is True
    }


def test_auto_gather_real_scenario_assets_exist():
    data = load_scenario()
    assert data["name"] == "Gather Gold"
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


def test_auto_gather_starts_with_stage_one_and_march_three_pointer():
    data = load_scenario()
    enabled = {step["name"] for step in data["steps"] if step.get("enabled", True)}
    assert enabled == {
        "Gather - Open Search",
        "Gather - Resource Taken",
        "Gather S1 P3 - No Free March",
        "Gather S1 P3 - Dispatch Free",
    }


def test_auto_gather_replacement_offsets_are_three_two_one():
    steps = step_map(load_scenario())
    expected = {
        3: (63, 630),
        2: (-61, 630),
        1: (-188, 630),
    }
    for pointer, offsets in expected.items():
        candidates = [
            step
            for name, step in steps.items()
            if f" P{pointer} - No Free March" in name
        ]
        assert candidates
        for step in candidates:
            click = next(a for a in step["actions"] if a["type"] == "click")
            assert (click["offset_x"], click["offset_y"]) == offsets


def test_free_success_keeps_same_replacement_pointer():
    steps = step_map(load_scenario())
    actions = steps["Gather S1 P3 - Success Free"]["actions"]
    enabled = enabled_targets(actions)
    assert "Gather S2 P3 - No Free March" in enabled
    assert "Gather S2 P3 - Dispatch Free" in enabled

    actions = steps["Gather S2 P2 - Success Free"]["actions"]
    enabled = enabled_targets(actions)
    assert "Gather S3 P2 - No Free March" in enabled
    assert "Gather S3 P2 - Dispatch Free" in enabled


def test_replacement_success_advances_pointer_only_after_success():
    steps = step_map(load_scenario())
    actions = steps["Gather S1 P3 - Success Replacement"]["actions"]
    enabled = enabled_targets(actions)
    assert "Gather S2 P2 - No Free March" in enabled
    assert "Gather S2 P2 - Dispatch Free" in enabled

    actions = steps["Gather S2 P2 - Success Replacement"]["actions"]
    enabled = enabled_targets(actions)
    assert "Gather S3 P1 - No Free March" in enabled
    assert "Gather S3 P1 - Dispatch Free" in enabled


def test_taken_resource_retry_preserves_same_stage_and_pointer():
    steps = step_map(load_scenario())
    actions = steps["Gather - Resource Taken"]["actions"]
    assert actions[0]["type"] == "click"  # Cancel
    enabled = enabled_targets(actions)
    assert "Gather - Open Search" in enabled
    # Cancel is global: current stage/pointer pair remains armed for retry.
    assert not any(action["type"] == "stop" for action in actions)
    disabled = {
        action["step_name"]
        for action in actions
        if action["type"] == "set_step"
        and action.get("set_enabled", True) is False
    }
    assert "Gather S2 P2 - Success Free" in disabled
    assert "Gather S2 P2 - Success Replacement" in disabled


def test_stage_three_stops_only_after_verified_success():
    steps = step_map(load_scenario())
    for pointer in (3, 2, 1):
        assert steps[f"Gather S3 P{pointer} - Success Free"]["actions"] == [
            {"type": "stop"}
        ]
        assert steps[f"Gather S3 P{pointer} - Success Replacement"]["actions"] == [
            {"type": "stop"}
        ]


def test_success_checks_taken_warning_is_absent():
    steps = step_map(load_scenario())
    success = steps["Gather S1 P3 - Success Free"]
    assert success.get("condition_operator", "AND") == "AND"
    assert len(success["conditions"]) == 2
    assert success["conditions"][1]["template_path"].endswith("GatherTakenCancel.jpg")
    assert success["conditions"][1]["negate"] is True
