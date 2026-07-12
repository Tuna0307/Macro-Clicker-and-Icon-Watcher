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
import math
import os
import tempfile
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


def portable_project_path(path):
    """Store project-owned paths relatively while preserving external absolute paths."""
    if not path:
        return path
    if not os.path.isabs(path):
        return os.path.normpath(path).replace("\\", "/")
    absolute = os.path.abspath(project_path(path))
    try:
        if os.path.normcase(os.path.commonpath((APP_DIR, absolute))) == os.path.normcase(
            os.path.abspath(APP_DIR)
        ):
            return os.path.relpath(absolute, APP_DIR).replace("\\", "/")
    except ValueError:
        pass
    return path


def _optional_int(value, default=None):
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        raise ValueError("boolean values are not valid whole numbers")
    if isinstance(value, float) and (not math.isfinite(value) or not value.is_integer()):
        raise ValueError(f"expected a whole number, got {value!r}")
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"expected a whole number, got {value!r}") from exc


def _int_value(value, default=0):
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        raise ValueError("boolean values are not valid whole numbers")
    if isinstance(value, float) and (not math.isfinite(value) or not value.is_integer()):
        raise ValueError(f"expected a whole number, got {value!r}")
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"expected a whole number, got {value!r}") from exc


def _float_value(value, default=0.0):
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        raise ValueError("boolean values are not valid numbers")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"expected a number, got {value!r}") from exc
    if not math.isfinite(result):
        raise ValueError("number must be finite")
    return result


def _bool_value(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        raise ValueError(f"invalid boolean value: {value!r}")
    if isinstance(value, (int, float)) and value in {0, 1}:
        return bool(value)
    raise ValueError(f"invalid boolean value: {value!r}")


def _string_list(value):
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, (list, tuple)):
        if not all(isinstance(part, str) for part in value):
            raise ValueError("expected a list of names")
        return [part.strip() for part in value if part.strip()]
    raise ValueError("expected a list of names")


def _int_list(value):
    if value is None:
        return None
    if not isinstance(value, (list, tuple)):
        return None
    try:
        if any(isinstance(part, bool) for part in value):
            return None
        if any(
            isinstance(part, float) and (not math.isfinite(part) or not part.is_integer())
            for part in value
        ):
            return None
        return [int(part) for part in value]
    except (TypeError, ValueError):
        return None


def _float_list(value):
    if value is None:
        return None
    if not isinstance(value, (list, tuple)):
        return None
    try:
        if any(isinstance(part, bool) for part in value):
            return None
        result = [float(part) for part in value]
        return result if all(math.isfinite(part) for part in result) else None
    except (TypeError, ValueError):
        return None


def _require_dict(value, label):
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _require_list(value, label):
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a JSON array")
    return value


def _validate_region(region, label="region"):
    if region is None:
        return None
    if not isinstance(region, (list, tuple)) or len(region) != 4:
        raise ValueError(f"{label} must contain [left, top, width, height]")
    if any(isinstance(value, bool) or not isinstance(value, int) for value in region):
        raise ValueError(f"{label} must contain whole numbers")
    if region[2] <= 0 or region[3] <= 0:
        raise ValueError(f"{label} width and height must be positive")
    return region


def _validate_ratio(region_ratio):
    if region_ratio is None:
        return None
    if not isinstance(region_ratio, (list, tuple)) or len(region_ratio) != 4:
        raise ValueError("region_ratio must contain [left, top, width, height]")
    try:
        left, top, width, height = (float(value) for value in region_ratio)
    except (TypeError, ValueError) as exc:
        raise ValueError("region_ratio must contain finite numbers") from exc
    if not all(math.isfinite(value) for value in (left, top, width, height)):
        raise ValueError("region_ratio must contain finite numbers")
    if left < 0.0 or top < 0.0 or width <= 0.0 or height <= 0.0:
        raise ValueError("region_ratio values must describe a positive region inside the window")
    if left + width > 1.001 or top + height > 1.001:
        raise ValueError("region_ratio must stay inside the target window")
    return region_ratio


def _validate_window_size(size):
    if size is None:
        return None
    if (
        not isinstance(size, (list, tuple))
        or len(size) != 2
        or any(isinstance(value, bool) or not isinstance(value, int) for value in size)
        or size[0] <= 0
        or size[1] <= 0
    ):
        raise ValueError("region_window_size must contain a positive [width, height]")
    return size


def _optional_int_list_field(data, key, label):
    raw = data.get(key)
    if raw is None:
        return None
    parsed = _int_list(raw)
    if parsed is None:
        raise ValueError(f"{label} must be an array of whole numbers")
    return parsed


def _optional_float_list_field(data, key, label):
    raw = data.get(key)
    if raw is None:
        return None
    parsed = _float_list(raw)
    if parsed is None:
        raise ValueError(f"{label} must be an array of finite numbers")
    return parsed


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
        data = asdict(self)
        data["template_path"] = portable_project_path(self.template_path)
        data["comparison_template_path"] = portable_project_path(self.comparison_template_path)
        return data

    @staticmethod
    def from_dict(d):
        d = _require_dict(d, "condition")
        condition_type = str(d.get("condition_type", "template") or "template")
        if condition_type != "template":
            raise ValueError(f"unsupported condition type: {condition_type!r}")
        confidence = _float_value(d.get("confidence"), 0.85)
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("condition confidence must be between 0 and 1")
        comparison_margin = _float_value(d.get("comparison_margin"), 0.03)
        if not 0.0 <= comparison_margin <= 1.0:
            raise ValueError("comparison margin must be between 0 and 1")
        region = _validate_region(_optional_int_list_field(d, "region", "region"))
        region_mode = str(d.get("region_mode", "screen") or "screen")
        if region_mode not in {"screen", "window"}:
            raise ValueError("region_mode must be 'screen' or 'window'")
        region_ratio = _validate_ratio(
            _optional_float_list_field(d, "region_ratio", "region_ratio")
        )
        region_window_size = _validate_window_size(
            _optional_int_list_field(d, "region_window_size", "region_window_size")
        )
        if region_mode == "window" and region is not None:
            if (region_ratio is None) != (region_window_size is None):
                raise ValueError("window regions must provide both ratio and base window size, or neither")
        if region is None and (region_ratio is not None or region_window_size is not None):
            raise ValueError("region resize metadata requires a region")
        if region_mode == "screen" and (region_ratio is not None or region_window_size is not None):
            raise ValueError("screen regions cannot contain window resize metadata")
        template_path = d.get("template_path", "")
        comparison_template_path = d.get("comparison_template_path", "")
        if not isinstance(template_path, str) or not isinstance(comparison_template_path, str):
            raise ValueError("template paths must be text")
        return ImageCondition(
            condition_type=condition_type,
            template_path=template_path,
            confidence=confidence,
            comparison_template_path=comparison_template_path,
            comparison_margin=comparison_margin,
            region=region,
            region_mode=region_mode,
            region_ratio=region_ratio,
            region_window_size=region_window_size,
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
        data = asdict(self)
        data["level_digit_template_dir"] = portable_project_path(self.level_digit_template_dir)
        return data

    @staticmethod
    def from_dict(d):
        d = _require_dict(d, "action")
        a = Action()
        a.type = str(d.get("type", a.type) or a.type)
        if a.type not in ACTION_TYPES:
            raise ValueError(f"unsupported action type: {a.type!r}")
        a.on_condition_index = _optional_int(d.get("on_condition_index"), a.on_condition_index)
        a.match_condition_index = _optional_int(d.get("match_condition_index"), a.match_condition_index)
        a.row_tolerance = _int_value(d.get("row_tolerance"), a.row_tolerance)
        if a.row_tolerance < 0:
            raise ValueError("row_tolerance cannot be negative")
        a.row_mode = str(d.get("row_mode", a.row_mode) or a.row_mode)
        if a.row_mode not in {"first", "all"}:
            raise ValueError("row_mode must be 'first' or 'all'")
        a.target_choice = str(d.get("target_choice", a.target_choice) or a.target_choice)
        if a.target_choice not in {"leftmost", "rightmost", "nearest"}:
            raise ValueError("target_choice must be leftmost, rightmost, or nearest")
        a.min_level = _optional_int(d.get("min_level"), a.min_level)
        a.max_level = _optional_int(d.get("max_level"), a.max_level)
        if a.min_level is not None and a.min_level < 0:
            raise ValueError("min_level cannot be negative")
        if a.max_level is not None and a.max_level < 0:
            raise ValueError("max_level cannot be negative")
        if a.min_level is not None and a.max_level is not None and a.min_level > a.max_level:
            raise ValueError("min_level cannot be greater than max_level")
        a.level_digit_template_dir = str(d.get("level_digit_template_dir", a.level_digit_template_dir) or "")
        a.level_roi = _validate_region(
            _optional_int_list_field(d, "level_roi", "level_roi"),
            "level_roi",
        )
        a.level_min_digits = _int_value(d.get("level_min_digits"), a.level_min_digits)
        if not 1 <= a.level_min_digits <= 4:
            raise ValueError("level_min_digits must be between 1 and 4")
        a.no_match_condition_index = _optional_int(d.get("no_match_condition_index"), a.no_match_condition_index)
        a.no_match_disable_steps = _string_list(d.get("no_match_disable_steps"))
        a.x = _optional_int(d.get("x"), a.x)
        a.y = _optional_int(d.get("y"), a.y)
        if (a.x is None) != (a.y is None):
            raise ValueError("fixed click coordinates require both x and y")
        if a.type == "click" and a.x is not None and a.on_condition_index is not None:
            raise ValueError("click actions cannot mix fixed and condition targets")
        a.offset_x = _int_value(d.get("offset_x"), a.offset_x)
        a.offset_y = _int_value(d.get("offset_y"), a.offset_y)
        a.button = str(d.get("button", a.button) or a.button)
        if a.button not in {"left", "right", "middle"}:
            raise ValueError("button must be left, right, or middle")
        a.key = str(d.get("key", a.key) or "")
        a.hold = _float_value(d.get("hold"), a.hold)
        if a.hold < 0.0:
            raise ValueError("key hold duration cannot be negative")
        a.seconds = _float_value(d.get("seconds"), a.seconds)
        if a.seconds < 0.0:
            raise ValueError("wait duration cannot be negative")
        a.step_name = str(d.get("step_name", a.step_name) or "")
        a.set_enabled = _bool_value(d.get("set_enabled"), a.set_enabled)
        if a.type == "key" and not a.key.strip():
            raise ValueError("key actions require a key name")
        if a.type == "set_step" and not a.step_name.strip():
            raise ValueError("set_step actions require a target step name")
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
        d = _require_dict(d, "step")
        raw_conditions = _require_list(d.get("conditions", []), "step conditions")
        raw_actions = _require_list(d.get("actions", []), "step actions")
        condition_operator = str(d.get("condition_operator", "AND") or "AND").upper()
        if condition_operator not in {"AND", "OR"}:
            raise ValueError("condition_operator must be AND or OR")
        cooldown = _float_value(d.get("cooldown"), 1.0)
        if cooldown < 0.0:
            raise ValueError("step cooldown cannot be negative")
        name = d.get("name", "")
        if not isinstance(name, str):
            raise ValueError("step name must be text")
        return Step(
            name=name,
            conditions=[ImageCondition.from_dict(c) for c in raw_conditions],
            actions=[Action.from_dict(a) for a in raw_actions],
            condition_operator=condition_operator,
            enabled=_bool_value(d.get("enabled"), True),
            cooldown=cooldown,
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
        d = _require_dict(d, "scenario")
        raw_steps = _require_list(d.get("steps", []), "scenario steps")
        steps = [Step.from_dict(s) for s in raw_steps]
        poll_interval = _float_value(d.get("poll_interval"), 0.25)
        if poll_interval < 0.01:
            raise ValueError("poll_interval must be at least 0.01 seconds")
        monitor_index = _int_value(d.get("monitor_index"), 1)
        if monitor_index < 1:
            raise ValueError("monitor_index must be 1 or greater")
        name = d.get("name")
        kill_switch = d.get("kill_switch", "f12")
        target_window_title = d.get("target_window_title", "")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("scenario name must be non-empty text")
        if not isinstance(kill_switch, str) or not kill_switch.strip():
            raise ValueError("kill_switch must be non-empty text")
        if not isinstance(target_window_title, str):
            raise ValueError("target_window_title must be text")
        scenario = Scenario(
            name=name,
            steps=steps,
            poll_interval=poll_interval,
            monitor_index=monitor_index,
            kill_switch=kill_switch,
            target_window_title=target_window_title,
        )
        validate_scenario(scenario)
        return scenario


def list_scenarios(folder=SCENARIOS_DIR):
    if not os.path.isdir(folder):
        return []
    return sorted(
        (f[:-5] for f in os.listdir(folder) if f.lower().endswith(".json")),
        key=str.casefold,
    )


def load_scenario(name, folder=SCENARIOS_DIR):
    try:
        safe_name = validate_scenario_name(name)
        path = os.path.join(folder, f"{safe_name}.json")
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("scenario file must contain a JSON object")
        scenario = Scenario.from_dict(data)
        if scenario.name.casefold() != safe_name.casefold():
            raise ValueError(
                f"scenario name '{scenario.name}' does not match filename '{safe_name}.json'"
            )
        return scenario
    except (OSError, json.JSONDecodeError, TypeError, ValueError, AttributeError, OverflowError) as exc:
        raise ValueError(f"Could not load scenario '{name}': {exc}") from exc


def validate_scenario_name(name):
    text = str(name or "")
    stripped = text.strip()
    if not stripped:
        raise ValueError("Scenario name cannot be blank.")
    if stripped != text or stripped.endswith("."):
        raise ValueError("Scenario name cannot start/end with spaces or dots.")
    if len(stripped) > 120:
        raise ValueError("Scenario name cannot be longer than 120 characters.")
    invalid = set('<>:"/\\|?*')
    if any(char in invalid or ord(char) < 32 for char in stripped):
        raise ValueError('Scenario name cannot contain <>:"/\\|?* or control characters.')
    if stripped in (".", "..") or ".." in stripped.split(os.sep):
        raise ValueError("Scenario name cannot be a relative path.")
    base = stripped.split(".")[0].upper()
    if base in WINDOWS_RESERVED_SCENARIO_NAMES:
        raise ValueError(f"Scenario name '{stripped}' is reserved by Windows.")
    return stripped


def validate_step_names(steps):
    seen = set()
    for step in steps:
        name = str(getattr(step, "name", "") or "").strip()
        if not name:
            raise ValueError("Scenario steps must have non-empty names.")
        folded_name = name.casefold()
        if folded_name in seen:
            raise ValueError(f"Scenario contains duplicate step name: '{name}'.")
        seen.add(folded_name)


def validate_scenario(scenario: Scenario, require_files=False):
    if not isinstance(scenario, Scenario):
        raise ValueError("scenario must be a Scenario object")
    validate_scenario_name(scenario.name)
    if not isinstance(scenario.steps, list):
        raise ValueError("scenario steps must be a list")
    if not all(isinstance(step, Step) for step in scenario.steps):
        raise ValueError("scenario steps must be Step objects")
    validate_step_names(scenario.steps)
    if (
        isinstance(scenario.poll_interval, bool)
        or not isinstance(scenario.poll_interval, (int, float))
        or not math.isfinite(float(scenario.poll_interval))
        or scenario.poll_interval < 0.01
    ):
        raise ValueError("poll_interval must be a finite number of at least 0.01 seconds")
    if (
        isinstance(scenario.monitor_index, bool)
        or not isinstance(scenario.monitor_index, int)
        or scenario.monitor_index < 1
    ):
        raise ValueError("monitor_index must be a whole number of 1 or greater")
    if not isinstance(scenario.kill_switch, str) or not scenario.kill_switch.strip():
        raise ValueError("kill_switch cannot be blank")
    if not isinstance(scenario.target_window_title, str):
        raise ValueError("target_window_title must be text")

    step_names = {step.name for step in scenario.steps}
    for step in scenario.steps:
        if not isinstance(step.name, str) or step.name != step.name.strip():
            raise ValueError(f"step name cannot start or end with spaces: {step.name!r}")
        if not isinstance(step.conditions, list) or not isinstance(step.actions, list):
            raise ValueError(f"step '{step.name}' conditions and actions must be lists")
        if not isinstance(step.enabled, bool) or not isinstance(step.repeatable, bool):
            raise ValueError(f"step '{step.name}' enabled/repeatable flags must be booleans")
        if step.condition_operator not in {"AND", "OR"}:
            raise ValueError(f"step '{step.name}' condition operator must be AND or OR")
        if (
            isinstance(step.cooldown, bool)
            or not isinstance(step.cooldown, (int, float))
            or not math.isfinite(float(step.cooldown))
            or step.cooldown < 0.0
        ):
            raise ValueError(f"step '{step.name}' cooldown must be a non-negative finite number")

        for condition_index, condition in enumerate(step.conditions):
            prefix = f"step '{step.name}' condition #{condition_index + 1}"
            if not isinstance(condition, ImageCondition):
                raise ValueError(f"{prefix} must be an ImageCondition")
            if condition.condition_type != "template":
                raise ValueError(f"{prefix} has unsupported type {condition.condition_type!r}")
            if not isinstance(condition.template_path, str) or not condition.template_path.strip():
                raise ValueError(f"{prefix} requires a template image")
            if not isinstance(condition.comparison_template_path, str):
                raise ValueError(f"{prefix} comparison template path must be text")
            if (
                isinstance(condition.confidence, bool)
                or not isinstance(condition.confidence, (int, float))
                or not math.isfinite(float(condition.confidence))
                or not 0.0 <= condition.confidence <= 1.0
            ):
                raise ValueError(f"{prefix} confidence must be between 0 and 1")
            if (
                isinstance(condition.comparison_margin, bool)
                or not isinstance(condition.comparison_margin, (int, float))
                or not math.isfinite(float(condition.comparison_margin))
                or not 0.0 <= condition.comparison_margin <= 1.0
            ):
                raise ValueError(f"{prefix} comparison margin must be between 0 and 1")
            _validate_region(condition.region, f"{prefix} region")
            if condition.region_mode not in {"screen", "window"}:
                raise ValueError(f"{prefix} region mode must be screen or window")
            _validate_ratio(condition.region_ratio)
            _validate_window_size(condition.region_window_size)
            if condition.region_mode == "window" and not scenario.target_window_title.strip():
                raise ValueError(f"{prefix} is window-relative but the scenario has no target window")
            if condition.region_mode == "window" and condition.region is not None:
                if (condition.region_ratio is None) != (condition.region_window_size is None):
                    raise ValueError(
                        f"{prefix} must provide both proportional region and base window size"
                    )
                if condition.region_window_size is not None:
                    left, top, width, height = condition.region
                    win_width, win_height = condition.region_window_size
                    if left < 0 or top < 0 or left + width > win_width or top + height > win_height:
                        raise ValueError(f"{prefix} region must stay inside its base target window")
            if condition.region is None and (
                condition.region_ratio is not None or condition.region_window_size is not None
            ):
                raise ValueError(f"{prefix} has resize metadata without a region")
            if condition.region_mode == "screen" and (
                condition.region_ratio is not None or condition.region_window_size is not None
            ):
                raise ValueError(f"{prefix} has window resize metadata in screen mode")
            if condition.comparison_template_path:
                target = os.path.normcase(os.path.abspath(project_path(condition.template_path)))
                rival = os.path.normcase(
                    os.path.abspath(project_path(condition.comparison_template_path))
                )
                if target == rival:
                    raise ValueError(f"{prefix} cannot compare a template against itself")
            if require_files:
                for label, raw_path in (
                    ("template", condition.template_path),
                    ("comparison template", condition.comparison_template_path),
                ):
                    if raw_path and not os.path.isfile(project_path(raw_path)):
                        raise ValueError(f"{prefix} {label} does not exist: {raw_path}")

        condition_count = len(step.conditions)
        for action_index, action in enumerate(step.actions):
            prefix = f"step '{step.name}' action #{action_index + 1}"
            if not isinstance(action, Action):
                raise ValueError(f"{prefix} must be an Action")
            if action.type not in ACTION_TYPES:
                raise ValueError(f"{prefix} has unsupported type {action.type!r}")
            if not isinstance(action.level_digit_template_dir, str):
                raise ValueError(f"{prefix} digit-template directory must be text")
            if (
                isinstance(action.row_tolerance, bool)
                or not isinstance(action.row_tolerance, int)
                or action.row_tolerance < 0
            ):
                raise ValueError(f"{prefix} row tolerance must be a non-negative whole number")
            if action.row_mode not in {"first", "all"}:
                raise ValueError(f"{prefix} row mode must be first or all")
            if action.target_choice not in {"leftmost", "rightmost", "nearest"}:
                raise ValueError(f"{prefix} has invalid row target choice")
            if action.button not in {"left", "right", "middle"}:
                raise ValueError(f"{prefix} has invalid mouse button")
            if (action.x is None) != (action.y is None):
                raise ValueError(f"{prefix} fixed point requires both x and y")
            if action.x is not None and (
                isinstance(action.x, bool)
                or isinstance(action.y, bool)
                or not isinstance(action.x, int)
                or not isinstance(action.y, int)
            ):
                raise ValueError(f"{prefix} fixed point must use whole numbers")
            if action.type == "click" and action.x is not None and action.on_condition_index is not None:
                raise ValueError(f"{prefix} cannot use both a fixed point and a condition target")
            if any(
                isinstance(value, bool) or not isinstance(value, int)
                for value in (action.offset_x, action.offset_y)
            ):
                raise ValueError(f"{prefix} click offsets must be whole numbers")
            if (
                isinstance(action.hold, bool)
                or not isinstance(action.hold, (int, float))
                or not math.isfinite(float(action.hold))
                or action.hold < 0.0
            ):
                raise ValueError(f"{prefix} key hold must be a non-negative finite number")
            if (
                isinstance(action.seconds, bool)
                or not isinstance(action.seconds, (int, float))
                or not math.isfinite(float(action.seconds))
                or action.seconds < 0.0
            ):
                raise ValueError(f"{prefix} wait must be a non-negative finite number")
            if action.min_level is not None and (
                isinstance(action.min_level, bool)
                or not isinstance(action.min_level, int)
                or action.min_level < 0
            ):
                raise ValueError(f"{prefix} minimum level cannot be negative")
            if action.max_level is not None and (
                isinstance(action.max_level, bool)
                or not isinstance(action.max_level, int)
                or action.max_level < 0
            ):
                raise ValueError(f"{prefix} maximum level cannot be negative")
            if (
                action.min_level is not None
                and action.max_level is not None
                and action.min_level > action.max_level
            ):
                raise ValueError(f"{prefix} minimum level cannot exceed maximum level")
            _validate_region(action.level_roi, f"{prefix} level ROI")
            if (
                isinstance(action.level_min_digits, bool)
                or not isinstance(action.level_min_digits, int)
                or not 1 <= action.level_min_digits <= 4
            ):
                raise ValueError(f"{prefix} level_min_digits must be between 1 and 4")
            for field_name in (
                "on_condition_index",
                "match_condition_index",
                "no_match_condition_index",
            ):
                condition_index = getattr(action, field_name)
                if condition_index is None:
                    continue
                if isinstance(condition_index, bool) or not isinstance(condition_index, int):
                    raise ValueError(f"{prefix} {field_name} must be a whole number")
                if not 0 <= condition_index < condition_count:
                    raise ValueError(
                        f"{prefix} has invalid {field_name}={condition_index}; "
                        f"the step has {condition_count} condition(s)"
                    )
            if action.type == "click_matching_row":
                if action.match_condition_index is None or action.on_condition_index is None:
                    raise ValueError(f"{prefix} requires row-reference and click-target conditions")
                if action.match_condition_index == action.on_condition_index:
                    raise ValueError(
                        f"{prefix} row-reference and click-target conditions must differ"
                    )
                if require_files and (action.min_level is not None or action.max_level is not None):
                    digit_dir = project_path(action.level_digit_template_dir)
                    if action.level_digit_template_dir and not os.path.isdir(digit_dir):
                        raise ValueError(
                            f"{prefix} digit-template directory does not exist: "
                            f"{action.level_digit_template_dir}"
                        )
            if action.type == "key" and (
                not isinstance(action.key, str) or not action.key.strip()
            ):
                raise ValueError(f"{prefix} requires a key name")
            if not isinstance(action.no_match_disable_steps, list) or not all(
                isinstance(name, str) and name.strip()
                for name in action.no_match_disable_steps
            ):
                raise ValueError(f"{prefix} disable-step names must be non-empty text")
            if action.type == "set_step" and (
                not isinstance(action.step_name, str) or action.step_name not in step_names
            ):
                raise ValueError(f"{prefix} refers to missing step '{action.step_name}'")
            missing_disable_steps = [
                name for name in action.no_match_disable_steps if name not in step_names
            ]
            if missing_disable_steps:
                raise ValueError(
                    f"{prefix} refers to missing disable step(s): "
                    f"{', '.join(missing_disable_steps)}"
                )
    return scenario


def scenario_name_exists(name, folder=SCENARIOS_DIR, exclude_name=None):
    candidate = validate_scenario_name(name).casefold()
    excluded = str(exclude_name).casefold() if exclude_name is not None else None
    return any(
        existing.casefold() == candidate and existing.casefold() != excluded
        for existing in list_scenarios(folder)
    )


def save_scenario(scenario: Scenario, folder=SCENARIOS_DIR, overwrite=True):
    validate_scenario(scenario)
    os.makedirs(folder, exist_ok=True)
    safe_name = validate_scenario_name(scenario.name)
    if not overwrite and scenario_name_exists(safe_name, folder=folder):
        raise FileExistsError(f"Scenario '{safe_name}' already exists.")
    path = os.path.join(folder, f"{safe_name}.json")
    fd, tmp_path = tempfile.mkstemp(prefix=f".{safe_name}.", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(scenario.to_dict(), f, indent=2, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise
    return path


def delete_scenario(name, folder=SCENARIOS_DIR):
    safe_name = validate_scenario_name(name)
    path = os.path.join(folder, f"{safe_name}.json")
    if os.path.exists(path):
        os.remove(path)
