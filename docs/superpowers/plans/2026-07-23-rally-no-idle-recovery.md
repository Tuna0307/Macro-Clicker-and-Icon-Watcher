# Rally No-Idle Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reliably recognize idle Team 3 at the observed score range and recover the two-team rally state machine when no eligible team is idle, without ever clicking Attack.

**Architecture:** Keep the selector's existing single atomic capture and priority order. Calibrate the scenario's shared idle threshold to `0.80`; on a no-idle result, dismiss the selector with an anchor-relative click and mark the abort as cleanup-capable. The cycle runner will then skip unsafe actions while executing only the remaining `set_step` cleanup actions.

**Tech Stack:** Python 3, OpenCV, unittest/pytest, JSON scenario configuration, PyAutoGUI click abstraction.

## Global Constraints

- The one-second post-Join wait remains unchanged.
- Team 3 remains preferred when its configured level limit accepts the mob.
- Team 1 remains the immediate fallback when eligible and idle.
- Carlie/Team 2 remains excluded.
- No-idle recovery must never click a team or `Attack.png`.
- Exact-frame score diagnostics and existing score logging remain enabled.
- User-owned changes in `alerts/settings.json` and `scenarios/Rally Gold Mob.json` must not be modified or staged.

---

### Task 1: Calibrate the selector threshold from real idle and busy frames

**Files:**
- Create: `tests/fixtures/rally_team_selection/team3_idle_0818.png`
- Create: `tests/fixtures/rally_team_selection/team3_busy_0736.png`
- Modify: `tests/test_rally_team_selection.py`
- Modify: `scenarios/Rally gold mob_ 2 team.json:669`

**Interfaces:**
- Consumes: `MacroEngine._best_scaled_template_match(frame, template) -> tuple[float, tuple[int, int]]` and `templates/Team3Idle.png`.
- Produces: portable regression crops and `select_rally_team.team_idle_confidence == 0.80` for the two-team scenario.

- [ ] **Step 1: Create the two portable 40x36 regression crops from the supplied screenshots**

Run:

```powershell
@'
from pathlib import Path
from PIL import Image

samples = (
    (
        Path(r"C:\Users\chimw\AppData\Local\Temp\codex-clipboard-d413a882-68f8-43b3-b3c8-fa064a20ba01.png"),
        (965, 938, 1005, 974),
        Path("tests/fixtures/rally_team_selection/team3_idle_0818.png"),
    ),
    (
        Path(r"C:\Users\chimw\OneDrive\Pictures\Screenshots 1\Screenshot 2026-07-22 173811.png"),
        (280, 72, 320, 108),
        Path("tests/fixtures/rally_team_selection/team3_busy_0736.png"),
    ),
)

for source, box, destination in samples:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as image:
        crop = image.crop(box)
        assert crop.size == (40, 36)
        crop.save(destination)
'@ | .\.venv\Scripts\python.exe -
```

Expected: both PNG files exist and are exactly `40x36` pixels.

- [ ] **Step 2: Write a failing real-image threshold regression test**

Add to `RallyTeamSelectionTests` in `tests/test_rally_team_selection.py`:

```python
def test_two_team_idle_threshold_accepts_supplied_idle_and_rejects_busy(self):
    scenario = load_scenario("Rally gold mob_ 2 team")
    action = next(
        action
        for step in scenario.steps
        if step.name == "Attack Confirm"
        for action in step.actions
        if action.type == "select_rally_team"
    )
    idle_crop = cv2.imread(
        project_path(
            "tests/fixtures/rally_team_selection/team3_idle_0818.png"
        )
    )
    busy_crop = cv2.imread(
        project_path(
            "tests/fixtures/rally_team_selection/team3_busy_0736.png"
        )
    )
    template = cv2.imread(project_path("templates/Team3Idle.png"))
    self.assertIsNotNone(idle_crop)
    self.assertIsNotNone(busy_crop)
    self.assertIsNotNone(template)

    engine = object.__new__(MacroEngine)
    engine.low_variance_threshold = 1.0
    idle_score, _ = engine._best_scaled_template_match(idle_crop, template)
    busy_score, _ = engine._best_scaled_template_match(busy_crop, template)

    self.assertAlmostEqual(action.team_idle_confidence, 0.80)
    self.assertGreaterEqual(idle_score, action.team_idle_confidence)
    self.assertLess(busy_score, action.team_idle_confidence)
    self.assertAlmostEqual(idle_score, 0.8177595, places=5)
    self.assertAlmostEqual(busy_score, 0.7355111, places=5)
```

- [ ] **Step 3: Run the calibration test and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_rally_team_selection.py::RallyTeamSelectionTests::test_two_team_idle_threshold_accepts_supplied_idle_and_rejects_busy -q
```

Expected: FAIL because the scenario still reports `0.85` and rejects the `0.8177595` idle crop.

- [ ] **Step 4: Make the minimum scenario change**

In `scenarios/Rally gold mob_ 2 team.json`, change only the selector threshold:

```json
"team_idle_confidence": 0.8,
```

- [ ] **Step 5: Run focused calibration and scenario-validation tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_rally_team_selection.py::RallyTeamSelectionTests::test_two_team_idle_threshold_accepts_supplied_idle_and_rejects_busy tests/test_rally_team_selection.py::RallyTeamSelectionTests::test_two_team_scenario_has_the_expected_gate_ranges_and_priority -q
```

Expected: PASS.

- [ ] **Step 6: Commit only the calibration files**

```powershell
git add -- "tests/fixtures/rally_team_selection/team3_idle_0818.png" "tests/fixtures/rally_team_selection/team3_busy_0736.png" "tests/test_rally_team_selection.py" "scenarios/Rally gold mob_ 2 team.json"
git commit -m "fix: calibrate rally team idle detection"
```

---

### Task 2: Dismiss the selector and request safe cleanup on no-idle results

**Files:**
- Modify: `tests/test_rally_team_selection.py:228-256`
- Modify: `macro_clicker/engine.py:81-88,142-147,1531-1553`

**Interfaces:**
- Consumes: the detected Attack anchor `(x, y)`, its `scale_y`, `_click_point(x, y, button) -> bool`, and the existing exact-frame diagnostic snapshot.
- Produces: `_cleanup_after_abort: bool`; a dismissal click at `(anchor_x, round(anchor_y - 400 * scale_y))`; cleared `_pending_rally_level` and `_pending_rally_team_availability`.

- [ ] **Step 1: Replace the no-idle unit assertion with the desired recovery contract**

Replace `test_no_eligible_idle_team_aborts_before_attack_action` with:

```python
def test_no_eligible_idle_team_dismisses_selector_and_requests_cleanup(self):
    engine, clicked = self._engine(45, team1_idle=False, team3_idle=False)
    engine._abort_current_step = False
    engine._cleanup_after_abort = False
    engine._pending_rally_team_availability = {"level_cap": 65}
    points = {0: (962, 808)}
    matches = {
        0: [{"center": (962, 808), "scale_x": 1.0, "scale_y": 1.0}]
    }

    result = engine._run_select_rally_team_action(
        self._action(), points, matches
    )

    self.assertTrue(result)
    self.assertEqual(clicked, [(962, 408, "left")])
    self.assertFalse(engine._retry_current_step)
    self.assertTrue(engine._abort_current_step)
    self.assertTrue(engine._cleanup_after_abort)
    self.assertIsNone(engine._pending_rally_level)
    self.assertIsNone(engine._pending_rally_team_availability)
```

Update `test_high_level_with_busy_team1_aborts_for_back_recovery` to expect the recovery click derived from its existing `(500, 300)` anchor:

```python
self.assertTrue(result)
self.assertEqual(clicked, [(500, -100, "left")])
self.assertTrue(engine._cleanup_after_abort)
```

Add the dismissal-failure safety regression:

```python
def test_no_idle_still_requests_cleanup_when_dismiss_click_fails(self):
    engine, _clicked = self._engine(
        45, team1_idle=False, team3_idle=False
    )
    engine._abort_current_step = False
    engine._cleanup_after_abort = False
    engine._pending_rally_team_availability = {"level_cap": 65}
    engine._click_point = lambda _x, _y, _button: False
    points, matches = self._context()

    result = engine._run_select_rally_team_action(
        self._action(), points, matches
    )

    self.assertFalse(result)
    self.assertTrue(engine._abort_current_step)
    self.assertTrue(engine._cleanup_after_abort)
    self.assertIsNone(engine._pending_rally_level)
    self.assertIsNone(engine._pending_rally_team_availability)
```

- [ ] **Step 2: Run the selector recovery tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_rally_team_selection.py::RallyTeamSelectionTests::test_no_eligible_idle_team_dismisses_selector_and_requests_cleanup tests/test_rally_team_selection.py::RallyTeamSelectionTests::test_high_level_with_busy_team1_aborts_for_back_recovery tests/test_rally_team_selection.py::RallyTeamSelectionTests::test_no_idle_still_requests_cleanup_when_dismiss_click_fails -q
```

Expected: FAIL because the current selector makes no dismissal click, does not clear the availability snapshot, and has no cleanup-capable abort flag.

- [ ] **Step 3: Initialize and reset the cleanup-capable abort flag**

In `MacroEngine.__init__` and `MacroEngine.start`, next to `_abort_current_step`, add/reset:

```python
self._cleanup_after_abort = False
```

- [ ] **Step 4: Implement minimum no-idle dismissal and state reset**

Replace the state-reset tail of the `selected is None` branch in `_run_select_rally_team_action` with:

```python
recovery_x = anchor[0]
recovery_y = round(anchor[1] - 400 * scale_y)
recovery_clicked = bool(
    self._click_point(recovery_x, recovery_y, action.button)
)
if recovery_clicked:
    self.log(
        "  [recovery] dismissed rally team selector at "
        f"({recovery_x}, {recovery_y})"
    )
else:
    self.log(
        "  [recovery] could not dismiss rally team selector; "
        "continuing state cleanup"
    )
self._pending_rally_level = None
self._pending_rally_team_availability = None
self._retry_current_step = False
self._cleanup_after_abort = True
self._abort_current_step = True
return recovery_clicked
```

Keep the diagnostic submission before this block so it continues to receive the exact decision frame.

- [ ] **Step 5: Run the focused selector tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_rally_team_selection.py -q
```

Expected: PASS. The cleanup-only cycle regression is added separately in Task 3.

- [ ] **Step 6: Commit the selector recovery state change**

```powershell
git add -- "tests/test_rally_team_selection.py" "macro_clicker/engine.py"
git commit -m "fix: dismiss rally selector when no team is idle"
```

---

### Task 3: Execute only trailing state cleanup after a selector abort

**Files:**
- Modify: `tests/test_rally_team_selection.py:258-300`
- Modify: `macro_clicker/engine.py:1711-1748`

**Interfaces:**
- Consumes: `_cleanup_after_abort == True` set by Task 2 and trailing `Action(type="set_step")` objects in the fired step.
- Produces: cycle behavior that skips waits/clicks after abort, executes only later `set_step` actions, and clears `_cleanup_after_abort` before continuing.

- [ ] **Step 1: Write the failing cleanup-only cycle regression test**

Add beside the existing abort-cycle test:

```python
def test_cleanup_abort_skips_attack_and_runs_only_trailing_set_steps(self):
    joining_step = Step(name="Joining", enabled=True)
    attack_step = Step(
        name="Attack Confirm",
        enabled=True,
        actions=[
            Action(type="select_rally_team"),
            Action(type="wait", seconds=0.2),
            Action(type="click", x=10, y=20),
            Action(type="set_step", step_name="Joining", set_enabled=False),
            Action(
                type="set_step",
                step_name="Attack Confirm",
                set_enabled=False,
            ),
            Action(
                type="set_step",
                step_name="Back if wrong mob",
                set_enabled=False,
            ),
        ],
    )
    back_step = Step(name="Back if wrong mob", enabled=True)
    engine = object.__new__(MacroEngine)
    engine.scenario = Scenario(
        name="Cleanup abort",
        steps=[joining_step, attack_step, back_step],
    )
    engine._stop_event = type("Stop", (), {"is_set": lambda self: False})()
    engine._last_fired = {attack_step.name: 0.0}
    engine._evaluate_uses_frame_cache = False
    engine._evaluate_step = lambda _step: (True, {}, {})
    engine._refresh_step_caches = lambda: [attack_step]
    engine._prepare_rally_team_availability_for_entry = lambda _step: True
    engine._should_log_perf = lambda *_args, **_kwargs: False
    engine.log = lambda _message: None
    engine._step_lookup = {
        step.name: step for step in engine.scenario.steps
    }
    executed = []

    def run_action(_step, action, _points, _matches):
        executed.append(action.type)
        if action.type == "select_rally_team":
            engine._abort_current_step = True
            engine._cleanup_after_abort = True
            return True
        if action.type == "set_step":
            engine._step_lookup[action.step_name].enabled = action.set_enabled
        return False

    engine._run_action = run_action

    self.assertTrue(engine._cycle())
    self.assertEqual(
        executed,
        ["select_rally_team", "set_step", "set_step", "set_step"],
    )
    self.assertFalse(joining_step.enabled)
    self.assertFalse(attack_step.enabled)
    self.assertFalse(back_step.enabled)
    self.assertFalse(engine._cleanup_after_abort)
```

- [ ] **Step 2: Run the cleanup-only cycle test and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_rally_team_selection.py::RallyTeamSelectionTests::test_cleanup_abort_skips_attack_and_runs_only_trailing_set_steps -q
```

Expected: FAIL because the current cycle breaks immediately after the selector and never runs the three `set_step` actions.

- [ ] **Step 3: Implement cleanup-only traversal in the action loop**

Change the action loop to retain its index and reset both abort flags at the start of a fired step:

```python
self._abort_current_step = False
self._cleanup_after_abort = False
for action_index, action in enumerate(step.actions):
```

Replace the current abort check with:

```python
if getattr(self, "_abort_current_step", False):
    if getattr(self, "_cleanup_after_abort", False):
        for cleanup_action in step.actions[action_index + 1 :]:
            if cleanup_action.type != "set_step":
                continue
            if self._stop_requested():
                return fired_any
            self._retry_current_step = False
            self._run_action(step, cleanup_action, points, matches)
        self._cleanup_after_abort = False
    break
```

This deliberately does not execute waits, ordinary clicks, keys, row clicks, or another team selector after an abort.

- [ ] **Step 4: Run the cleanup regression and all rally-team tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_rally_team_selection.py -q
```

Expected: PASS, including the existing successful Team 1/Team 3 selection and exact-frame diagnostic tests.

- [ ] **Step 5: Commit the cleanup control flow**

```powershell
git add -- "tests/test_rally_team_selection.py" "macro_clicker/engine.py"
git commit -m "fix: complete rally cleanup after selector abort"
```

---

### Task 4: Verify the complete application without touching user-owned files

**Files:**
- Verify: `macro_clicker/engine.py`
- Verify: `scenarios/Rally gold mob_ 2 team.json`
- Verify: `tests/test_rally_team_selection.py`
- Preserve: `alerts/settings.json`
- Preserve: `scenarios/Rally Gold Mob.json`

**Interfaces:**
- Consumes: all deliverables from Tasks 1-3.
- Produces: test, lint, type-check, scenario-validation, and clean-diff evidence suitable for actual game retesting.

- [ ] **Step 1: Run the focused rally and state-machine suites**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_rally_team_selection.py tests/test_matching_row_action.py -q
```

Expected: PASS.

- [ ] **Step 2: Run the complete test suite**

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Expected: PASS; any explicitly marked fixture-dependent skip remains reported as a skip rather than a failure.

- [ ] **Step 3: Run static checks**

```powershell
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m mypy macro_clicker tools
```

Expected: both commands exit successfully with no new errors.

- [ ] **Step 4: Check patch integrity and working-tree scope**

```powershell
git diff --check
git status --short
git log -5 --oneline
```

Expected: no whitespace errors; only the pre-existing user modifications to `alerts/settings.json` and `scenarios/Rally Gold Mob.json` remain uncommitted; the three implementation commits are present.

- [ ] **Step 5: Review the final diff against the approved design**

```powershell
git show --stat --oneline HEAD~2..HEAD
git diff HEAD~3..HEAD -- "macro_clicker/engine.py" "scenarios/Rally gold mob_ 2 team.json" "tests/test_rally_team_selection.py" "tests/fixtures/rally_team_selection"
```

Expected: threshold calibration, no-idle dismissal/reset, cleanup-only traversal, regression fixtures/tests, and no post-Join wait change.
