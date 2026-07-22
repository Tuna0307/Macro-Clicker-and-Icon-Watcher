# Two-Team Limits Only Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the visible Team 1 and Team 3 maximum-level fields the sole maximum-level authority in smart two-team rally mode while preserving the ordinary row maximum in one-team mode.

**Architecture:** Add one shared predicate for recognizing a smart rally row, then use it consistently in validation, runtime OCR preparation, atomic screenshot sizing, serialization, and the editor. Smart availability resolves exactly one `select_rally_team` action, derives a cap only from idle teams and those visible limits, and replaces rather than clamps the ordinary row maximum. The existing final Team 3-then-Team 1 dispatch order and one-capture handoff remain unchanged.

**Tech Stack:** Python 3, dataclasses and JSON, Tkinter/ttk, OpenCV-backed template matching, PaddleOCR through `LevelOcrReader`, unittest-style pytest tests, Ruff, and mypy.

## Global Constraints

- In smart two-team mode, only `select_rally_team.team1_max_level` and `select_rally_team.team3_max_level` control maximum eligible levels.
- A blank visible team maximum is represented by `None` and means unlimited.
- Team 3 remains the preferred candidate; Team 1 remains the immediate fallback.
- Preserve `click_matching_row.min_level` behavior; this change removes only redundant maximum-level authorities.
- Preserve the ordinary `click_matching_row.max_level` behavior for rows without smart Team 1/Team 3 availability configuration.
- Do not add another availability capture, OCR read, wait, or click.
- Preserve the cached pre-entry availability frame and the existing pre-click-delay revalidation behavior.
- Do not change OCR recognition, portrait templates, confidence thresholds, click offsets, or team order.
- Do not modify or stage the user's existing local changes in `alerts/settings.json` or `scenarios/Rally Gold Mob.json`.
- Do not remove the one-team scenario until live two-team testing succeeds.
- Do not add per-team minimum-level controls.

---

## File Map

- `macro_clicker/models.py`: shared smart-row predicate, blank team-limit defaults, exact-one-selector validation, canonical smart-row serialization, and fallback action summary.
- `macro_clicker/rally_matching.py`: selector-only limit resolution, idle-team cap calculation, smart override semantics, level handoff, diagnostics, and OCR warm-up detection.
- `macro_clicker/engine.py`: include the level OCR crop in an atomic snapshot even when a smart row has no ordinary min/max.
- `macro_clicker/editors.py`: disable the redundant smart-row maximum field and clear obsolete row maximum values when the row is saved.
- `macro_clicker/ui_components.py`: pure UI-state/disclosure helpers and unlimited limit labels.
- `scenarios/Rally gold mob_ 2 team.json`: persist null row maximums while retaining selector Team 1 = 65 and Team 3 = 50.
- `tests/test_models_validation.py`: model defaults, null round-trip, smart-row migration, and selector-count validation.
- `tests/test_rally_team_selection.py`: selector authority, unlimited behavior, diagnostics, one-team boundary, cached capture, level handoff, and scenario configuration.
- `tests/test_matching_row_action.py`: atomic snapshot coverage for a fully unbounded smart row.
- `tests/test_engine_performance.py`: OCR warm-up recognition for a smart row with blank ordinary limits.
- `tests/test_ui_components.py`: editor-state and unlimited-summary behavior without brittle Tk automation.

### Task 1: Define Smart-Mode Identity and Validation

**Files:**
- Modify: `macro_clicker/models.py:351-366, 600-601, 809-810, 1062-1097, 1157-1161`
- Modify: `tests/test_models_validation.py:140-151`
- Modify: `tests/test_rally_team_selection.py:344-373`

**Interfaces:**
- Produces: `has_smart_rally_team_prefilter(action: Action) -> bool`.
- Produces: `Action.team1_max_level` and `Action.team3_max_level` default to `None`.
- Produces: smart scenarios validate only when they contain exactly one `select_rally_team` action.
- Consumes: the existing four smart availability fields: `team_status_region`, `team_status_reference_size`, `team1_busy_template_path`, and `team3_busy_template_path`.

- [ ] **Step 1: Write failing model-default and validation tests**

Add this method to `ModelValidationTests` in `tests/test_models_validation.py`:

```python
    def test_rally_team_maximums_default_to_unlimited_and_round_trip_null(self):
        defaults = Action(type="select_rally_team")
        restored = Action.from_dict(
            {
                "type": "select_rally_team",
                "team1_max_level": None,
                "team3_max_level": None,
            }
        )

        self.assertIsNone(defaults.team1_max_level)
        self.assertIsNone(defaults.team3_max_level)
        self.assertIsNone(restored.team1_max_level)
        self.assertIsNone(restored.team3_max_level)
```

In `tests/test_rally_team_selection.py`, replace the two selector-count tests with:

```python
    def test_smart_availability_prefilter_rejects_multiple_team_selectors(self):
        scenario = load_scenario("Rally gold mob_ 2 team")
        selector_step = next(
            step
            for step in scenario.steps
            if any(action.type == "select_rally_team" for action in step.actions)
        )
        selector_action = next(
            action
            for action in selector_step.actions
            if action.type == "select_rally_team"
        )
        selector_step.actions.append(Action.from_dict(selector_action.to_dict()))

        with self.assertRaisesRegex(
            ValueError,
            "smart rally-team availability prefilter.*exactly one select_rally_team",
        ):
            validate_scenario(scenario)

    def test_smart_availability_prefilter_requires_a_team_selector(self):
        scenario = load_scenario("Rally gold mob_ 2 team")
        for step in scenario.steps:
            step.actions = [
                action
                for action in step.actions
                if action.type != "select_rally_team"
            ]

        with self.assertRaisesRegex(
            ValueError,
            "smart rally-team availability prefilter.*exactly one select_rally_team",
        ):
            validate_scenario(scenario)
```

- [ ] **Step 2: Run the new tests and confirm the old behavior fails**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_models_validation.py::ModelValidationTests::test_rally_team_maximums_default_to_unlimited_and_round_trip_null tests/test_rally_team_selection.py::RallyTeamSelectionTests::test_smart_availability_prefilter_rejects_multiple_team_selectors tests/test_rally_team_selection.py::RallyTeamSelectionTests::test_smart_availability_prefilter_requires_a_team_selector -q
```

Expected: failures show fixed defaults `65/45`, the old "at most one" message, and acceptance of zero selectors.

- [ ] **Step 3: Add the shared predicate and blank model defaults**

Change the two dataclass defaults in `Action`:

```python
    team1_max_level: Optional[int] = None
    team3_idle_region: Optional[List[int]] = None
    team3_click_offset: Optional[List[int]] = None
    team3_max_level: Optional[int] = None
```

Add this function immediately after the `Action` class and before `Step`:

```python
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
```

The predicate intentionally detects partial configuration so existing validation can report the missing calibration field instead of silently treating the row as one-team mode.

- [ ] **Step 4: Require exactly one selector when the predicate is true**

In `validate_scenario`, replace the local four-field `any` expression with:

```python
                team_status_configured = has_smart_rally_team_prefilter(action)
```

Replace the final selector-count check with:

```python
    if smart_rally_team_prefilter_configured and select_rally_team_count != 1:
        raise ValueError(
            "A smart rally-team availability prefilter requires exactly one "
            "select_rally_team action."
        )
```

Do not restrict `select_rally_team` actions in scenarios that have no smart availability row; this keeps existing standalone selector validation behavior intact.

- [ ] **Step 5: Run focused model and selector-count tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_models_validation.py tests/test_rally_team_selection.py -q
```

Expected: all tests pass after updating only the two intentionally inverted selector-count assertions; runtime authority tests still describe the pre-change behavior until Task 2.

- [ ] **Step 6: Commit the model boundary**

```powershell
git add -- macro_clicker/models.py tests/test_models_validation.py tests/test_rally_team_selection.py
git commit -m "feat: require one selector for smart rally routing"
```

### Task 2: Make Visible Selector Limits the Sole Runtime Authority

**Files:**
- Modify: `macro_clicker/rally_matching.py:13-14, 253-348, 356-473, 997-1005`
- Modify: `macro_clicker/engine.py:28-36, 420-437`
- Modify: `tests/test_rally_team_selection.py:511-793, 846-953`
- Modify: `tests/test_matching_row_action.py:12-140`
- Modify: `tests/test_engine_performance.py:328-362`

**Interfaces:**
- Consumes: `has_smart_rally_team_prefilter(action)` from Task 1.
- Preserves: `_TEAM_LEVEL_CAP_UNSET` means ordinary one-team behavior, `None` means both dispatch teams are busy, and `_TEAM_LEVEL_CAP_UNBOUNDED == "unbounded"` means at least one idle team has a blank visible maximum.
- Produces: `_resolve_rally_team_level_limits(action)` returns limits from the sole selector and never from row fields.
- Produces: `_row_level_status` with `max_level_override` always reads and returns a level for smart numeric and smart unbounded caps.

- [ ] **Step 1: Invert the cap-authority regressions**

In `tests/test_rally_team_selection.py`:

1. Rename `test_blank_visible_team_limit_uses_joining_maximum_when_team_is_idle` to `test_blank_visible_team_limit_is_unbounded_when_that_team_is_idle` and replace its setup assertions and result assertion with:

```python
        selector_action.team3_max_level = None
        joining_action.max_level = 12
        joining_action.team1_max_level = 11
        joining_action.team3_max_level = 10

        self.assertEqual(
            engine._available_rally_team_level_cap(joining_action),
            "unbounded",
        )
        self.assertEqual(
            engine._last_rally_team_availability["level_limits"],
            {1: 65, 3: None},
        )
        self.assertEqual(
            engine._last_rally_team_availability["level_limits_source"],
            "select_rally_team",
        )
```

Keep the existing engine stubs between those two blocks unchanged; they make Team 1 busy and Team 3 idle.

2. Rename `test_joining_maximum_bounds_selector_prefilter` to `test_selector_limit_above_joining_maximum_is_not_clamped`, set stale row limits, and change the final assertion:

```python
        selector_action.team3_max_level = 70
        joining_action.max_level = 30
        joining_action.team1_max_level = 29
        joining_action.team3_max_level = 28

        self.assertEqual(engine._available_rally_team_level_cap(joining_action), 70)
```

3. Replace `test_legacy_row_action_limits_apply_without_a_team_selector` with a defensive runtime check:

```python
    def test_smart_limit_resolver_rejects_a_missing_selector_at_runtime(self):
        row_action = Action(
            type="click_matching_row",
            team_status_region=[0, 0, 100, 100],
            team_status_reference_size=[100, 100],
            team1_busy_template_path="team1-busy.png",
            team3_busy_template_path="team3-busy.png",
            team1_max_level=65,
            team3_max_level=45,
        )
        engine = object.__new__(MacroEngine)
        engine.scenario = Scenario(
            name="Missing selector",
            steps=[Step(name="Joining", actions=[row_action])],
        )

        with self.assertRaisesRegex(ValueError, "exactly one select_rally_team"):
            engine._rally_team_level_limits(row_action)
```

4. Keep the runtime multiple-selector test, but match `"exactly one select_rally_team"` instead of `"multiple select_rally_team"`.

5. Extend `test_busy_portraits_adapt_the_row_level_cap` after its existing four assertions so each visible blank limit is proven unlimited only when that team is idle:

```python
        selector_action = next(
            action
            for step in scenario.steps
            for action in step.actions
            if action.type == "select_rally_team"
        )
        selector_action.team3_max_level = None
        self.assertEqual(
            level_cap(team1_busy=True, team3_busy=False),
            "unbounded",
        )
        selector_action.team3_max_level = 50
        selector_action.team1_max_level = None
        self.assertEqual(
            level_cap(team1_busy=False, team3_busy=True),
            "unbounded",
        )
```

- [ ] **Step 2: Add the one-team boundary regression**

Add to `RallyTeamSelectionTests`:

```python
    def test_original_one_team_row_maximum_remains_active(self):
        scenario = load_scenario("Rally Gold Mob")
        action = next(
            action
            for step in scenario.steps
            if step.name == "Joining"
            for action in step.actions
            if action.type == "click_matching_row"
        )
        self.assertIsNotNone(action.max_level)
        self.assertFalse(action.team1_busy_template_path)
        self.assertFalse(action.team3_busy_template_path)

        engine = object.__new__(MacroEngine)
        engine._stop_event = type("Stop", (), {"is_set": lambda self: False})()
        engine.log = lambda _message: None
        rejected_level = action.max_level + 1
        engine._read_level_for_row = lambda _action, _reference: rejected_level

        self.assertEqual(
            engine._row_level_status(action, {"center": (100, 100)}),
            ("ineligible", rejected_level),
        )
```

This test reads the existing value dynamically, so it protects both the committed one-team maximum and the user's uncommitted local tuning without modifying that file.

- [ ] **Step 3: Strengthen the fully unbounded cached-handoff regression**

In `test_unbounded_pre_entry_availability_is_cached_and_reused`, remove:

```python
        row_action.min_level = 0
        row_action.max_level = None
```

Keep the shipped row maximum stale, make Team 3 visibly unlimited, and count OCR calls:

```python
        selector_action.team3_max_level = None
        level_reads = []
        engine._read_level_for_row = (
            lambda _action, _reference: level_reads.append(99) or 99
        )
```

After calling `_row_level_status`, add:

```python
        self.assertEqual(level_reads, [99])
        self.assertEqual(level_status, ("eligible", 99))
```

Then pass that exact level through the existing row action without another OCR call:

```python
        joining_step = steps["Joining"]
        row_matches = {
            0: [{"center": (100, 100)}],
            1: [{"center": (300, 100), "scale_x": 1.0, "scale_y": 1.0}],
        }
        engine._pending_rally_level = None
        engine._matching_row_reuse_context = (joining_step, row_action, object())
        engine._find_matching_row_selections = lambda *_args, **_kwargs: (
            [
                {
                    "reference": row_matches[0][0],
                    "target": row_matches[1][0],
                    "level": level_status[1],
                }
            ],
            False,
        )
        engine._record_matching_row_diagnostic = lambda *_args, **_kwargs: None
        engine._click_point = lambda _x, _y, _button: True

        self.assertTrue(
            engine._run_action(
                joining_step,
                row_action,
                {0: (100, 100), 1: (300, 100)},
                row_matches,
            )
        )
        self.assertEqual(engine._pending_rally_level, 99)
        self.assertEqual(level_reads, [99])
```

The existing assertions for one availability capture, cached object identity, `"unbounded"`, and JSON-safe diagnostics remain in place.

- [ ] **Step 4: Add atomic-snapshot and OCR-warm-up tests for blank ordinary limits**

Add this method to `MatchingRowActionTests` in `tests/test_matching_row_action.py`:

```python
    def test_unbounded_smart_row_snapshot_still_contains_level_crops(self):
        engine = object.__new__(MacroEngine)
        engine.scenario = Scenario(name="smart atomic", monitor_index=1)
        engine._stop_event = type("Stop", (), {"is_set": lambda self: False})()
        engine._all_match_indices = {}
        engine._evaluate_uses_frame_cache = True
        engine._level_offset_cache = {}
        engine._window_rect_lookup_cache = None
        engine._matching_row_snapshot = None
        captures = []

        def grab(region):
            captures.append(region)
            return (
                np.full((region[3], region[2], 3), len(captures), dtype=np.uint8),
                region[0],
                region[1],
            )

        def evaluate(index, _condition, frame, _off_x, _off_y, collect_all):
            self.assertTrue(collect_all)
            self.assertEqual(int(frame[0, 0, 0]), 1)
            center = (110, 110) if index == 0 else (210, 110)
            return True, [{"center": center, "box": (*center, *center)}]

        engine._grab = grab
        engine._resolve_capture_region = lambda condition: condition.region
        engine._evaluate_template_condition = evaluate
        action = Action(
            type="click_matching_row",
            match_condition_index=0,
            on_condition_index=1,
            min_level=None,
            max_level=None,
            level_roi=[0, 30, 20, 20],
            team_status_region=[0, 0, 100, 100],
            team_status_reference_size=[1920, 1080],
            team1_busy_template_path="team1-busy.png",
            team3_busy_template_path="team3-busy.png",
        )
        step = Step(
            name="Joining",
            conditions=[
                ImageCondition(template_path="mob.png", region=[100, 100, 20, 20]),
                ImageCondition(template_path="join.png", region=[200, 100, 20, 20]),
            ],
            actions=[action],
        )

        refreshed = engine._refresh_click_matching_row_matches(step, action)
        self.assertIsNotNone(refreshed)
        _points, matches = refreshed
        candidates = engine._capture_level_crop_candidates(action, matches[0][0])

        self.assertEqual(len(captures), 1)
        self.assertEqual(len(candidates), 6)
        self.assertTrue(
            all(np.all(frame == 1) for _offset, _rect, frame in candidates)
        )
```

Add this method to `EnginePerformanceTests` in `tests/test_engine_performance.py`:

```python
    def test_smart_row_with_blank_ordinary_limits_still_uses_level_ocr(self):
        engine = object.__new__(MacroEngine)
        engine.scenario = Scenario(
            name="Smart OCR",
            steps=[
                Step(
                    name="Joining",
                    actions=[
                        Action(
                            type="click_matching_row",
                            team_status_region=[0, 0, 100, 100],
                            team_status_reference_size=[1920, 1080],
                            team1_busy_template_path="team1-busy.png",
                            team3_busy_template_path="team3-busy.png",
                        )
                    ],
                )
            ],
        )

        self.assertTrue(engine._scenario_uses_level_ocr())
```

- [ ] **Step 5: Run the authority and unbounded-flow tests and confirm failures**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_rally_team_selection.py tests/test_matching_row_action.py::MatchingRowActionTests::test_unbounded_smart_row_snapshot_still_contains_level_crops tests/test_engine_performance.py::EnginePerformanceTests::test_smart_row_with_blank_ordinary_limits_still_uses_level_ocr -q
```

Expected: failures show the row maximum clamping selector values, hidden fallback limits, no OCR level for unbounded smart routing, and a level crop omitted from the atomic snapshot.

- [ ] **Step 6: Resolve only the sole visible selector**

Import the shared predicate in `macro_clicker/rally_matching.py`:

```python
from .models import Action, ImageCondition, has_smart_rally_team_prefilter
```

Replace `_resolve_rally_team_level_limits` with:

```python
    def _resolve_rally_team_level_limits(
        self, action: Action
    ) -> tuple[
        dict[int, int | None],
        str,
        dict[str, str | int],
    ]:
        selectors = []
        for step_index, step in enumerate(
            getattr(getattr(self, "scenario", None), "steps", [])
        ):
            for action_index, candidate in enumerate(step.actions):
                if candidate.type == "select_rally_team":
                    selectors.append(
                        (
                            candidate,
                            {
                                "step_name": step.name,
                                "step_index": step_index,
                                "action_index": action_index,
                            },
                        )
                    )
        if len(selectors) != 1:
            raise ValueError(
                "Smart rally-team availability requires exactly one "
                f"select_rally_team action; found {len(selectors)}."
            )
        selector, identity = selectors[0]
        return (
            {
                1: selector.team1_max_level,
                3: selector.team3_max_level,
            },
            "select_rally_team",
            identity,
        )
```

The `action` argument stays in the method signature for call-site compatibility but is no longer a limit source.

- [ ] **Step 7: Replace row clamping with smart override semantics**

Replace the opening of `_row_level_status` through its early no-filter return with:

```python
        if self._stop_requested():
            return _LEVEL_UNREADABLE, None
        smart_override = max_level_override is not _TEAM_LEVEL_CAP_UNSET
        if max_level_override is None:
            return _LEVEL_INELIGIBLE, None
        if max_level_override == _TEAM_LEVEL_CAP_UNBOUNDED:
            effective_max = None
        elif smart_override:
            effective_max = int(max_level_override)
        else:
            effective_max = action.max_level
        if (
            not smart_override
            and action.min_level is None
            and effective_max is None
        ):
            return _LEVEL_ELIGIBLE, None
```

Keep the existing OCR call, minimum comparison, maximum comparison, log messages, and return values after this block. This retains `min_level`, makes numeric smart limits replace the row maximum, makes `"unbounded"` bypass maximum filtering, and still forces exactly one `_read_level_for_row` call for final dispatch.

In `_available_rally_team_level_cap`, replace the idle-cap block with:

```python
        idle_teams = [team_number for team_number in (1, 3) if not busy[team_number]]
        if not idle_teams:
            level_cap: int | str | None = None
        elif any(level_limits[team_number] is None for team_number in idle_teams):
            level_cap = _TEAM_LEVEL_CAP_UNBOUNDED
        else:
            level_cap = max(
                int(level_limits[team_number])
                for team_number in idle_teams
                if level_limits[team_number] is not None
            )
```

Leave diagnostic keys `level_limits`, `level_limits_source`, `level_limits_selector`, and `level_cap` unchanged so existing evidence remains JSON-safe and attributable to the visible selector.

- [ ] **Step 8: Include smart rows in OCR warm-up and atomic snapshot sizing**

Replace `_scenario_uses_level_ocr` in `macro_clicker/rally_matching.py` with:

```python
    def _scenario_uses_level_ocr(self):
        for step in getattr(self.scenario, "steps", []):
            for action in getattr(step, "actions", []):
                if action.type != "click_matching_row":
                    continue
                if (
                    action.min_level is not None
                    or action.max_level is not None
                    or has_smart_rally_team_prefilter(action)
                ):
                    return True
        return False
```

Import the predicate in `macro_clicker/engine.py`:

```python
from .models import (
    Action,
    ImageCondition,
    Scenario,
    Step,
    has_smart_rally_team_prefilter,
    project_path,
    validate_scenario,
)
```

Replace the ordinary-limit portion of `_matching_row_snapshot_regions` with:

```python
            if (
                action.type != "click_matching_row"
                or (
                    action.min_level is None
                    and action.max_level is None
                    and not has_smart_rally_team_prefilter(action)
                )
                or action.match_condition_index is None
                or not 0 <= action.match_condition_index < len(regions)
            ):
                continue
```

- [ ] **Step 9: Run focused runtime tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_rally_team_selection.py tests/test_matching_row_action.py tests/test_engine_performance.py -q
```

Expected: all tests pass, including one-team maximum enforcement, selector values over 65, blank unlimited values, single cached availability capture, atomic level crops, OCR warm-up, level handoff, and Team 3 priority.

- [ ] **Step 10: Commit runtime authority and OCR handoff**

```powershell
git add -- macro_clicker/rally_matching.py macro_clicker/engine.py tests/test_rally_team_selection.py tests/test_matching_row_action.py tests/test_engine_performance.py
git commit -m "fix: use visible team limits for smart rally routing"
```

### Task 3: Canonicalize Persistence and Clarify the Interface

**Files:**
- Modify: `macro_clicker/models.py:389-409, 560-589`
- Modify: `macro_clicker/editors.py:20-45, 678-732, 1025-1073`
- Modify: `macro_clicker/ui_components.py:1-20, 555-598`
- Modify: `scenarios/Rally gold mob_ 2 team.json:523-552`
- Modify: `tests/test_models_validation.py`
- Modify: `tests/test_rally_team_selection.py:419-469`
- Modify: `tests/test_ui_components.py:4-14, 40-71`

**Interfaces:**
- Consumes: `has_smart_rally_team_prefilter(action)` from Task 1.
- Produces: `row_max_level_editor_state(action) -> tuple[str, str]`, returning the ttk entry state and label text.
- Produces: `row_advanced_options_configured(action) -> bool`, ensuring smart-row limit guidance cannot disappear inside a collapsed advanced section.
- Produces: smart-row `Action.to_dict()` emits `None` for `max_level`, `team1_max_level`, and `team3_max_level` while `Action.from_dict()` continues to parse old values.
- Preserves: selector maximum fields remain editable and blank values remain `None` through `_parse_optional_int`.

- [ ] **Step 1: Add persistence and UI regression tests**

Add to `ModelValidationTests`:

```python
    def test_smart_row_parses_old_limits_but_serializes_them_as_null(self):
        action = Action.from_dict(
            {
                "type": "click_matching_row",
                "max_level": 12,
                "team1_max_level": 11,
                "team3_max_level": 10,
                "team_status_region": [0, 0, 100, 100],
                "team_status_reference_size": [1920, 1080],
                "team1_busy_template_path": "templates/Team1Busy.png",
                "team3_busy_template_path": "templates/Team3Busy.png",
            }
        )

        self.assertEqual(action.max_level, 12)
        self.assertEqual(action.team1_max_level, 11)
        self.assertEqual(action.team3_max_level, 10)
        serialized = action.to_dict()
        self.assertIsNone(serialized["max_level"])
        self.assertIsNone(serialized["team1_max_level"])
        self.assertIsNone(serialized["team3_max_level"])

    def test_blank_team_limits_have_clear_action_summary(self):
        summary = Action(type="select_rally_team").summary()

        self.assertIn("Team 3 (unlimited)", summary)
        self.assertIn("Team 1 (unlimited)", summary)
        self.assertNotIn("None", summary)
```

Import `row_advanced_options_configured` and `row_max_level_editor_state` in `tests/test_ui_components.py` and add:

```python
    def test_smart_row_maximum_control_is_disabled(self):
        smart = Action(
            type="click_matching_row",
            team_status_region=[0, 0, 100, 100],
            team_status_reference_size=[1920, 1080],
            team1_busy_template_path="team1-busy.png",
            team3_busy_template_path="team3-busy.png",
        )
        ordinary = Action(type="click_matching_row", max_level=65)

        self.assertEqual(
            row_max_level_editor_state(smart),
            ("disabled", "Controlled by Team 1 / Team 3"),
        )
        self.assertEqual(
            row_max_level_editor_state(ordinary),
            ("normal", "Max level"),
        )
        self.assertTrue(row_advanced_options_configured(smart))
        self.assertFalse(
            row_advanced_options_configured(Action(type="click_matching_row"))
        )

    def test_blank_team_limits_render_as_unlimited(self):
        action = Action(
            type="select_rally_team",
            on_condition_index=1,
            team1_max_level=None,
            team3_max_level=None,
        )

        summary = action_display_summary(action, self.conditions)

        self.assertIn("Team 3 (unlimited)", summary)
        self.assertIn("Team 1 (unlimited)", summary)
        self.assertNotIn("None", summary)
```

In `test_two_team_scenario_has_the_expected_gate_ranges_and_priority`, replace the obsolete row-limit assertions with:

```python
        self.assertIsNone(row_action.max_level)
        self.assertIsNone(row_action.team1_max_level)
        self.assertIsNone(row_action.team3_max_level)
        self.assertEqual(team_action.team3_max_level, 50)
        self.assertEqual(team_action.team1_max_level, 65)
```

- [ ] **Step 2: Run the persistence and UI tests and confirm failures**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_models_validation.py tests/test_ui_components.py tests/test_rally_team_selection.py::RallyTeamSelectionTests::test_two_team_scenario_has_the_expected_gate_ranges_and_priority -q
```

Expected: failures show serialized stale row values, `None` in summaries, the ordinary maximum control state missing, and non-null values in the supplied two-team scenario.

- [ ] **Step 3: Canonicalize smart-row serialization and fallback summaries**

In `Action.to_dict`, preserve all existing portable path conversion and add the smart-row normalization before `return data`:

```python
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
```

This is a save-boundary migration: old values remain parseable in memory, runtime ignores them, and the next clone/save writes canonical nulls.

Replace the `select_rally_team` branch of `Action.summary` with:

```python
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
            return (
                f"Select idle Team 3 ({team3_limit}), then "
                f"Team 1 ({team1_limit})"
            )
```

- [ ] **Step 4: Add pure UI labels and use them in summaries**

Import the predicate near the top of `macro_clicker/ui_components.py`:

```python
from .models import has_smart_rally_team_prefilter
```

Add these helpers immediately before `action_display_summary`:

```python
def row_max_level_editor_state(action):
    if has_smart_rally_team_prefilter(action):
        return "disabled", "Controlled by Team 1 / Team 3"
    return "normal", "Max level"


def row_advanced_options_configured(action):
    return any(
        (
            has_smart_rally_team_prefilter(action),
            action.row_tolerance != 60,
            action.offset_x != 0,
            action.offset_y != 0,
            action.min_level is not None,
            action.max_level is not None,
            action.level_roi is not None,
            action.no_match_condition_index is not None,
            bool(action.no_match_disable_steps),
            action.pre_click_delay > 0.0,
        )
    )


def _team_limit_summary(max_level):
    return "unlimited" if max_level is None else f"max level {max_level}"
```

Replace the `select_rally_team` display branch with:

```python
    if action.type == "select_rally_team":
        anchor = condition_name(
            conditions,
            action.on_condition_index,
            "Unselected anchor",
        )
        return (
            f"Select idle Team 3 ({_team_limit_summary(action.team3_max_level)}), "
            f"then Team 1 ({_team_limit_summary(action.team1_max_level)}), "
            f"anchored to {anchor}"
        )
```

- [ ] **Step 5: Disable and clear the redundant row maximum in the editor**

Import `has_smart_rally_team_prefilter` from `models` and both `row_advanced_options_configured` and `row_max_level_editor_state` from `ui_components` in `macro_clicker/editors.py`.

Where the row variables are initialized, derive the editor mode:

```python
    smart_row_limits = has_smart_rally_team_prefilter(a)
    max_level_state, max_level_label = row_max_level_editor_state(a)
    max_level_var = tk.StringVar(
        value=(
            ""
            if smart_row_limits or a.max_level is None
            else str(a.max_level)
        )
    )
```

Replace the row maximum label and entry with:

```python
    ttk.Label(
        row_click_frame,
        text=max_level_label,
        style="Surface.TLabel",
    ).grid(row=7, column=2, sticky="w")
    ttk.Entry(
        row_click_frame,
        textvariable=max_level_var,
        width=7,
        state=max_level_state,
    ).grid(row=7, column=3, sticky="w")
```

Replace the inline `advanced_configured` calculation with:

```python
    advanced_configured = row_advanced_options_configured(a)
```

Because the helper includes `has_smart_rally_team_prefilter(action)`, a smart row automatically opens the advanced section even when its ordinary min, max, ROI, delay, offsets, and fallback settings are all blank/default. The disabled `Controlled by Team 1 / Team 3` field is therefore always presented when that row is edited.

In the `click_matching_row` save branch, replace the maximum assignment with:

```python
                new_action.max_level = (
                    None
                    if smart_row_limits
                    else _parse_optional_int(max_level_var.get(), "Max level")
                )
```

Preserve the existing team status region, reference size, busy templates, and confidence, then explicitly clear the hidden per-team values:

```python
                new_action.team_status_region = copy.deepcopy(a.team_status_region)
                new_action.team_status_reference_size = copy.deepcopy(
                    a.team_status_reference_size
                )
                new_action.team1_busy_template_path = a.team1_busy_template_path
                new_action.team3_busy_template_path = a.team3_busy_template_path
                new_action.team_busy_confidence = a.team_busy_confidence
                new_action.team1_max_level = None
                new_action.team3_max_level = None
```

Do not disable or clear `min_level`; this feature removes only redundant maximum controls.

- [ ] **Step 6: Canonicalize the supplied two-team scenario**

In the `Joining` step's `click_matching_row` action in `scenarios/Rally gold mob_ 2 team.json`, set exactly these fields:

```json
        "min_level": null,
        "max_level": null,
        "team1_max_level": null,
        "team3_max_level": null
```

Leave the `Attack Confirm` step's `select_rally_team` values unchanged:

```json
        "team1_max_level": 65,
        "team3_max_level": 50
```

Do not edit `scenarios/Rally Gold Mob.json`.

- [ ] **Step 7: Run focused persistence and interface tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_models_validation.py tests/test_ui_components.py tests/test_rally_team_selection.py -q
```

Expected: all tests pass; old smart-row values parse but serialize as null, the shipped two-team row loads with null obsolete limits, selector 65/50 remains authoritative, and blank selector fields display as unlimited.

- [ ] **Step 8: Perform a short manual editor acceptance check**

Run:

```powershell
.\.venv\Scripts\python.exe -m macro_clicker
```

Verify in the two-team scenario:

- Editing `Joining` shows `Controlled by Team 1 / Team 3` and the field is disabled.
- Editing `Attack Confirm` keeps Team 1 and Team 3 maximum fields editable; clearing either field is allowed and reads as unlimited in the action list after Save.
- Editing the one-team `Rally Gold Mob` Joining action still shows an editable `Max level` field.
- Close the application without altering the user's existing one-team tuning or alert volume.

- [ ] **Step 9: Commit persistence, scenario, and interface changes**

```powershell
git add -- macro_clicker/models.py macro_clicker/editors.py macro_clicker/ui_components.py "scenarios/Rally gold mob_ 2 team.json" tests/test_models_validation.py tests/test_rally_team_selection.py tests/test_ui_components.py
git commit -m "feat: expose only visible rally team limits"
```

### Task 4: Full Verification and Safety Audit

**Files:**
- Verify only; modify no files unless a verification command exposes a defect in the planned implementation.

**Interfaces:**
- Consumes: all behavior and tests from Tasks 1-3.
- Produces: a verified branch whose only uncommitted files are the two protected user preferences that existed before implementation.

- [ ] **Step 1: Verify the two protected local changes remain unstaged and intact**

Run:

```powershell
git status --short
git diff -- alerts/settings.json "scenarios/Rally Gold Mob.json"
```

Expected: both files remain modified and unstaged; their diff still contains only the user's alert-volume and one-team level/wait tuning. No implementation commit contains either path.

- [ ] **Step 2: Run the complete test suite**

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Expected: all tests pass with no unexpected skips or failures.

- [ ] **Step 3: Run Ruff**

```powershell
.\.venv\Scripts\python.exe -m ruff check .
```

Expected: `All checks passed!`

- [ ] **Step 4: Run mypy**

```powershell
.\.venv\Scripts\python.exe -m mypy macro_clicker tools
```

Expected: `Success: no issues found`.

- [ ] **Step 5: Validate both shipped rally scenarios with file checks**

Run:

```powershell
@'
from macro_clicker.models import load_scenario, validate_scenario

for name in ("Rally Gold Mob", "Rally gold mob_ 2 team"):
    scenario = load_scenario(name)
    validate_scenario(scenario, require_files=True)
    print(f"validated: {name}")
'@ | .\.venv\Scripts\python.exe -
```

Expected:

```text
validated: Rally Gold Mob
validated: Rally gold mob_ 2 team
```

- [ ] **Step 6: Check whitespace, commit scope, and final repository state**

Run:

```powershell
git diff --check
git log -3 --oneline
git status --short --branch
```

Expected: no whitespace errors; the three implementation commits are present; the branch contains no uncommitted implementation files; only `alerts/settings.json` and `scenarios/Rally Gold Mob.json` remain as the user's pre-existing local changes. Do not create an empty verification commit.

## Live-Game Acceptance After Automated Verification

Use the two-team scenario on the 1920x1080 game window and confirm these cases from the runtime log:

- Both idle, level 45: Team 3 (Stetmann) is selected.
- Both idle, level 55 with Team 1 = 65 and Team 3 = 50: Team 1 (Murphy) is selected.
- Team 1 busy, Team 3 idle, level 50: Team 3 is selected.
- Team 1 busy, Team 3 idle, level 51: the rally row is rejected.
- A blank Team 3 maximum permits Team 3 to take a detected level above 50.
- A Team 3 maximum above 65 works without editing any other maximum.
- Carlie remains ignored because no Team 2 candidate is created.
- The log records selector-derived `level_limits`, selector identity, and `level cap unbounded` when a blank idle-team maximum is active.
- The row-to-team flow remains: rally page -> matching Join row -> Team 3/Team 1 portrait -> Attack button.
