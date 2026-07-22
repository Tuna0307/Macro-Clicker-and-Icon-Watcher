# Per-Team Rally Idle Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make level 1–45 rallies reliably select idle Stetmann before Murphy by using separate idle templates, while recording every fallback decision from the exact dispatch frame.

**Architecture:** Extend `Action` with optional Team 1 and Team 3 idle-template paths that fall back to the existing shared path for backward compatibility. The selector loads the effective template per candidate from one atomic panel snapshot, logs all evaluated scores, and captures that snapshot whenever low-level selection falls back from Team 3 to Team 1.

**Tech Stack:** Python 3.12, OpenCV, NumPy, dataclasses/JSON, Tkinter/ttk, pytest/unittest.

## Global Constraints

- Team 1 is Murphy and accepts levels 1 through 65.
- Team 2 is Carlie and is never a rally candidate.
- Team 3 is Stetmann and accepts levels 1 through 45.
- Keep the idle confidence threshold at 0.85.
- Preserve `team_idle_template_path` as the fallback for older scenarios.
- Use one atomic dispatch-panel snapshot for all candidate scores and diagnostics.
- Preserve unrelated working-tree changes.

---

### Task 1: Add Per-Team Idle Template Configuration and Assets

**Files:**
- Create: `templates/Team1Idle.png`
- Create: `templates/Team3Idle.png`
- Create: `tests/fixtures/rally_team_selection/both_idle_union.png`
- Modify: `macro_clicker/models.py`
- Modify: `macro_clicker/editors.py`
- Modify: `scenarios/Rally gold mob_ 2 team.json`
- Modify: `tests/test_rally_team_selection.py`

**Interfaces:**
- Consumes: existing `Action.team_idle_template_path: str` and `portable_project_path(path) -> str`.
- Produces: `Action.team1_idle_template_path: str`, `Action.team3_idle_template_path: str`, and effective-path fallback `specific_path or team_idle_template_path`.

- [ ] **Step 1: Crop the durable reference assets**

From `codex-clipboard-6bef5f1c-fd8c-4534-9bc6-8391f77136a5.png`, create:

```text
Team1Idle.png: x=720, y=945, width=24, height=23
Team3Idle.png: x=971, y=945, width=24, height=23
both_idle_union.png: x=713, y=938, width=292, height=36
```

Verify each write succeeds and the resulting dimensions match exactly.

- [ ] **Step 2: Write failing model/scenario tests**

Extend the action round-trip and scenario assertions:

```python
action.team1_idle_template_path = "templates/Team1Idle.png"
action.team3_idle_template_path = "templates/Team3Idle.png"
restored = Action.from_dict(action.to_dict())
self.assertEqual(restored.team1_idle_template_path, action.team1_idle_template_path)
self.assertEqual(restored.team3_idle_template_path, action.team3_idle_template_path)
```

```python
self.assertEqual(
    team_action.team1_idle_template_path,
    "templates/Team1Idle.png",
)
self.assertEqual(
    team_action.team3_idle_template_path,
    "templates/Team3Idle.png",
)
```

Add a backward-compatibility assertion that an older action containing only
`team_idle_template_path` still validates.

- [ ] **Step 3: Run the model tests and verify they fail**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_rally_team_selection.py -q
```

Expected: FAIL because `Action` has no team-specific idle-template fields and the scenario does not contain them.

- [ ] **Step 4: Implement the model, validation, editor, and scenario fields**

In `Action`, add two empty-string fields. Serialize them with
`portable_project_path`, parse them as strings, and validate that each
`select_rally_team` action has an effective template:

```python
team1_idle_template_path: str = ""
team3_idle_template_path: str = ""

effective_team1 = action.team1_idle_template_path or action.team_idle_template_path
effective_team3 = action.team3_idle_template_path or action.team_idle_template_path
```

When `require_files=True`, verify both effective paths exist. Update the editor
to show separate Team 1 and Team 3 template fields and browse buttons, while
retaining the shared field for older actions. Store both paths in `on_ok`.
Configure the two-team scenario with `templates/Team1Idle.png` and
`templates/Team3Idle.png`.

- [ ] **Step 5: Run focused tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_rally_team_selection.py -q
```

Expected: all focused tests pass.

- [ ] **Step 6: Commit configuration and assets**

```powershell
git add -- templates/Team1Idle.png templates/Team3Idle.png tests/fixtures/rally_team_selection tests/test_rally_team_selection.py macro_clicker/models.py macro_clicker/editors.py "scenarios/Rally gold mob_ 2 team.json"
git commit -m "feat: configure per-team rally idle templates"
```

### Task 2: Use Per-Team Templates and Capture Preferred-Team Fallbacks

**Files:**
- Modify: `macro_clicker/engine.py`
- Modify: `tests/test_rally_team_selection.py`

**Interfaces:**
- Consumes: `Action.team1_idle_template_path`, `Action.team3_idle_template_path`, and the shared fallback path.
- Produces: candidate records containing `team`, `region`, `click`, `max_level`, `template_path`, and `score`; diagnostic event `rally_team_preferred_fallback` with `decision="preferred_team_fallback"`.

- [ ] **Step 1: Write the failing real-frame priority test**

Load the scenario action, set `_pending_rally_level = 45`, return
`both_idle_union.png` from `_grab` with origin `(713, 938)`, and use anchor
`(962, 808)`. Call `_run_select_rally_team_action` and assert:

```python
self.assertTrue(result)
self.assertEqual(clicked, [(1025, 976, "left")])
self.assertIn("Team 3=1.00", "\n".join(logs))
```

This must exercise the real template files and real selector code.

- [ ] **Step 2: Run the priority test and verify it fails on Murphy fallback**

Run the single new test. Expected: FAIL because the engine still loads
`team_idle_template_path` for every candidate and clicks Team 1 at `(773, 976)`.

- [ ] **Step 3: Implement per-candidate template loading**

When building each candidate, attach:

```python
specific_path = getattr(action, f"team{team_number}_idle_template_path", "")
candidate["template_path"] = specific_path or action.team_idle_template_path
```

Inside the candidate loop, load and scale that candidate's template before
matching its region. Keep the order `(3, 1)` and stop after the first accepted
candidate.

- [ ] **Step 4: Run the priority test and focused suite**

Expected: idle Stetmann scores at least 0.85, Team 3 is clicked, and the focused
suite passes.

- [ ] **Step 5: Write failing fallback evidence tests**

Create a low-level case where Team 3 scores below threshold and Team 1 passes.
Assert that the successful log contains both evaluated scores:

```python
self.assertIn("Team 3=0.00, Team 1=1.00", "\n".join(logs))
```

Assert one diagnostic submission with:

```python
self.assertEqual(event_type, "rally_team_preferred_fallback")
self.assertEqual(metadata["decision"], "preferred_team_fallback")
self.assertEqual(metadata["selected_team"], 1)
self.assertEqual(metadata["level"], 45)
self.assertIs(context_snapshot, captured_snapshot)
```

The snapshot assertion may compare the submitted frame and origin to the exact
frame returned by `_grab`; no second capture is allowed.

- [ ] **Step 6: Run the evidence tests and verify they fail**

Expected: FAIL because successful fallback currently logs only Murphy's score
and emits no diagnostic.

- [ ] **Step 7: Implement complete score logging and fallback diagnostics**

Build `score_text` from every evaluated candidate before clearing the pending
level. Log:

```text
select idle Team 1 for mob level 45 (Team 3=0.00, Team 1=1.00)
```

If Team 1 is selected and Team 3 was an evaluated candidate, call
`_submit_rally_diagnostic` with event `rally_team_preferred_fallback`, all
candidate metadata/matches, a stable throttle key, and the existing
`context_snapshot=snapshot`. Do not alter the successful click or retry state.

- [ ] **Step 8: Run focused tests and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_rally_team_selection.py -q
git add -- macro_clicker/engine.py tests/test_rally_team_selection.py
git commit -m "feat: diagnose rally preferred-team fallbacks"
```

### Task 3: Full Verification and Live-Retest Handoff

**Files:**
- Verify: `macro_clicker/models.py`
- Verify: `macro_clicker/editors.py`
- Verify: `macro_clicker/engine.py`
- Verify: `scenarios/Rally gold mob_ 2 team.json`
- Verify: `templates/Team1Idle.png`
- Verify: `templates/Team3Idle.png`
- Verify: `tests/test_rally_team_selection.py`

**Interfaces:**
- Consumes: Tasks 1 and 2.
- Produces: verified code and instructions for a fresh live run.

- [ ] **Step 1: Verify the supplied screenshot scores**

Run the real-frame priority test with `-vv` and confirm Team 3 is selected at
level 45.

- [ ] **Step 2: Run all checks**

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m mypy macro_clicker tools
```

Expected: zero failures or issues.

- [ ] **Step 3: Review the scoped diff and scenario paths**

Confirm both new templates exist, scenario validation passes with
`require_files=True`, the original one-team scenario remains untouched, and
unrelated working-tree files are preserved.

- [ ] **Step 4: Hand off the live retest**

Tell the user to press F12, close and relaunch the application, run
`Rally gold mob_ 2 team`, and inspect the next low-level selection log for both
candidate scores. Explain where the exact fallback diagnostic will be saved.
