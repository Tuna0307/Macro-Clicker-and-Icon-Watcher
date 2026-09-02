# Architecture and maintenance guide

The project is a specialized Windows visual-automation and screen-monitoring application.

It originally started from a more generic macro-builder idea, but future development should follow the current implementation rather than trying to restore that original goal.

There are now two primary runtime systems:

1. **Active automation** — evaluates visual conditions and performs controlled actions, workflow transitions, retries, OCR-assisted decisions, and recovery.
2. **Passive Icon Alerts** — watches configured regions/templates and notifies the user without executing macro actions.

Both systems share the same visual detection foundation.

```text
                         Shared Detection
                         detection_core.py
                               │
                  capture / scaling / matching
                               │
                 ┌─────────────┴─────────────┐
                 │                           │
                 ▼                           ▼
          Active Automation             Icon Alerts
             engine.py                 alert_watcher.py
                 │                           │
          actions / state             confirm / cooldown
          OCR / recovery              sound / popup
```

## Module ownership

| Module | Responsibility |
| --- | --- |
| `app.py` | Main application window, automation-scenario editing, application lifecycle, scheduling, and background step previews |
| `editors.py` | Condition, action, and step dialogs plus region-preview helpers |
| `alert_watcher.py` | Passive Icon Alert controller, template management, watcher thread, confirmation, cooldown, and sound policy |
| `alert_settings.py` | Icon Alert settings model, validation, loading, and persistence |
| `alert_ui.py` | Screen/region pickers and alert popup presentation |
| `engine.py` | Active automation polling, condition evaluation, action dispatch, state transitions, retry/recovery behavior, and safe input control |
| `rally_matching.py` | Specialized row matching, atomic rally snapshots, level OCR decisions, team availability handling, team selection support, and rally evidence |
| `detection_core.py` | Shared capture, monitor/window geometry, scaling, template preparation, colored-text handling, rotations, match scoring, and candidate suppression |
| `models.py` | Scenario/action/condition dataclasses, JSON conversion, validation, scheduling fields, and scenario persistence |
| `hotkeys.py` | Physical hotkey normalization and cross-feature conflict detection |
| `level_ocr.py` | OCR engine lifecycle, preprocessing, text extraction, confidence handling, and level parsing support |
| `diagnostics.py` | Asynchronous bounded automation evidence and rotating decision metadata |
| `atomic_io.py` | Crash-safe JSON and PNG replacement |
| `project_paths.py` | Stable paths to project-owned scenarios, templates, and alert files |
| `runtime_paths.py` | Writable per-user logs, diagnostics, locks, and runtime state |
| `window_locator.py` | Window discovery, foreground checks, and coordinate conversion |
| `capture_tool.py` | Interactive template and region capture |
| `ui_components.py` | Shared Tk styles and reusable controls |
| `app_helpers.py` | Scenario/step duplication and reference remapping |
| `log_maintenance.py` | Runtime log rotation and age/count cleanup |

The `tools` package contains developer utilities that are not part of normal application startup.

## Active automation architecture

Automation follows the model:

```text
Scenario
  -> ordered Steps
      -> Conditions
      -> Actions
```

Each polling cycle evaluates enabled steps from top to bottom. A step whose conditions are not currently satisfied is skipped for that cycle rather than blocking the entire scenario.

Existing workflows use `set_step` actions to enable and disable steps as a practical state machine. Those transitions may represent important sequencing or recovery state and should not be treated as cosmetic configuration.

Current action types include:

- `click`
- `click_matching_row`
- `select_rally_team`
- `key`
- `wait`
- `set_step`
- `stop`

Some actions and fields are intentionally specialized around the bundled workflows. The project no longer requires every action to be a generic abstraction for unrelated applications.

## Specialized automation workflows

### Rally workflow

The rally workflow is spread across:

- `engine.py`
- `rally_matching.py`
- `level_ocr.py`
- scenario JSON
- stored templates
- focused tests

Important behavior includes:

- detecting desired row references and candidate target/slot matches;
- associating each candidate target with the closest valid vertical row;
- choosing the configured leftmost/rightmost target;
- level OCR and min/max eligibility checks;
- Team 1 / Team 3 availability and level-cap decisions;
- choosing an eligible team on the confirmation screen;
- carrying pre-entry team availability across screen transitions;
- retrying when OCR is unreadable or a row changes;
- guarded transition time after a successful row click;
- recovery/back-out behavior when no valid target, slot, or team is available.

The current rally system is mature and working well. Preserve its behavior unless a change is specifically needed.

#### Atomic matching/OCR snapshot

Level-filtered row decisions intentionally use one shared screen snapshot for row references, target matches, and OCR crops whenever possible.

This avoids combining information from different moments while a live list is changing. Do not replace this with independent captures without understanding the timing race it prevents.

#### Rally transition guard

`MacroEngine` includes a short post-join transition guard.

A valid target can disappear immediately after the click while the next screen is still opening. Without the guard, recovery steps may interpret that normal transition as a failure and press Back, undoing a successful join.

This is intentionally specialized behavior.

#### Team selection

The two-team workflow has different Team 1 and Team 3 level limits and availability rules.

Lower eligible levels may prefer Team 3 when it is idle, with Team 1 as fallback. Higher levels may require Team 1 according to the configured limits.

The focused tests in `tests/test_rally_team_selection.py` are the behavioral contract for this logic.

#### Fixed-slot three-team status detector

An additive checkpoint detector in `rally_matching.py` reads exact Team 1/2/3 availability from dispatch / team-selection screens that expose the fixed bottom squad cards, without changing the current two-team workflow.

Team identity comes from fixed card position, not the hero portrait. The compressed world-map expedition list is not authoritative because rows disappear and shift when teams return.

The 1920x1080 reference calibration is:

- screen anchor template: `templates/SquadAmount.png`;
- anchor ROI: `(900, 480, 130, 145)`, confidence `0.85`;
- Team 1 status ROI: `(712, 937, 40, 38)`;
- Team 2 status ROI: `(837, 937, 40, 38)`;
- Team 3 status ROI: `(963, 937, 40, 38)`;
- shared idle template: `templates/TeamIdleZZ.png`, confidence `0.90`.

`TeamIdleZZ.png` is a 9x12 glyph-only crop from the user-supplied 1920x1080 screenshot `Screenshot 2026-09-02 204523.png`, source pixels `(728, 950)` through `(736, 961)`. It intentionally excludes the hero portrait and most card-border pixels.

The taller anchor ROI deliberately covers the two observed vertical positions of the dispatch panel while remaining horizontally bounded to the `士兵數量` label. The detector scales these window-relative reference regions with the current target-window size and uses one frame for the anchor plus all three status checks. A valid screen with an idle score at or above the threshold is `IDLE`; a valid screen below threshold is `BUSY`. A missing/wrong screen, capture failure, template failure, or invalid ROI returns `UNKNOWN` rather than inferring busy.

This is not yet production three-team dispatch. `Rally gold mob_ 2 team` remains on its existing Team 1/Team 3 path until a later checkpoint explicitly integrates three-team eligibility and selection.

### Position application workflows

Bundled automation includes:

- `scenarios/Apply Development Position.json`
- `scenarios/Apply Science Position.json`

These are real supported workflows rather than example/demo scenarios.

## Passive Icon Alerts architecture

Icon Alerts are intentionally separate from active automation.

The watcher flow is approximately:

```text
capture configured region
        -> detect template
        -> confirm when required
        -> apply activation/cooldown policy
        -> sound / popup notification
```

Typical use includes event/icon detection such as Dig-related notifications.

Passive alerts should not execute automation actions unless the user explicitly asks for a feature that connects an alert detection to an automation workflow.

## Shared detection foundation

`detection_core.py` is the shared perception layer for both runtime systems.

It owns reusable behavior such as:

- DPI-aware BGR capture;
- physical monitor handling;
- monitor/window-relative regions;
- exact resolution-derived X/Y scaling;
- multi-scale template variants;
- independent X/Y resizing for aspect-ratio changes;
- static picture matching;
- animated/rotated picture matching;
- grayscale matching;
- colored-text isolation;
- low-variance safety checks;
- bounded candidate generation;
- match scoring and duplicate suppression;
- cancellation-aware matching.

Workflow-specific decisions do not belong in `detection_core.py` simply because they use image matching.

Use this rule:

- reusable perception -> `detection_core.py`;
- automation decision/action policy -> `engine.py`, `rally_matching.py`, or another automation-specific module;
- passive alert policy -> `alert_watcher.py` and alert modules.

## Window targeting and input safety

Active automation intentionally fails closed when target state is uncertain.

Important safety guarantees include:

- target-window-relative regions follow the configured window;
- monitor-relative regions follow the selected physical monitor;
- a missing required target window does not silently redirect input elsewhere;
- clicks are rejected outside the configured target window;
- mouse clicks require the correct target window to be foreground when the
  scenario-level `require_target_foreground` setting is enabled (the default);
- disabling `require_target_foreground` relaxes only the mouse foreground gate,
  so target-window containment, fresh geometry checks, monitor validation, and
  negative desktop coordinates remain enforced;
- key actions still require the correct target window to be foreground;
- target-window geometry is rechecked near input dispatch;
- `pyautogui.FAILSAFE` remains enabled;
- the scenario kill switch is required;
- stop requests are checked during waits, capture, matching, OCR, and action execution;
- unreadable OCR is treated as unknown rather than guessed.

When making performance improvements, do not remove these checks just to save a small amount of time.

## Data boundaries

Project-owned configuration and assets live outside the Python package:

- `templates/` — automation image assets and OCR references;
- `scenarios/` — saved active-automation workflows;
- `alerts/settings.json` — passive alert configuration;
- `alerts/templates/` — passive alert templates.

These paths are centralized in `project_paths.py`.

Runtime-written state belongs under the per-user directory exposed by `runtime_paths.py`, including:

- logs;
- diagnostic evidence;
- rotating decision logs;
- runtime locks/state.

Runtime modules should not derive project data paths from their own `__file__` location.

Loading a scenario or alert manifest must not silently rewrite it. Persistence should happen only through an explicit user save/update operation.

## Diagnostics boundary

Automation diagnostics are designed to explain image- and timing-dependent decisions without turning every poll into a disk-write operation.

Expensive screenshot encoding and file writes should stay outside the time-critical automation loop whenever possible. Diagnostic workers should receive bounded evidence and write asynchronously.

Icon Alerts have separate alert logging behavior and do not use the automation screenshot-evidence pipeline by default.

## Safe extension rules

1. **Preserve mature behavior first.** Working rally logic, guards, retries, and recovery paths are not refactoring targets by default.
2. **Keep perception shared, policy separate.** Put reusable matching/capture logic in `detection_core.py`, but keep automation and alert decisions in their own subsystems.
3. **Specialized code is acceptable.** Do not generalize a feature solely to support hypothetical unrelated applications.
4. **Fail closed under uncertainty.** Detection/OCR/window failures should retry or skip safely rather than guess.
5. **Change persisted models completely.** New persisted fields must update parsing, validation, serialization, UI, backward-compatible defaults, and tests together.
6. **Keep timing-critical work lean.** Screenshots, encoding, and verbose evidence should remain asynchronous or rate-limited.
7. **Respect project/runtime paths.** Use `project_paths.py` for bundled assets and `runtime_paths.py` for writable state.
8. **Add focused regression tests.** Especially for any change that may alter clicks, row selection, OCR decisions, team selection, recovery, or foreground/window safety.
9. **Leave descriptive commit history.** Explain what changed and why so future AI-assisted sessions can reconstruct the intent behind unusual code.

## Complexity hotspots

Large modules are not automatically a problem if they are stable and well tested. Refactor only when there is a concrete development benefit.

Current hotspots include:

- `alert_watcher.py` — template management, watcher loop, UI/controller behavior, and sound policy;
- `engine.py` — polling lifecycle, generic action dispatch, input safety, and some specialized workflow guards;
- `rally_matching.py` — row matching, OCR arbitration, team availability, and diagnostic serialization;
- `detection_core.py` — multi-scale candidate generation and matching pipeline;
- `models.py` — a broad persisted model supporting multiple specialized actions.

If extraction becomes necessary, prefer one small test-backed responsibility at a time. Avoid large rewrites across several hotspots simultaneously.

## AI-assisted development workflow

Future AI coding sessions should read `AGENTS.md` before making changes.

Before editing a mature subsystem:

1. inspect the implementation;
2. inspect the focused tests;
3. identify the real behavior being preserved or changed;
4. make the smallest coherent modification;
5. run the relevant focused tests plus the repository quality checks;
6. document the reason for the change in the commit message.

The intended goal is a reliable specialized automation/monitoring application, not architectural purity or generic reuse.
