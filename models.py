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


ACTION_TYPES = frozenset({"click", "click_matching_row", "key", "wait", "set_step"})
APP_DIR = os.path.dirname(os.path.abspath(__file__))
SCENARIOS_DIR = os.path.join(APP_DIR, "scenarios")
TEMPLATES_DIR = os.path.join(APP_DIR, "templates")
WINDOWS_RESERVED_SCENARIO_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)


def project_path(path):
    if not path or os.path.isabs(path):
        return path
    return os.path.join(APP_DIR, path)


def _optional_int(value, default=None):
    if value is None or value == "":
        return default
    return int(value)


def _int_value(value, default=0):
    if value is None or value == "":
        return default
    return int(value)


def _float_value(value, default=0.0):
    if value is None or value == "":
        return default
    return float(value)


def _bool_value(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _string_list(value):
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, (list, tuple)):
        return [str(part) for part in value if str(part).strip()]
    return []


def _int_list(value):
    if value is None:
        return None
    if not isinstance(value, (list, tuple)):
        return None
    try:
        return [int(part) for part in value]
    except (TypeError, ValueError):
        return None


def _float_list(value):
    if value is None:
        return None
    if not isinstance(value, (list, tuple)):
        return None
    try:
        return [float(part) for part in value]
    except (TypeError, ValueError):
        return None


@dataclass
class ImageCondition:
    condition_type: str = "template"     # OpenCV template match
    template_path: str = ""
    confidence: float = 0.85
    comparison_template_path: str = ""   # optional rival template that this template must outscore
    comparison_margin: float = 0.03       # minimum score lead over the rival template
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
            template_path=str(d.get("template_path", "")),
            confidence=_float_value(d.get("confidence"), 0.85),
            comparison_template_path=str(d.get("comparison_template_path", "")),
            comparison_margin=max(0.0, _float_value(d.get("comparison_margin"), 0.03)),
            region=_int_list(d.get("region")),
            region_mode=str(d.get("region_mode", "screen") or "screen"),
            region_ratio=_float_list(d.get("region_ratio")),
            region_window_size=_int_list(d.get("region_window_size")),
            negate=_bool_value(d.get("negate"), False),
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
        if not isinstance(d, dict):
            return a
        a.type = str(d.get("type", a.type) or a.type)
        if a.type not in ACTION_TYPES:
            a.type = "click"
        a.on_condition_index = _optional_int(d.get("on_condition_index"), a.on_condition_index)
        a.match_condition_index = _optional_int(d.get("match_condition_index"), a.match_condition_index)
        a.row_tolerance = _int_value(d.get("row_tolerance"), a.row_tolerance)
        a.row_mode = str(d.get("row_mode", a.row_mode) or a.row_mode)
        a.target_choice = str(d.get("target_choice", a.target_choice) or a.target_choice)
        a.min_level = _optional_int(d.get("min_level"), a.min_level)
        a.max_level = _optional_int(d.get("max_level"), a.max_level)
        a.level_digit_template_dir = str(d.get("level_digit_template_dir", a.level_digit_template_dir) or "")
        a.level_roi = _int_list(d.get("level_roi"))
        a.level_min_digits = max(1, _int_value(d.get("level_min_digits"), a.level_min_digits))
        a.no_match_condition_index = _optional_int(d.get("no_match_condition_index"), a.no_match_condition_index)
        a.no_match_disable_steps = _string_list(d.get("no_match_disable_steps"))
        a.x = _optional_int(d.get("x"), a.x)
        a.y = _optional_int(d.get("y"), a.y)
        a.offset_x = _int_value(d.get("offset_x"), a.offset_x)
        a.offset_y = _int_value(d.get("offset_y"), a.offset_y)
        a.button = str(d.get("button", a.button) or a.button)
        a.key = str(d.get("key", a.key) or "")
        a.hold = _float_value(d.get("hold"), a.hold)
        a.seconds = _float_value(d.get("seconds"), a.seconds)
        a.step_name = str(d.get("step_name", a.step_name) or "")
        a.set_enabled = _bool_value(d.get("set_enabled"), a.set_enabled)
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
            name=str(d.get("name", "")),
            conditions=[ImageCondition.from_dict(c) for c in d.get("conditions", [])],
            actions=[Action.from_dict(a) for a in d.get("actions", [])],
            condition_operator=str(d.get("condition_operator", "AND") or "AND"),
            enabled=_bool_value(d.get("enabled"), True),
            cooldown=_float_value(d.get("cooldown"), 1.0),
            repeatable=_bool_value(d.get("repeatable"), True),
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
            name=str(d.get("name", "untitled") or "untitled"),
            steps=[Step.from_dict(s) for s in d.get("steps", [])],
            poll_interval=_float_value(d.get("poll_interval"), 0.25),
            monitor_index=_int_value(d.get("monitor_index"), 1),
            kill_switch=str(d.get("kill_switch", "f12") or "f12"),
            target_window_title=str(d.get("target_window_title", "") or ""),
        )


def list_scenarios(folder=SCENARIOS_DIR):
    if not os.path.isdir(folder):
        return []
    return sorted(f[:-5] for f in os.listdir(folder) if f.endswith(".json"))


def load_scenario(name, folder=SCENARIOS_DIR):
    path = os.path.join(folder, f"{name}.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("scenario file must contain a JSON object")
        return Scenario.from_dict(data)
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ValueError(f"Could not load scenario '{name}': {exc}") from exc


def validate_scenario_name(name):
    text = str(name or "")
    stripped = text.strip()
    if not stripped:
        raise ValueError("Scenario name cannot be blank.")
    if stripped != text or stripped.endswith("."):
        raise ValueError("Scenario name cannot start/end with spaces or dots.")
    invalid = set('<>:"/\\|?*')
    if any(char in invalid or ord(char) < 32 for char in stripped):
        raise ValueError('Scenario name cannot contain <>:"/\\|?* or control characters.')
    if stripped in (".", "..") or ".." in stripped.split(os.sep):
        raise ValueError("Scenario name cannot be a relative path.")
    base = stripped.split(".")[0].upper()
    if base in WINDOWS_RESERVED_SCENARIO_NAMES:
        raise ValueError(f"Scenario name '{stripped}' is reserved by Windows.")
    return stripped


def save_scenario(scenario: Scenario, folder=SCENARIOS_DIR):
    os.makedirs(folder, exist_ok=True)
    safe_name = validate_scenario_name(scenario.name)
    path = os.path.join(folder, f"{safe_name}.json")
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(scenario.to_dict(), f, indent=2)
    os.replace(tmp_path, path)
    return path


def delete_scenario(name, folder=SCENARIOS_DIR):
    path = os.path.join(folder, f"{name}.json")
    if os.path.exists(path):
        os.remove(path)
