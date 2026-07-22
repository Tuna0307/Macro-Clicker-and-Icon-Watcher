# Single-Source Rally Team Limits Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the visible rally-team maximum levels control both Joining-stage OCR eligibility and final team dispatch.

**Architecture:** Resolve per-team limits from the scenario's `select_rally_team` action whenever it exists, while retaining the row action values as legacy fallback. Keep the Joining action's global `max_level` as an independent safety ceiling and record the resolved source in diagnostics.

**Tech Stack:** Python 3.12, OpenCV, dataclasses/JSON, Tkinter/ttk, pytest/unittest.

## Global Constraints

- The visible `select_rally_team` Team 1 and Team 3 maxima are authoritative.
- The Joining global maximum remains an additional upper bound.
- Older scenarios without `select_rally_team` continue using row-action limits.
- No additional OCR pass, capture, or delay is allowed.
- Preserve unrelated working-tree changes.

---

### Task 1: Resolve Team Limits from One Authority

**Files:**
- Modify: `macro_clicker/rally_matching.py:305-400`
- Test: `tests/test_rally_team_selection.py`

**Interfaces:**
- Produces: `_rally_team_level_limits(action: Action) -> tuple[dict[int, int | None], str]`.
- Consumes: the current scenario's first `select_rally_team` action, or the supplied row action as fallback.

- [ ] **Step 1: Write the exact failing regression**

Load the two-team scenario, set only the visible selector's Team 3 maximum to
50, deliberately leave the Joining action's hidden value at 45, and simulate
Team 1 busy plus Team 3 idle. Assert that
`_available_rally_team_level_cap(joining_action)` returns 50.

- [ ] **Step 2: Verify the regression fails**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_rally_team_selection.py::RallyTeamSelectionTests::test_visible_team3_limit_controls_joining_prefilter -q
```

Expected: `45 != 50`.

- [ ] **Step 3: Implement the resolver**

Add:

```python
def _rally_team_level_limits(self, action: Action):
    for step in getattr(getattr(self, "scenario", None), "steps", []):
        for candidate in step.actions:
            if candidate.type == "select_rally_team":
                return (
                    {1: candidate.team1_max_level, 3: candidate.team3_max_level},
                    "select_rally_team",
                )
    return (
        {1: action.team1_max_level, 3: action.team3_max_level},
        "legacy_row_action",
    )
```

Use the resolved mapping when calculating idle-team caps and include both the
mapping and source in `_last_rally_team_availability`.

- [ ] **Step 4: Verify focused behavior**

Run the failing regression and the complete rally-team test file. Expected:
all focused tests pass.

### Task 2: Verify Compatibility and Explain the UI

**Files:**
- Modify: `macro_clicker/editors.py:809-940`
- Modify: `scenarios/Rally gold mob_ 2 team.json`
- Test: `tests/test_rally_team_selection.py`

**Interfaces:**
- Consumes: `_rally_team_level_limits`.
- Produces: explicit legacy-fallback coverage and editor guidance.

- [ ] **Step 1: Add compatibility, persistence, and full-flow tests**

Add one test showing a scenario with no selector still uses the row action's
45 limit. Add another that sets selector Team 3 to 50, obtains prefilter cap
50, then runs final selection at level 50 and clicks Team 3. Update the saved
two-team scenario so the visible selector stores Team 3 maximum 50 while the
hidden Joining fallback remains 45, proving the duplicate is no longer the
runtime authority.

- [ ] **Step 2: Add editor guidance**

Under the smart team fields, display:

```text
These max levels also control which OCR levels Joining accepts.
Use the main Save button to keep changes after restart.
```

- [ ] **Step 3: Run focused tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_rally_team_selection.py -q
```

Expected: all tests pass.

### Task 3: Complete Verification

**Files:**
- Verify: `macro_clicker/rally_matching.py`
- Verify: `macro_clicker/editors.py`
- Verify: `tests/test_rally_team_selection.py`

- [ ] **Step 1: Run all checks**

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m mypy macro_clicker tools
```

- [ ] **Step 2: Review and commit scope**

Confirm the two-team scenario's visible values are used at runtime, the global
Joining maximum remains 65, the original one-team scenario is untouched, and
unrelated working-tree files remain uncommitted. Confirm the saved Team 3
selector maximum is 50.
