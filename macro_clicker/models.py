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
from dataclasses import asdict, dataclass, field
from typing import List, Optional

import keyboard

from .detection_core import LEGACY_MACRO_MATCH_MODE, MATCH_MODE_VALUES
from .project_paths import MACRO_TEMPLATES_DIR, PROJECT_ROOT
from .project_paths import SCENARIOS_DIR as SCENARIO_PATH

ACTION_TYPES = frozenset(
    {"click", "click_matching_row", "select_rally_team", "key", "wait", "set_step"}
)
APP_DIR = str(PROJECT_ROOT)
SCENARIOS_DIR = str(SCENARIO_PATH)
TEMPLATES_DIR = str(MACRO_TEMPLATES_DIR)
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
        if os.path.normcase(
            os.path.commonpath((APP_DIR, absolute))
        ) == os.path.normcase(os.path.abspath(APP_DIR)):
            return os.path.relpath(absolute, APP_DIR).replace("\\", "/")
    except ValueError:
        pass
    return path


def _optional_int(value, default=None):
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        raise ValueError("boolean values are not valid whole numbers")
    if isinstance(value, float) and (
        not math.isfinite(value) or not value.is_integer()
    ):
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
    if isinstance(value, float) and (
        not math.isfinite(value) or not value.is_integer()
    ):
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
            isinstance(part, float)
            and (not math.isfinite(part) or not part.is_integer())
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
        if any(
            isinstance(part, bool) or not isinstance(part, (int, float))
            for part in value
        ):
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
    if any(
        isinstance(value, bool) or not isinstance(value, (int, float))
        for value in region_ratio
    ):
        raise ValueError("region_ratio must contain finite numbers")
    left, top, width, height = (float(value) for value in region_ratio)
    if not all(math.isfinite(value) for value in (left, top, width, height)):
        raise ValueError("region_ratio must contain finite numbers")
    if left < 0.0 or top < 0.0 or width <= 0.0 or height <= 0.0:
        raise ValueError(
            "region_ratio values must describe a positive region inside the window"
        )
    if left + width > 1.001 or top + height > 1.001:
        raise ValueError("region_ratio must stay inside the target window")
    return region_ratio


def _validate_window_size(size, label="region_window_size"):
    if size is None:
        return None
    if (
        not isinstance(size, (list, tuple))
        or len(size) != 2
        or any(isinstance(value, bool) or not isinstance(value, int) for value in size)
        or size[0] <= 0
        or size[1] <= 0
    ):
        raise ValueError(f"{label} must contain a positive [width, height]")
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
    condition_type: str = "template"  # OpenCV template match
    template_path: str = ""
    confidence: float = 0.85
    comparison_template_path: str = (
        ""  # optional rival template that this template must outscore
    )
    comparison_margin: float = 0.03  # minimum score lead over the rival template
    comparison_template_reference_size: Optional[List[int]] = None
    match_mode: str = LEGACY_MACRO_MATCH_MODE
    use_grayscale: bool = False
    template_reference_size: Optional[List[int]] = None
    region: Optional[List[int]] = None
    region_mode: str = (
        "screen"  # absolute screen, target-window, or selected-monitor relative
    )
    region_ratio: Optional[List[float]] = None
    region_window_size: Optional[List[int]] = (
        None  # reference window/monitor size for the region
    )
    negate: bool = False  # True = condition succeeds when the image is ABSENT

    def to_dict(self):
        data = asdict(self)
        data["template_path"] = portable_project_path(self.template_path)
        data["comparison_template_path"] = portable_project_path(
            self.comparison_template_path
        )
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
        comparison_template_reference_size = _validate_window_size(
            _optional_int_list_field(
                d,
                "comparison_template_reference_size",
                "comparison_template_reference_size",
            ),
            "comparison_template_reference_size",
        )
        match_mode = d.get("match_mode", LEGACY_MACRO_MATCH_MODE)
        if not isinstance(match_mode, str) or match_mode not in MATCH_MODE_VALUES:
            raise ValueError("condition match_mode is invalid")
        use_grayscale = _bool_value(d.get("use_grayscale"), False)
        template_reference_size = _validate_window_size(
            _optional_int_list_field(
                d,
                "template_reference_size",
                "template_reference_size",
            ),
            "template_reference_size",
        )
        region = _validate_region(_optional_int_list_field(d, "region", "region"))
        region_mode = str(d.get("region_mode", "screen") or "screen")
        if region_mode not in {"screen", "window", "monitor"}:
            raise ValueError("region_mode must be 'screen', 'window', or 'monitor'")
        region_ratio = _validate_ratio(
            _optional_float_list_field(d, "region_ratio", "region_ratio")
        )
        region_window_size = _validate_window_size(
            _optional_int_list_field(d, "region_window_size", "region_window_size")
        )
        if region_mode in {"window", "monitor"} and region is not None:
            if (region_ratio is None) != (region_window_size is None):
                raise ValueError(
                    "relative regions must provide both ratio and base size, or neither"
                )
        if region is None and (
            region_ratio is not None or region_window_size is not None
        ):
            raise ValueError("region resize metadata requires a region")
        if region_mode == "screen" and (
            region_ratio is not None or region_window_size is not None
        ):
            raise ValueError("screen regions cannot contain resize metadata")
        template_path = d.get("template_path", "")
        comparison_template_path = d.get("comparison_template_path", "")
        if not isinstance(template_path, str) or not isinstance(
            comparison_template_path, str
        ):
            raise ValueError("template paths must be text")
        return ImageCondition(
            condition_type=condition_type,
            template_path=template_path,
            confidence=confidence,
            comparison_template_path=comparison_template_path,
            comparison_margin=comparison_margin,
            comparison_template_reference_size=comparison_template_reference_size,
            match_mode=match_mode,
            use_grayscale=use_grayscale,
            template_reference_size=template_reference_size,
            region=region,
            region_mode=region_mode,
            region_ratio=region_ratio,
            region_window_size=region_window_size,
            negate=_bool_value(d.get("negate"), False),
        )


@dataclass
class Action:
    type: str = "click"  # one of ACTION_TYPES

    # click
    on_condition_index: Optional[int] = (
        None  # click center of this step's Nth condition match
    )
    match_condition_index: Optional[int] = (
        None  # for click_matching_row: row reference condition
    )
    row_tolerance: int = 60  # vertical center distance allowed for same-row matching
    row_mode: str = "first"  # "first" = first valid row, "all" = every valid row
    target_choice: str = "leftmost"  # "leftmost", "rightmost", or "nearest"
    pre_click_delay: float = (
        0.0  # wait after level selection, before revalidation/click
    )
    min_level: Optional[int] = None  # optional level filter for click_matching_row
    max_level: Optional[int] = None
    level_roi: Optional[List[int]] = (
        None  # [x, y, w, h] relative to row reference center
    )
    no_match_condition_index: Optional[int] = None  # for click_matching_row fallback
    no_match_disable_steps: List[str] = field(default_factory=list)

    # select_rally_team -- offsets/regions are relative to the anchor match center
    team_idle_template_path: str = ""
    team1_idle_template_path: str = ""
    team3_idle_template_path: str = ""
    team_idle_confidence: float = 0.85
    team1_idle_region: Optional[List[int]] = None
    team1_click_offset: Optional[List[int]] = None
    team1_max_level: Optional[int] = None
    team3_idle_region: Optional[List[int]] = None
    team3_click_offset: Optional[List[int]] = None
    team3_max_level: Optional[int] = None
    team_status_region: Optional[List[int]] = None
    team_status_reference_size: Optional[List[int]] = None
    team1_busy_template_path: str = ""
    team3_busy_template_path: str = ""
    team_busy_confidence: float = 0.85
    x: Optional[int] = None  # or a fixed point instead
    y: Optional[int] = None
    offset_x: int = 0
    offset_y: int = 0
    button: str = "left"

    # key
    key: str = ""
    hold: float = 0.0  # seconds to hold down; 0 = quick tap

    # wait
    seconds: float = 0.5

    # set_step (enable/disable another step -- this is what creates sequencing)
    step_name: str = ""
    set_enabled: bool = True

    def to_dict(self):
        data = asdict(self)
        data["team_idle_template_path"] = portable_project_path(
            self.team_idle_template_path
        )
        data["team1_idle_template_path"] = portable_project_path(
            self.team1_idle_template_path
        )
        data["team3_idle_template_path"] = portable_project_path(
            self.team3_idle_template_path
        )
        data["team1_busy_template_path"] = portable_project_path(
            self.team1_busy_template_path
        )
        data["team3_busy_template_path"] = portable_project_path(
            self.team3_busy_template_path
        )
        if has_smart_rally_team_prefilter(self):
            data["max_level"] = None
            data["team1_max_level"] = None
            data["team3_max_level"] = None
        return data

    @staticmethod
    def from_dict(d):
        d = _require_dict(d, "action")
        a = Action()
        a.type = str(d.get("type", a.type) or a.type)
        if a.type not in ACTION_TYPES:
            raise ValueError(f"unsupported action type: {a.type!r}")
        a.on_condition_index = _optional_int(
            d.get("on_condition_index"), a.on_condition_index
        )
        a.match_condition_index = _optional_int(
            d.get("match_condition_index"), a.match_condition_index
        )
        a.row_tolerance = _int_value(d.get("row_tolerance"), a.row_tolerance)
        if a.row_tolerance < 0:
            raise ValueError("row_tolerance cannot be negative")
        a.row_mode = str(d.get("row_mode", a.row_mode) or a.row_mode)
        if a.row_mode not in {"first", "all"}:
            raise ValueError("row_mode must be 'first' or 'all'")
        a.target_choice = str(
            d.get("target_choice", a.target_choice) or a.target_choice
        )
        if a.target_choice not in {"leftmost", "rightmost", "nearest"}:
            raise ValueError("target_choice must be leftmost, rightmost, or nearest")
        a.pre_click_delay = _float_value(d.get("pre_click_delay"), a.pre_click_delay)
        if a.pre_click_delay < 0.0:
            raise ValueError("pre-click delay cannot be negative")
        a.min_level = _optional_int(d.get("min_level"), a.min_level)
        a.max_level = _optional_int(d.get("max_level"), a.max_level)
        if a.min_level is not None and a.min_level < 0:
            raise ValueError("min_level cannot be negative")
        if a.max_level is not None and a.max_level < 0:
            raise ValueError("max_level cannot be negative")
        if (
            a.min_level is not None
            and a.max_level is not None
            and a.min_level > a.max_level
        ):
            raise ValueError("min_level cannot be greater than max_level")
        a.level_roi = _validate_region(
            _optional_int_list_field(d, "level_roi", "level_roi"),
            "level_roi",
        )
        a.no_match_condition_index = _optional_int(
            d.get("no_match_condition_index"), a.no_match_condition_index
        )
        a.no_match_disable_steps = _string_list(d.get("no_match_disable_steps"))
        a.team_idle_template_path = str(
            d.get("team_idle_template_path", a.team_idle_template_path) or ""
        )
        a.team1_idle_template_path = str(
            d.get("team1_idle_template_path", a.team1_idle_template_path) or ""
        )
        a.team3_idle_template_path = str(
            d.get("team3_idle_template_path", a.team3_idle_template_path) or ""
        )
        a.team_idle_confidence = _float_value(
            d.get("team_idle_confidence"), a.team_idle_confidence
        )
        if not 0.0 <= a.team_idle_confidence <= 1.0:
            raise ValueError("team idle confidence must be between 0 and 1")
        a.team1_idle_region = _validate_region(
            _optional_int_list_field(d, "team1_idle_region", "team1_idle_region"),
            "team1_idle_region",
        )
        a.team3_idle_region = _validate_region(
            _optional_int_list_field(d, "team3_idle_region", "team3_idle_region"),
            "team3_idle_region",
        )
        a.team1_click_offset = _optional_int_list_field(
            d, "team1_click_offset", "team1_click_offset"
        )
        a.team3_click_offset = _optional_int_list_field(
            d, "team3_click_offset", "team3_click_offset"
        )
        for label, value in (
            ("team1_click_offset", a.team1_click_offset),
            ("team3_click_offset", a.team3_click_offset),
        ):
            if value is not None and len(value) != 2:
                raise ValueError(f"{label} must contain [x, y]")
        a.team1_max_level = _optional_int(d.get("team1_max_level"), a.team1_max_level)
        a.team3_max_level = _optional_int(d.get("team3_max_level"), a.team3_max_level)
        for label, value in (
            ("team1_max_level", a.team1_max_level),
            ("team3_max_level", a.team3_max_level),
        ):
            if value is not None and value < 0:
                raise ValueError(f"{label} cannot be negative")
        a.team_status_region = _validate_region(
            _optional_int_list_field(d, "team_status_region", "team_status_region"),
            "team_status_region",
        )
        a.team_status_reference_size = _validate_window_size(
            _optional_int_list_field(
                d,
                "team_status_reference_size",
                "team_status_reference_size",
            ),
            "team_status_reference_size",
        )
        a.team1_busy_template_path = str(
            d.get("team1_busy_template_path", a.team1_busy_template_path) or ""
        )
        a.team3_busy_template_path = str(
            d.get("team3_busy_template_path", a.team3_busy_template_path) or ""
        )
        a.team_busy_confidence = _float_value(
            d.get("team_busy_confidence"), a.team_busy_confidence
        )
        if not 0.0 <= a.team_busy_confidence <= 1.0:
            raise ValueError("team busy confidence must be between 0 and 1")
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
            target = (
                f"condition #{self.on_condition_index}"
                if self.on_condition_index is not None
                else f"({self.x}, {self.y})"
            )
            return f"Click {target}  [{self.button}]"
        if self.type == "click_matching_row":
            scope = "all rows" if self.row_mode == "all" else "first row"
            level_parts = []
            if self.min_level is not None:
                level_parts.append(f">= {self.min_level}")
            if self.max_level is not None and not has_smart_rally_team_prefilter(self):
                level_parts.append(f"<= {self.max_level}")
            level = f", level {' and '.join(level_parts)}" if level_parts else ""
            fallback = (
                f"; no match click condition #{self.no_match_condition_index}"
                if self.no_match_condition_index is not None
                else ""
            )
            delay = (
                f"; wait {self.pre_click_delay:g}s before click"
                if self.pre_click_delay
                else ""
            )
            availability = (
                "; adapt level to idle rally teams"
                if self.team1_busy_template_path and self.team3_busy_template_path
                else ""
            )
            return (
                f"Click {self.target_choice} condition #{self.on_condition_index} matching "
                f"{scope} of condition #{self.match_condition_index}{level}{delay}{fallback}"
                f"{availability}  [{self.button}]"
            )
        if self.type == "select_rally_team":
            team3_limit = (
                "unlimited"
                if self.team3_max_level is None
                else f"max level {self.team3_max_level}"
            )
            team1_limit = (
                "unlimited"
                if self.team1_max_level is None
                else f"max level {self.team1_max_level}"
            )
            return f"Select idle Team 3 ({team3_limit}), then Team 1 ({team1_limit})"
        if self.type == "key":
            extra = f" (hold {self.hold}s)" if self.hold else ""
            return f"Press key '{self.key}'{extra}"
        if self.type == "wait":
            return f"Wait {self.seconds}s"
        if self.type == "set_step":
            verb = "Enable" if self.set_enabled else "Disable"
            return f"{verb} step '{self.step_name}'"
        return self.type


def has_smart_rally_team_prefilter(action: Action) -> bool:
    """Return whether a matching-row action declares smart team availability."""
    if action.type != "click_matching_row":
        return False
    return any(
        (
            action.team_status_region is not None,
            action.team_status_reference_size is not None,
            bool(action.team1_busy_template_path.strip()),
            bool(action.team3_busy_template_path.strip()),
        )
    )


@dataclass
class Step:
    name: str
    conditions: List[ImageCondition] = field(default_factory=list)
    actions: List[Action] = field(default_factory=list)
    condition_operator: str = "AND"  # "AND" = all conditions must hold, "OR" = any one
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
    start_hotkey: str = "f8"
    kill_switch: str = "f12"
    target_window_title: str = ""
    diagnostics_enabled: bool = True

    def to_dict(self):
        return {
            "name": self.name,
            "steps": [s.to_dict() for s in self.steps],
            "poll_interval": self.poll_interval,
            "monitor_index": self.monitor_index,
            "start_hotkey": self.start_hotkey,
            "kill_switch": self.kill_switch,
            "target_window_title": self.target_window_title,
            "diagnostics_enabled": self.diagnostics_enabled,
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
        start_hotkey = d.get("start_hotkey", "f8")
        kill_switch = d.get("kill_switch", "f12")
        target_window_title = d.get("target_window_title", "")
        diagnostics_enabled = _bool_value(d.get("diagnostics_enabled"), True)
        if not isinstance(name, str) or not name.strip():
            raise ValueError("scenario name must be non-empty text")
        if not isinstance(start_hotkey, str) or not start_hotkey.strip():
            raise ValueError("start_hotkey must be non-empty text")
        if not isinstance(kill_switch, str) or not kill_switch.strip():
            raise ValueError("kill_switch must be non-empty text")
        if not isinstance(target_window_title, str):
            raise ValueError("target_window_title must be text")
        scenario = Scenario(
            name=name,
            steps=steps,
            poll_interval=poll_interval,
            monitor_index=monitor_index,
            start_hotkey=start_hotkey,
            kill_switch=kill_switch,
            target_window_title=target_window_title,
            diagnostics_enabled=diagnostics_enabled,
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
    except (
        OSError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
        AttributeError,
        OverflowError,
    ) as exc:
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
        raise ValueError(
            'Scenario name cannot contain <>:"/\\|?* or control characters.'
        )
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
        raise ValueError(
            "poll_interval must be a finite number of at least 0.01 seconds"
        )
    if (
        isinstance(scenario.monitor_index, bool)
        or not isinstance(scenario.monitor_index, int)
        or scenario.monitor_index < 1
    ):
        raise ValueError("monitor_index must be a whole number of 1 or greater")
    if not isinstance(scenario.start_hotkey, str) or not scenario.start_hotkey.strip():
        raise ValueError("start_hotkey cannot be blank")
    if not isinstance(scenario.kill_switch, str) or not scenario.kill_switch.strip():
        raise ValueError("kill_switch cannot be blank")
    if (
        scenario.start_hotkey.strip().casefold()
        == scenario.kill_switch.strip().casefold()
    ):
        raise ValueError("start_hotkey and kill_switch must use different keys")
    for label, hotkey in (
        ("start_hotkey", scenario.start_hotkey),
        ("kill_switch", scenario.kill_switch),
    ):
        try:
            keyboard.parse_hotkey(hotkey.strip())
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{label} is invalid: {exc}") from exc
    if not isinstance(scenario.target_window_title, str):
        raise ValueError("target_window_title must be text")
    if not isinstance(scenario.diagnostics_enabled, bool):
        raise ValueError("diagnostics_enabled must be a boolean")

    step_names = {step.name for step in scenario.steps}
    select_rally_team_count = 0
    smart_rally_team_prefilter_configured = False
    for step in scenario.steps:
        if not isinstance(step.name, str) or step.name != step.name.strip():
            raise ValueError(
                f"step name cannot start or end with spaces: {step.name!r}"
            )
        if not isinstance(step.conditions, list) or not isinstance(step.actions, list):
            raise ValueError(f"step '{step.name}' conditions and actions must be lists")
        if not isinstance(step.enabled, bool) or not isinstance(step.repeatable, bool):
            raise ValueError(
                f"step '{step.name}' enabled/repeatable flags must be booleans"
            )
        if step.condition_operator not in {"AND", "OR"}:
            raise ValueError(f"step '{step.name}' condition operator must be AND or OR")
        if (
            isinstance(step.cooldown, bool)
            or not isinstance(step.cooldown, (int, float))
            or not math.isfinite(float(step.cooldown))
            or step.cooldown < 0.0
        ):
            raise ValueError(
                f"step '{step.name}' cooldown must be a non-negative finite number"
            )

        for condition_index, condition in enumerate(step.conditions):
            prefix = f"step '{step.name}' condition #{condition_index + 1}"
            if not isinstance(condition, ImageCondition):
                raise ValueError(f"{prefix} must be an ImageCondition")
            if condition.condition_type != "template":
                raise ValueError(
                    f"{prefix} has unsupported type {condition.condition_type!r}"
                )
            if (
                not isinstance(condition.template_path, str)
                or not condition.template_path.strip()
            ):
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
            if condition.match_mode not in MATCH_MODE_VALUES:
                raise ValueError(f"{prefix} has an invalid detection type")
            if not isinstance(condition.use_grayscale, bool):
                raise ValueError(f"{prefix} grayscale setting must be a boolean")
            _validate_window_size(
                condition.template_reference_size,
                "template_reference_size",
            )
            _validate_window_size(
                condition.comparison_template_reference_size,
                "comparison_template_reference_size",
            )
            if (
                condition.comparison_template_reference_size is not None
                and not condition.comparison_template_path.strip()
            ):
                raise ValueError(
                    f"{prefix} has comparison reference metadata without a comparison template"
                )
            _validate_region(condition.region, f"{prefix} region")
            if condition.region_mode not in {"screen", "window", "monitor"}:
                raise ValueError(
                    f"{prefix} region mode must be screen, window, or monitor"
                )
            _validate_ratio(condition.region_ratio)
            _validate_window_size(condition.region_window_size)
            if (
                condition.region_mode == "window"
                and not scenario.target_window_title.strip()
            ):
                raise ValueError(
                    f"{prefix} is window-relative but the scenario has no target window"
                )
            if (
                condition.region_mode in {"window", "monitor"}
                and condition.region is not None
            ):
                if (condition.region_ratio is None) != (
                    condition.region_window_size is None
                ):
                    raise ValueError(
                        f"{prefix} must provide both proportional region and base size"
                    )
                if condition.region_window_size is not None:
                    left, top, width, height = condition.region
                    win_width, win_height = condition.region_window_size
                    if (
                        left < 0
                        or top < 0
                        or left + width > win_width
                        or top + height > win_height
                    ):
                        raise ValueError(
                            f"{prefix} region must stay inside its base area"
                        )
            if condition.region is None and (
                condition.region_ratio is not None
                or condition.region_window_size is not None
            ):
                raise ValueError(f"{prefix} has resize metadata without a region")
            if condition.region_mode == "screen" and (
                condition.region_ratio is not None
                or condition.region_window_size is not None
            ):
                raise ValueError(f"{prefix} has resize metadata in screen mode")
            if condition.comparison_template_path:
                target = os.path.normcase(
                    os.path.abspath(project_path(condition.template_path))
                )
                rival = os.path.normcase(
                    os.path.abspath(project_path(condition.comparison_template_path))
                )
                if target == rival:
                    raise ValueError(
                        f"{prefix} cannot compare a template against itself"
                    )
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
            if action.type == "select_rally_team":
                select_rally_team_count += 1
            if (
                isinstance(action.row_tolerance, bool)
                or not isinstance(action.row_tolerance, int)
                or action.row_tolerance < 0
            ):
                raise ValueError(
                    f"{prefix} row tolerance must be a non-negative whole number"
                )
            if action.row_mode not in {"first", "all"}:
                raise ValueError(f"{prefix} row mode must be first or all")
            if action.target_choice not in {"leftmost", "rightmost", "nearest"}:
                raise ValueError(f"{prefix} has invalid row target choice")
            if action.button not in {"left", "right", "middle"}:
                raise ValueError(f"{prefix} has invalid mouse button")
            if not isinstance(action.set_enabled, bool):
                raise ValueError(f"{prefix} set_enabled must be a boolean")
            if (action.x is None) != (action.y is None):
                raise ValueError(f"{prefix} fixed point requires both x and y")
            if action.x is not None and (
                isinstance(action.x, bool)
                or isinstance(action.y, bool)
                or not isinstance(action.x, int)
                or not isinstance(action.y, int)
            ):
                raise ValueError(f"{prefix} fixed point must use whole numbers")
            if (
                action.type == "click"
                and action.x is not None
                and action.on_condition_index is not None
            ):
                raise ValueError(
                    f"{prefix} cannot use both a fixed point and a condition target"
                )
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
                raise ValueError(
                    f"{prefix} key hold must be a non-negative finite number"
                )
            if (
                isinstance(action.pre_click_delay, bool)
                or not isinstance(action.pre_click_delay, (int, float))
                or not math.isfinite(float(action.pre_click_delay))
                or action.pre_click_delay < 0.0
            ):
                raise ValueError(
                    f"{prefix} pre-click delay must be a non-negative finite number"
                )
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
                isinstance(action.team_idle_confidence, bool)
                or not isinstance(action.team_idle_confidence, (int, float))
                or not math.isfinite(float(action.team_idle_confidence))
                or not 0.0 <= action.team_idle_confidence <= 1.0
            ):
                raise ValueError(
                    f"{prefix} team idle confidence must be between 0 and 1"
                )
            for field_name in ("team1_idle_region", "team3_idle_region"):
                _validate_region(
                    getattr(action, field_name),
                    f"{prefix} {field_name.replace('_', ' ')}",
                )
            for field_name in ("team1_click_offset", "team3_click_offset"):
                value = getattr(action, field_name)
                if value is not None and (
                    not isinstance(value, list)
                    or len(value) != 2
                    or any(
                        isinstance(part, bool) or not isinstance(part, int)
                        for part in value
                    )
                ):
                    raise ValueError(f"{prefix} {field_name} must contain [x, y]")
            for field_name in ("team1_max_level", "team3_max_level"):
                value = getattr(action, field_name)
                if value is not None and (
                    isinstance(value, bool) or not isinstance(value, int) or value < 0
                ):
                    raise ValueError(f"{prefix} {field_name} cannot be negative")
            _validate_region(action.team_status_region, f"{prefix} team status region")
            _validate_window_size(
                action.team_status_reference_size,
                f"{prefix} team status reference size",
            )
            if (
                isinstance(action.team_busy_confidence, bool)
                or not isinstance(action.team_busy_confidence, (int, float))
                or not math.isfinite(float(action.team_busy_confidence))
                or not 0.0 <= action.team_busy_confidence <= 1.0
            ):
                raise ValueError(
                    f"{prefix} team busy confidence must be between 0 and 1"
                )
            for field_name in (
                "team1_busy_template_path",
                "team3_busy_template_path",
            ):
                if not isinstance(getattr(action, field_name), str):
                    raise ValueError(f"{prefix} {field_name} must be text")
            for field_name in (
                "on_condition_index",
                "match_condition_index",
                "no_match_condition_index",
            ):
                condition_index = getattr(action, field_name)
                if condition_index is None:
                    continue
                if isinstance(condition_index, bool) or not isinstance(
                    condition_index, int
                ):
                    raise ValueError(f"{prefix} {field_name} must be a whole number")
                if not 0 <= condition_index < condition_count:
                    raise ValueError(
                        f"{prefix} has invalid {field_name}={condition_index}; "
                        f"the step has {condition_count} condition(s)"
                    )
            if action.type == "click_matching_row":
                if (
                    action.match_condition_index is None
                    or action.on_condition_index is None
                ):
                    raise ValueError(
                        f"{prefix} requires row-reference and click-target conditions"
                    )
                if action.match_condition_index == action.on_condition_index:
                    raise ValueError(
                        f"{prefix} row-reference and click-target conditions must differ"
                    )
                team_status_configured = has_smart_rally_team_prefilter(action)
                if team_status_configured:
                    smart_rally_team_prefilter_configured = True
                    for field_name in (
                        "team_status_region",
                        "team_status_reference_size",
                        "team1_busy_template_path",
                        "team3_busy_template_path",
                    ):
                        value = getattr(action, field_name)
                        if value is None or (
                            isinstance(value, str) and not value.strip()
                        ):
                            raise ValueError(f"{prefix} requires {field_name}")
                    if require_files:
                        for raw_path in (
                            action.team1_busy_template_path,
                            action.team3_busy_template_path,
                        ):
                            if not os.path.isfile(project_path(raw_path)):
                                raise ValueError(
                                    f"{prefix} team-busy template does not exist: "
                                    f"{raw_path}"
                                )
            if action.type == "select_rally_team":
                if action.on_condition_index is None:
                    raise ValueError(f"{prefix} requires an anchor condition")
                effective_idle_paths = {
                    "Team 1": (
                        action.team1_idle_template_path
                        or action.team_idle_template_path
                    ),
                    "Team 3": (
                        action.team3_idle_template_path
                        or action.team_idle_template_path
                    ),
                }
                for team_label, raw_path in effective_idle_paths.items():
                    if not isinstance(raw_path, str) or not raw_path.strip():
                        raise ValueError(
                            f"{prefix} requires a {team_label} idle-team template"
                        )
                for field_name in (
                    "team1_idle_region",
                    "team1_click_offset",
                    "team3_idle_region",
                    "team3_click_offset",
                ):
                    if getattr(action, field_name) is None:
                        raise ValueError(f"{prefix} requires {field_name}")
                if require_files:
                    for team_label, raw_path in effective_idle_paths.items():
                        if not os.path.isfile(project_path(raw_path)):
                            raise ValueError(
                                f"{prefix} {team_label} idle-team template does "
                                f"not exist: {raw_path}"
                            )
            if action.type == "key" and (
                not isinstance(action.key, str) or not action.key.strip()
            ):
                raise ValueError(f"{prefix} requires a key name")
            if action.type == "key":
                try:
                    keyboard.parse_hotkey(action.key.strip())
                except (TypeError, ValueError) as exc:
                    raise ValueError(
                        f"{prefix} has an invalid key name: {exc}"
                    ) from exc
            if not isinstance(action.no_match_disable_steps, list) or not all(
                isinstance(name, str) and name.strip()
                for name in action.no_match_disable_steps
            ):
                raise ValueError(f"{prefix} disable-step names must be non-empty text")
            if action.type == "set_step" and (
                not isinstance(action.step_name, str)
                or action.step_name not in step_names
            ):
                raise ValueError(
                    f"{prefix} refers to missing step '{action.step_name}'"
                )
            missing_disable_steps = [
                name for name in action.no_match_disable_steps if name not in step_names
            ]
            if missing_disable_steps:
                raise ValueError(
                    f"{prefix} refers to missing disable step(s): "
                    f"{', '.join(missing_disable_steps)}"
                )
    if smart_rally_team_prefilter_configured and select_rally_team_count != 1:
        raise ValueError(
            "A smart rally-team availability prefilter requires exactly one "
            "select_rally_team action."
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
