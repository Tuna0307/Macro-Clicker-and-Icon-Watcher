# AI Development Context

This file is intended for AI coding assistants and future development sessions.
Read it before making architectural changes.

Also read these focused guides when relevant:

- `docs/ARCHITECTURE.md` — module ownership and runtime boundaries.
- `docs/MAINTAINABILITY.md` — what is protected, what is safe cleanup, and when refactoring is justified.
- `docs/TESTING.md` — automated checks, screenshot fixtures, headless limitations, and live verification.

## Project direction

This repository originally started as a more general-purpose macro builder, but that is **not the current design goal**.

The project is now a specialized Windows visual-automation and screen-monitoring application built around the workflows, templates, timing, and UI states already present in this repository.

Do **not** spend development effort trying to make existing specialized behavior generic for unrelated applications. New work should preserve and extend the current workflows unless the user explicitly asks for a redesign.

The public-facing project description should remain neutral. Do not rename the project, modules, or documentation to explicitly identify the external application being automated unless the user asks for that change.

## Two main runtime systems

The application has two distinct high-level functions that share the same vision foundation.

### 1. Active automation

Main runtime:

- `macro_clicker/engine.py`

Supporting modules include:

- `macro_clicker/rally_matching.py`
- `macro_clicker/level_ocr.py`
- `macro_clicker/models.py`
- `macro_clicker/window_locator.py`
- `macro_clicker/diagnostics.py`

Automation evaluates visual conditions and then performs actions such as clicks, keys, waits, row-based target selection, OCR-assisted decisions, workflow transitions, retries, and recovery.

Important bundled automation currently includes:

- one-team and two-team rally workflows;
- same-row reference/target matching;
- level OCR and level filtering;
- Team 1 / Team 3 availability and selection behavior;
- rally entry, joining, confirmation, and recovery/back-out states;
- Development Position application workflow;
- Science Position application workflow;
- one-time scheduled starts and configurable waits.

The rally automation is mature and currently works well. Treat it as protected behavior. Do not rewrite or simplify it merely because some conditions, waits, step names, guards, retries, or recovery actions look redundant in isolation.

### 2. Passive Icon Alerts

Main runtime:

- `macro_clicker/alert_watcher.py`

Supporting modules include:

- `macro_clicker/alert_settings.py`
- `macro_clicker/alert_ui.py`

Icon Alerts continuously scan selected screen regions for configured image or text templates and notify the user rather than taking automation actions.

Examples include detecting saved event icons such as Dig-related images and producing a sound/popup alert.

This system has its own confirmation, cooldown, activation, popup, and sound policy. Do not route passive alerts through the macro action engine unless the user explicitly requests that behavior.

## Shared vision foundation

Main module:

- `macro_clicker/detection_core.py`

Both automation and Icon Alerts use this shared implementation for reusable screen perception.

It owns behavior such as:

- DPI-aware screen capture;
- physical monitor handling;
- target-window and monitor-relative regions;
- template scaling for resolution changes;
- independent X/Y scaling when aspect ratio changes;
- static, animated/rotating, grayscale, and colored-text matching;
- low-variance protection;
- match scoring and candidate suppression;
- cancellation-aware matching.

Reusable detection improvements belong here when both runtime systems can benefit from them. Workflow policy should remain in the automation or alert layer.

## Automation model

The automation model is:

```text
Scenario
  -> Steps
      -> Conditions
      -> Actions
```

Steps are polled repeatedly. Conditions determine whether a step is ready. Actions then perform work and may enable/disable other steps.

The enabled-step mechanism is used by existing workflows as a practical state machine. Do not assume every enabled/disabled transition is merely UI configuration; some transitions are part of recovery and sequencing logic.

Current action types include:

- `click`
- `click_matching_row`
- `select_rally_team`
- `key`
- `wait`
- `set_step`
- `stop`

Some action fields and action types are intentionally specialized. It is acceptable for this project to contain specialized automation logic.

## Rally workflow notes

Rally behavior spans `engine.py`, `rally_matching.py`, scenario JSON, templates, OCR, and tests.

Important concepts include:

- identifying desired row anchors;
- finding available target/slot matches;
- associating each target with the closest valid vertical row;
- choosing the configured leftmost/rightmost target;
- OCR-reading a level associated with a row;
- applying configured level limits;
- checking Team 1 and Team 3 availability;
- selecting the appropriate team according to level and availability;
- retrying when OCR is unreadable or the visible row changes;
- preserving pre-entry team availability while the queue screen is temporarily hidden;
- recovery when there is no valid mob, no usable slot, or no eligible idle team.

### Atomic screenshots

For level-filtered row selection, reference matches, target matches, and OCR crops are intentionally based on one atomic screenshot when possible.

This prevents decisions from mixing information from different moments while the list is updating.

Do not replace this with multiple independent captures without understanding the race condition it solves.

### Transition guard

`MacroEngine` contains a short rally join transition guard.

After a valid row is clicked, a visible Join/slot target can disappear before the next screen is fully open. During that interval, normal recovery logic could otherwise misinterpret the transition as a failed join and press Back.

The guard and the associated blocked/recovery steps are intentional behavior.

### Team selection

The smart two-team flow uses Team 1 and Team 3 level limits and availability checks.

The behavior is not simply "always use one team first." Lower eligible levels can prefer Team 3 when available, with Team 1 as fallback, while higher levels may require Team 1 according to configured limits.

Tests in `tests/test_rally_team_selection.py` protect this behavior.

## Position application workflows

Bundled scenarios currently include:

- `scenarios/Apply Development Position.json`
- `scenarios/Apply Science Position.json`

Treat these as real specialized workflows, not sample/demo scenarios that may be freely removed or renamed.

## Alert workflow notes

Icon Alerts are passive observation.

Typical flow:

```text
capture
  -> detect template
  -> confirm if required
  -> apply cooldown
  -> sound / popup
```

Only templates enabled for detection should be scanned.

Alert scanning and macro automation may run under different performance and confirmation requirements even though they share `detection_core.py`.

## Safety invariants

The current code deliberately fails closed in many uncertain situations. Preserve that philosophy.

Important safety behavior includes:

- do not click when target-window geometry is unavailable;
- do not click outside the target window when a target window is configured;
- mouse foreground validation is scenario-configurable through
  `require_target_foreground`, which defaults to `true`;
- when that setting is `false`, only the mouse foreground requirement is relaxed:
  target-window containment, fresh geometry, monitor validity, negative desktop
  coordinates, kill-switch handling, and PyAutoGUI fail-safe remain enforced;
- key actions still require the configured target window to be foreground;
- recheck target-window geometry close to input dispatch;
- preserve `pyautogui.FAILSAFE`;
- preserve the required scenario kill switch;
- check stop requests during waits, captures, matching, OCR, and action execution;
- treat unreadable OCR as unknown rather than inventing a value;
- retry uncertain automation states rather than guessing when possible.

A change that makes the automation faster but removes these fail-closed checks should be treated as a regression unless the user explicitly approves the tradeoff.

## Diagnostics

Automation diagnostics exist because many failures are timing- or image-dependent and cannot be understood from source code alone.

`macro_clicker/diagnostics.py` provides bounded diagnostic evidence and rotating decision metadata.

Rally diagnostics can record items such as:

- row/reference matches;
- target matches;
- template scores;
- OCR crops, text, and confidence;
- configured level limits;
- team availability/selection decisions;
- row changes and retry reasons;
- final decision metadata.

Keep expensive screenshot encoding and disk writes away from the timing-critical matching loop whenever possible.

## Data and persistence boundaries

Project-owned configuration/assets:

- `scenarios/`
- `templates/`
- `alerts/settings.json`
- `alerts/templates/`

Use `macro_clicker/project_paths.py` for project-owned paths.

Writable runtime state such as logs, diagnostics, locks, and temporary state belongs under the per-user data directory exposed by `macro_clicker/runtime_paths.py`.

Loading configuration should not silently rewrite it. Persistence should happen only through an intentional save/update path.

## When adding a new automation feature

Prefer the following approach:

1. Identify the visual state(s) required to make a decision.
2. Reuse the shared capture/matching primitives where possible.
3. Keep feature-specific decision policy separate from passive alert behavior.
4. Decide explicitly what happens when detection is uncertain.
5. Add retry/recovery paths before assuming the happy path is permanent.
6. Add focused regression tests for any logic that could send input to the wrong place or choose the wrong target.
7. Avoid modifying mature rally behavior unless the new feature actually needs it.

A feature does not need to be reusable by other games/applications to belong in this repository.

## When adding a new alert feature

Prefer:

1. reusable detection in `detection_core.py` when appropriate;
2. alert-specific confirmation/cooldown policy in the alert subsystem;
3. passive notification by default;
4. no macro action unless the user explicitly wants that detection to drive automation.

## Validation before finishing a code change

The repository uses the following checks:

```powershell
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
python -m mypy macro_clicker tools
python -m tools.validate_scenarios
```

Current CI policy:

- **Blocking:** pytest, Ruff lint, scenario/template validation.
- **Informational:** Ruff formatting, mypy.

Formatting/type-hint feedback should still be reviewed, but it should not be treated as proof that a working runtime path is broken. See `docs/TESTING.md` for the testing layers and when screenshots/live verification are appropriate.

For changes to stored scenarios or models, validate both the Python code and scenario/template integrity.

## Guidance for AI-generated changes

Before editing code:

- inspect the existing implementation and focused tests;
- assume unusual guards/retries may encode a real previously observed failure mode;
- prefer small, behavior-preserving changes;
- do not broadly refactor a working subsystem just to make it look more generic or theoretically cleaner;
- update tests when intentionally changing behavior;
- leave a descriptive commit message explaining both **what changed** and **why** so future AI sessions can reconstruct the development history.

If a request is ambiguous, preserve existing working behavior rather than inventing a new interpretation.
