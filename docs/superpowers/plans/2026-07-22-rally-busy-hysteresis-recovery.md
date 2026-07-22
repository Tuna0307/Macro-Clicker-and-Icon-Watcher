# Rally Busy-State Hysteresis and Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent transition frames from falsely releasing a busy rally team and recover safely when final team availability disagrees with the map prefilter.

**Architecture:** Apply per-team hysteresis inside the existing map availability scan, using the last confirmed state and a `0.50` busy-release threshold. Add an action-loop abort signal so final selection failure skips the remaining attack actions but leaves existing scenario recovery steps available.

**Tech Stack:** Python 3.12, OpenCV, dataclasses/JSON, pytest/unittest.

## Global Constraints

- Team 1 accepts levels 1 through 65.
- Team 3 accepts levels 1 through 45 and must never be considered above 45.
- Preserve the configured first-detection busy threshold of `0.85`.
- Use `0.50` only to release a team that was previously confirmed busy.
- Do not add another screen capture or delay to the rally-entry fast path.
- Never click `Attack.png` unless a team was selected successfully.
- Preserve unrelated working-tree changes.

---

### Task 1: Add Map Busy-State Hysteresis

**Files:**
- Modify: `macro_clicker/rally_matching.py:305-388`
- Test: `tests/test_rally_team_selection.py`

**Interfaces:**
- Consumes: `_last_rally_team_availability["busy"]` and current template scores.
- Produces: conservative `busy` values and `level_cap` without an extra capture.

- [ ] **Step 1: Write the failing score-sequence test**

Drive `_available_rally_team_level_cap` through Team 1 scores `1.00`, `0.70`,
and `0.29`, with Team 3 absent. Assert caps `45`, `45`, and `65`.

- [ ] **Step 2: Verify the test fails**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_rally_team_selection.py::RallyTeamSelectionTests::test_busy_team_requires_a_clear_score_drop_before_becoming_idle -q
```

Expected: the second cap is incorrectly `65`.

- [ ] **Step 3: Implement minimal hysteresis**

For each team, calculate:

```python
was_busy = previous_busy.get(team_number, False)
threshold = 0.50 if was_busy else action.team_busy_confidence
busy[team_number] = scores[team_number] >= threshold
```

Record the effective thresholds in `_last_rally_team_availability` for
diagnostics.

- [ ] **Step 4: Run the focused test and suite**

Expected: the score-sequence test and all rally team tests pass.

### Task 2: Abort Failed Dispatch and Allow Existing Recovery

**Files:**
- Modify: `macro_clicker/engine.py:1407-1575,1707-1741`
- Test: `tests/test_rally_team_selection.py`

**Interfaces:**
- Produces: `_abort_current_step: bool`, cleared `_pending_rally_level`, and no retry loop when no eligible final team exists.

- [ ] **Step 1: Write the failing selector test**

For level 50 with Team 1 unavailable, assert no click, pending level cleared,
`_abort_current_step is True`, and `_retry_current_step is False`.

- [ ] **Step 2: Verify the test fails**

Run the single test. Expected: selector sets retry and retains level 50.

- [ ] **Step 3: Implement selector abort state**

In the no-selection branch:

```python
self._pending_rally_level = None
self._retry_current_step = False
self._abort_current_step = True
return False
```

- [ ] **Step 4: Make the action loop honor abort**

Reset `_abort_current_step` before each fired step. After every action, break
the action loop when abort is set, without setting `retry_step`. This skips the
wait and Attack click while allowing later recovery steps to be evaluated.

- [ ] **Step 5: Run focused tests**

Expected: all rally selection tests pass and successful team selection remains
unchanged.

### Task 3: Verify and Commit

**Files:**
- Verify: `macro_clicker/rally_matching.py`
- Verify: `macro_clicker/engine.py`
- Verify: `tests/test_rally_team_selection.py`

- [ ] **Step 1: Run complete checks**

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m mypy macro_clicker tools
```

- [ ] **Step 2: Review scope and commit**

Confirm the level ranges remain 65 and 45, `Rally Gold Mob.json` is not
included, and unrelated working-tree changes remain untouched. Commit only the
implementation and its regression tests.
