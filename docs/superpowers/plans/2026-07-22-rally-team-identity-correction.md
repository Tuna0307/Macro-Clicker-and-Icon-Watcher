# Rally Team Identity Correction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Correct the two-team rally scenario so Murphy is Team 1, Carlie is ignored Team 2, and Stetmann is Team 3.

**Architecture:** Keep the existing two-stage safety design. Before entering the rally page, search the entire compressed deployment queue for Murphy and Stetmann portraits; on the dispatch panel, verify and click the fixed first or third card. Correct only the misidentified Team 3 template and second-card coordinates.

**Tech Stack:** Python 3.12, OpenCV template matching, `unittest`/pytest, JSON scenario configuration, Tkinter editor defaults.

## Global Constraints

- Team 1 is Murphy and accepts levels 1 through 65.
- Team 2 is Carlie and must never be selected, even when idle.
- Team 3 is Stetmann and accepts levels 1 through 45.
- Portrait identity is detected anywhere in the full queue region; row position is not identity.
- The blue `Z` icon remains the final idle-state confirmation before a dispatch-card click.
- Preserve unrelated working-tree changes.

---

### Task 1: Lock Team Identity to the Supplied Queue Screenshots

**Files:**
- Create: `tests/fixtures/rally_team_status/carlie_only.png`
- Create: `tests/fixtures/rally_team_status/murphy_carlie.png`
- Create: `tests/fixtures/rally_team_status/all_three.png`
- Create: `tests/fixtures/rally_team_status/carlie_stetmann.png`
- Modify: `tests/test_rally_team_selection.py`
- Modify: `templates/Team3Busy.png`

**Interfaces:**
- Consumes: `MacroEngine._available_rally_team_level_cap(action) -> int | None | object` and the `team_status_region` dimensions from the two-team scenario.
- Produces: `templates/Team3Busy.png` containing Stetmann's 50x48 portrait and a regression test covering compressed queue positions.

- [ ] **Step 1: Crop durable queue-region fixtures from the four supplied screenshots**

Use OpenCV to crop `[x=0, y=265, width=220, height=215]` from screenshots `191716`, `191724`, `191735`, and `191742` into the four fixture names above. These are test inputs only and contain the complete region searched by the runtime.

- [ ] **Step 2: Write the failing identity test**

Add this behavior to `RallyTeamSelectionTests`:

```python
def test_supplied_queue_frames_identify_murphy_and_stetmann_by_portrait(self):
    cases = {
        "carlie_only.png": (65, {1: False, 3: False}),
        "murphy_carlie.png": (45, {1: True, 3: False}),
        "all_three.png": (None, {1: True, 3: True}),
        "carlie_stetmann.png": (65, {1: False, 3: True}),
    }
    for fixture_name, (expected_cap, expected_busy) in cases.items():
        with self.subTest(fixture_name=fixture_name):
            cap, busy = self._availability_from_queue_fixture(fixture_name)
            self.assertEqual(cap, expected_cap)
            self.assertEqual(busy, expected_busy)
```

The helper must load the scenario's `click_matching_row` action, return the fixture from `_grab`, run `_available_rally_team_level_cap`, and return both its cap and `_last_rally_team_availability["busy"]`.

- [ ] **Step 3: Run the identity test and verify the current Carlie template fails**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_rally_team_selection.py::RallyTeamSelectionTests::test_supplied_queue_frames_identify_murphy_and_stetmann_by_portrait -q
```

Expected: FAIL for `murphy_carlie.png`, because the current `Team3Busy.png` is Carlie and incorrectly reports Team 3 busy.

- [ ] **Step 4: Replace only the Team 3 portrait template**

Crop `Screenshot 2026-07-22 191735.png` at `[x=12, y=412, width=50, height=48]` and overwrite `templates/Team3Busy.png`. Do not add Carlie as a runtime template; Team 2 is intentionally ignored.

- [ ] **Step 5: Run the identity test and focused team tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_rally_team_selection.py -q
```

Expected: all tests in the file pass.

- [ ] **Step 6: Commit the portrait-identity regression**

```powershell
git add -- tests/fixtures/rally_team_status tests/test_rally_team_selection.py templates/Team3Busy.png
git commit -m "fix: identify Stetmann as rally Team 3"
```

### Task 2: Move Team 3 Selection from Carlie's Card to Stetmann's Card

**Files:**
- Modify: `tests/test_rally_team_selection.py`
- Modify: `scenarios/Rally gold mob_ 2 team.json`
- Modify: `macro_clicker/editors.py`

**Interfaces:**
- Consumes: `Action.team3_idle_region` and `Action.team3_click_offset`, relative to the blue dispatch-button anchor.
- Produces: Team 3 detection region `[3, 130, 40, 36]` and click offset `[63, 168]`, both targeting the fixed third card.

- [ ] **Step 1: Write the failing scenario-coordinate assertions**

Extend `test_two_team_scenario_has_the_expected_gate_ranges_and_priority`:

```python
self.assertEqual(team_action.team1_idle_region, [-249, 130, 40, 36])
self.assertEqual(team_action.team1_click_offset, [-189, 168])
self.assertEqual(team_action.team3_idle_region, [3, 130, 40, 36])
self.assertEqual(team_action.team3_click_offset, [63, 168])
```

- [ ] **Step 2: Run the coordinate test and verify it fails on the second-card values**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_rally_team_selection.py::RallyTeamSelectionTests::test_two_team_scenario_has_the_expected_gate_ranges_and_priority -q
```

Expected: FAIL because Team 3 currently uses `[-123, 130, 40, 36]` and `[-63, 168]`, which point to Carlie's second card.

- [ ] **Step 3: Correct the scenario and editor defaults**

In the two-team scenario, set:

```json
"team3_idle_region": [3, 130, 40, 36],
"team3_click_offset": [63, 168]
```

In `macro_clicker/editors.py`, use the same values as the defaults for newly configured `select_rally_team` actions. Preserve the loaded values when editing an existing action.

- [ ] **Step 4: Run the focused test file**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_rally_team_selection.py -q
```

Expected: all focused tests pass, including low-level Team 3 priority and high-level Team 1 restriction.

- [ ] **Step 5: Commit the third-card selection correction**

```powershell
git add -- "scenarios/Rally gold mob_ 2 team.json" macro_clicker/editors.py tests/test_rally_team_selection.py
git commit -m "fix: target Stetmann's dispatch card"
```

### Task 3: Verify the Corrected Scenario End to End

**Files:**
- Verify: `templates/Team3Busy.png`
- Verify: `scenarios/Rally gold mob_ 2 team.json`
- Verify: `macro_clicker/editors.py`
- Verify: `tests/test_rally_team_selection.py`

**Interfaces:**
- Consumes: all outputs from Tasks 1 and 2.
- Produces: a verified scenario ready for the user's live-game retest.

- [ ] **Step 1: Benchmark the four real queue fixtures**

Run the fixture regression with `-vv` and confirm Stetmann is found whether it appears in row two or row three, while Carlie alone never marks Team 3 busy.

- [ ] **Step 2: Validate scenario paths and schema**

Run the existing scenario validation test with `require_files=True` through the focused test file. Expected: no missing template paths and no validation errors.

- [ ] **Step 3: Run all automated checks**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m mypy macro_clicker tools
```

Expected: zero test failures, Ruff errors, or mypy errors.

- [ ] **Step 4: Review the scoped diff**

Confirm the correction changes only the Team 3 portrait identity, third-card coordinates/defaults, regression fixtures/tests, and documentation. Preserve `scenarios/Rally Gold Mob.json` and all unrelated user files unchanged.

- [ ] **Step 5: Hand off live retest instructions**

Tell the user to stop the currently running macro with F12, close and relaunch the application so Python and scenario changes reload, then test `Rally gold mob_ 2 team` again. State that Carlie will be ignored even if idle.
