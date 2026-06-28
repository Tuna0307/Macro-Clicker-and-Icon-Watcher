"""
Data models for the PC Macro Builder.

A Scenario is a named, savable/loadable set of Steps. Each Step has
Conditions (what must/must-not be on screen) and Actions (what to do
when conditions are met), and can enable/disable other Steps -- that's
how sequencing ("step 1 then step 2, skip if not found") is built.

Everything here is plain dataclasses + to_dict/from_dict, so a
Scenario can be saved as a single JSON file under scenarios/.
"""
import json
import os
from dataclasses import dataclass, field, asdict
from typing import List, Optional


ACTION_TYPES = ["click", "click_matching_row", "key", "wait", "set_step"]
SCENARIOS_DIR = "scenarios"
TEMPLATES_DIR = "templates"


@dataclass
class ImageCondition:
    condition_type: str = "template"     # OpenCV template match
    template_path: str = ""
    confidence: float = 0.85
    region: Optional[List[int]] = None   # [left, top, width, height] screen coords, or None = full monitor
    region_mode: str = "screen"          # "screen" = absolute coords, "window" = relative to target window
    region_ratio: Optional[List[float]] = None  # proportional window region, used if target window resizes
    region_window_size: Optional[List[int]] = None  # [width, height] of target window when region was picked
    negate: bool = False                  # True = condition succeeds when the image is ABSENT

    def to_dict(self):
        return asdict(self)

    @staticmethod
    def from_dict(d):
        return ImageCondition(
            condition_type="template",
            template_path=d.get("template_path", ""),
            confidence=d.get("confidence", 0.85),
            region=d.get("region"),
            region_mode=d.get("region_mode", "screen"),
            region_ratio=d.get("region_ratio"),
            region_window_size=d.get("region_window_size"),
            negate=d.get("negate", False),
        )


@dataclass
class Action:
    type: str = "click"   # one of ACTION_TYPES

    # click
    on_condition_index: Optional[int] = None  # click center of this step's Nth condition match
    match_condition_index: Optional[int] = None  # for click_matching_row: row reference condition
    row_tolerance: int = 60                     # vertical center distance allowed for same-row matching
    row_mode: str = "first"                     # "first" = first valid row, "all" = every valid row
    target_choice: str = "leftmost"             # "leftmost", "rightmost", or "nearest"
    min_level: Optional[int] = None             # optional level filter for click_matching_row
    max_level: Optional[int] = None
    level_digit_template_dir: str = os.path.join(TEMPLATES_DIR, "level_digits")
    level_roi: Optional[List[int]] = None       # [x, y, w, h] relative to row reference center
    level_min_digits: int = 1
    no_match_condition_index: Optional[int] = None  # for click_matching_row fallback
    no_match_disable_steps: List[str] = field(default_factory=list)
    x: Optional[int] = None                   # or a fixed point instead
    y: Optional[int] = None
    offset_x: int = 0
    offset_y: int = 0
    button: str = "left"

    # key
    key: str = ""
    hold: float = 0.0   # seconds to hold down; 0 = quick tap

    # wait
    seconds: float = 0.5

    # set_step (enable/disable another step -- this is what creates sequencing)
    step_name: str = ""
    set_enabled: bool = True

    def to_dict(self):
        return asdict(self)

    @staticmethod
    def from_dict(d):
        a = Action()
        for k, v in d.items():
            if hasattr(a, k):
                setattr(a, k, v)
        return a

    def summary(self):
        if self.type == "click":
            target = (f"condition #{self.on_condition_index}"
                      if self.on_condition_index is not None else f"({self.x}, {self.y})")
            return f"Click {target}  [{self.button}]"
        if self.type == "click_matching_row":
            scope = "all rows" if self.row_mode == "all" else "first row"
            level_parts = []
            if self.min_level is not None:
                level_parts.append(f">= {self.min_level}")
            if self.max_level is not None:
                level_parts.append(f"<= {self.max_level}")
            level = f", level {' and '.join(level_parts)}" if level_parts else ""
            fallback = (
                f"; no match click condition #{self.no_match_condition_index}"
                if self.no_match_condition_index is not None else ""
            )
            return (f"Click {self.target_choice} condition #{self.on_condition_index} matching "
                    f"{scope} of condition #{self.match_condition_index}{level}{fallback}  [{self.button}]")
        if self.type == "key":
            extra = f" (hold {self.hold}s)" if self.hold else ""
            return f"Press key '{self.key}'{extra}"
        if self.type == "wait":
            return f"Wait {self.seconds}s"
        if self.type == "set_step":
            verb = "Enable" if self.set_enabled else "Disable"
            return f"{verb} step '{self.step_name}'"
        return self.type


@dataclass
class Step:
    name: str
    conditions: List[ImageCondition] = field(default_factory=list)
    actions: List[Action] = field(default_factory=list)
    condition_operator: str = "AND"   # "AND" = all conditions must hold, "OR" = any one
    enabled: bool = True
    cooldown: float = 1.0
    repeatable: bool = True

    def to_dict(self):
        return {
            "name": self.name,
            "conditions": [c.to_dict() for c in self.conditions],
            "actions": [a.to_dict() for a in self.actions],
            "condition_operator": self.condition_operator,
            "enabled": self.enabled,
            "cooldown": self.cooldown,
            "repeatable": self.repeatable,
        }

    @staticmethod
    def from_dict(d):
        return Step(
            name=d.get("name", ""),
            conditions=[ImageCondition.from_dict(c) for c in d.get("conditions", [])],
            actions=[Action.from_dict(a) for a in d.get("actions", [])],
            condition_operator=d.get("condition_operator", "AND"),
            enabled=d.get("enabled", True),
            cooldown=d.get("cooldown", 1.0),
            repeatable=d.get("repeatable", True),
        )


@dataclass
class Scenario:
    name: str
    steps: List[Step] = field(default_factory=list)
    poll_interval: float = 0.25
    monitor_index: int = 1
    kill_switch: str = "f12"
    target_window_title: str = ""

    def to_dict(self):
        return {
            "name": self.name,
            "steps": [s.to_dict() for s in self.steps],
            "poll_interval": self.poll_interval,
            "monitor_index": self.monitor_index,
            "kill_switch": self.kill_switch,
            "target_window_title": self.target_window_title,
        }

    @staticmethod
    def from_dict(d):
        return Scenario(
            name=d.get("name", "untitled"),
            steps=[Step.from_dict(s) for s in d.get("steps", [])],
            poll_interval=d.get("poll_interval", 0.25),
            monitor_index=d.get("monitor_index", 1),
            kill_switch=d.get("kill_switch", "f12"),
            target_window_title=d.get("target_window_title", ""),
        )


def list_scenarios(folder=SCENARIOS_DIR):
    if not os.path.isdir(folder):
        return []
    return sorted(f[:-5] for f in os.listdir(folder) if f.endswith(".json"))


def load_scenario(name, folder=SCENARIOS_DIR):
    path = os.path.join(folder, f"{name}.json")
    with open(path, "r", encoding="utf-8") as f:
        return Scenario.from_dict(json.load(f))


def save_scenario(scenario: Scenario, folder=SCENARIOS_DIR):
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{scenario.name}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(scenario.to_dict(), f, indent=2)
    return path


def delete_scenario(name, folder=SCENARIOS_DIR):
    path = os.path.join(folder, f"{name}.json")
    if os.path.exists(path):
        os.remove(path)
