# PC Macro Builder

A Windows desktop utility for **visual automation** and **passive screen monitoring**.

The project has two main runtime functions:

1. **Automation** — watches the screen for configured visual conditions and runs multi-step actions such as clicks, key presses, waits, row-based target selection, OCR-assisted decisions, and workflow transitions.
2. **Icon Alerts** — continuously monitors selected screen regions for configured images or text and notifies the user with sound and popup alerts without performing macro actions.

Both systems share the same screen-capture and image-recognition foundation, including OpenCV template matching, DPI-aware coordinates, window/monitor-relative regions, resolution scaling, and optional OCR.

> The repository contains tuned workflows, templates, and behavior developed for the bundled screen layouts. It is no longer treated as a blank general-purpose macro framework. Existing workflow-specific timing, recovery, matching, and safety behavior should be preserved when extending the project.

## Setup

Using a project virtual environment is recommended so OCR and image-processing packages do not conflict with packages installed for other programs:

```powershell
py -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m macro_clicker
```

On Windows, `Run PC Macro Builder.bat` starts the GUI without a console and automatically uses `.venv` when it exists.

Startup failures are shown in a dialog and recorded under:

```text
%LOCALAPPDATA%\Macro Clicker and Icon Watcher\logs
```

Set `MACRO_CLICKER_DATA_DIR` to use a different runtime-data location.

If the target application runs elevated, run the macro utility with matching privileges or Windows may block clicks and key events sent by `pyautogui` / `keyboard`.

## Application overview

```text
                         Shared Detection Layer
                      macro_clicker/detection_core.py
                                  │
                     capture / scale / match / OCR
                                  │
                 ┌────────────────┴────────────────┐
                 │                                 │
                 ▼                                 ▼
        Automation / Macro                  Passive Icon Alerts
        macro_clicker/engine.py             macro_clicker/alert_watcher.py
                 │                                 │
        Conditions → Decisions              Detect → Confirm
                 │                                 │
        Actions → Verification              Cooldown → Sound / Popup
                 │
        Retry / Recovery / State
```

The two workflows intentionally share visual detection but keep their behavior separate:

- **Automation** converts matches into controlled input and workflow transitions.
- **Icon Alerts** observes the screen and reports detections without taking macro actions.

## Automation system

Automation is scenario-driven. A scenario contains ordered **Steps**, and each Step contains **Conditions** and **Actions**.

### Scenario

A named set of steps stored under `scenarios/*.json`.

Scenario settings include:

- Start hotkey
- Kill-switch / stop hotkey
- Poll interval
- Optional one-time scheduled start using the computer's local clock
- Target window selection
- Monitor selection
- Diagnostic collection

### Step

A step is evaluated during each polling cycle when it is enabled and outside its cooldown.

If the step's visual conditions are not currently satisfied, the engine skips it for that cycle rather than blocking the entire scenario.

Steps can enable or disable other steps, allowing bundled workflows to behave like small state machines with detection, action, confirmation, retry, and recovery phases.

### Conditions

Conditions currently use visual template matching and support:

- Image present / absent checks
- AND / OR combinations
- Per-condition confidence
- Optional comparison templates
- Screen, monitor, or target-window-relative regions
- Resolution-aware template scaling
- Static, animated/rotating, and colored-text matching modes
- Grayscale picture matching when configured

Templates can be captured directly from the screen using the built-in capture tools.

### Actions

Supported action types include:

- `click`
- `click_matching_row`
- `select_rally_team`
- `key`
- `wait`
- `set_step`
- `stop`

Some actions are intentionally specialized for the advanced bundled workflows. They should not be simplified into generic clicks unless the existing behavior and tests are preserved.

### Row-based automation

`click_matching_row` is used when a target button must belong to the same visible row as another detected object.

The engine can:

- detect multiple row references and multiple candidate targets;
- associate each target with the closest valid row;
- choose the leftmost or rightmost target in that row;
- process the first valid row or multiple rows;
- scale row tolerance with the detected geometry;
- use OCR-based level filtering where configured;
- revalidate a selected row after a pre-click delay before acting.

For OCR-filtered row decisions, the reference matches, target matches, and OCR crop regions are derived from one atomic screenshot whenever possible. If the level cannot be read reliably or the row changes before the click, the workflow retries instead of guessing.

## Icon Alerts

Icon Alerts are a separate passive-monitoring feature.

The user can save image/text templates, choose which ones are active, and configure where they should be detected. The watcher scans those templates independently from the automation scenario engine.

Typical alert behavior is:

```text
Capture screen region
        ↓
Find configured template
        ↓
Confirm detection when required
        ↓
Apply per-template cooldown
        ↓
Sound / popup notification
```

Icon Alerts do **not** execute the macro scenario actions and do not collect the automation diagnostic screenshots.

Use **Detect this icon** (or Space when the item is selected) to control which saved alert templates are actively scanned.

## Shared detection foundation

Automation and Icon Alerts both use `macro_clicker/detection_core.py` for the underlying visual-processing pipeline.

Shared capabilities include:

- DPI-aware BGR screen capture
- Physical-monitor handling
- Target-window-relative and monitor-relative regions
- Exact X/Y resolution scaling
- Multi-scale template preparation
- Static and rotated template variants
- Colored-text isolation
- Grayscale matching
- Low-variance safety checks
- Match scoring and duplicate suppression
- Cancellation-aware matching

When a template has a reference window or monitor size, the matcher inserts the exact current X/Y scale before trying fallback scales. This allows saved regions and detected-target offsets to follow common resolution and aspect-ratio changes without relying only on absolute coordinates.

Automation and alerts deliberately use different fallback ranges because they have different timing requirements, while known resolution changes can still inject exact scale candidates outside those fallback ranges.

## Window targeting and input safety

A scenario or alert configuration can target a window by title substring.

When a target window is configured:

- New regions can be stored relative to the target window.
- Proportional region metadata lets them follow window resizing.
- Detection follows the physical monitor containing the target window.
- Missing target windows fail closed instead of silently clicking elsewhere.
- Macro clicks are rejected when the resolved point is outside the target window.
- Mouse-click foreground validation is controlled per scenario by
  `require_target_foreground` and defaults to `true` for backward-compatible
  safety. When disabled, clicks may target a visible window on another monitor
  while another application is foreground, but target-window containment and
  geometry checks still apply.
- Key actions continue to require the selected target window to be in the foreground,
  regardless of the mouse-click foreground setting.
- Window geometry is rechecked around input operations so a moved window does not receive stale coordinates.
- The application does not automatically raise or focus the target window.

Multi-monitor coordinates remain absolute desktop coordinates, including valid
negative X/Y positions for monitors located left of or above the primary display.
Coordinates are not clamped to the primary monitor.

When no target window is configured, monitor-relative and legacy absolute-screen behavior remain available.

`pyautogui.FAILSAFE` remains enabled, and the scenario kill switch is checked throughout captures, matching, waits, and action execution.

## Interface preferences and scheduling

Open **Scenario settings** to configure scenario hotkeys, target settings, diagnostics, and the optional one-time automatic start.

A scheduled start:

- uses the computer's local time;
- requires the application and selected scenario to remain open;
- runs only once;
- disables itself after it runs or expires;
- does not restart a scenario that is already running.

The application also stores shared interface preferences such as sound and animation settings in the per-user data directory.

## Detection types

Automation conditions and Icon Alert templates share the same main detection modes:

- **Text / colored text** — isolates foreground text color so changing backgrounds have less influence on the match.
- **Static picture** — searches configured scales without rotation.
- **Animated/rotating picture** — searches configured scales at small positive and negative rotations for icons that visibly tilt or move.

Alert and automation workflows may apply different confirmation or fallback policies even when they use the same underlying detection mode.

## Diagnostics

Automation scenarios can collect bounded diagnostic evidence when diagnostics are enabled.

Runtime evidence is stored under:

```text
%LOCALAPPDATA%\Macro Clicker and Icon Watcher\logs\diagnostics
```

The collector is selective so long-running automation does not save a full screenshot for every normal poll. Important OCR failures, ambiguous decisions, row changes, near misses, and sampled successful decisions can retain evidence for later review.

Diagnostic events can contain:

- annotated context screenshots;
- OCR crops;
- template scores;
- matched row / target geometry;
- OCR text and confidence;
- configured decision limits;
- the final automation decision.

Decision metadata is also written to a bounded rotating JSONL log. Screenshot retention is limited by count, age, and total storage size.

Icon Alerts use their own alert logging behavior and do not save the automation diagnostic screenshots.

## Project layout

```text
macro_clicker/   Main application, automation engine, alerts, detection, OCR, diagnostics, and UI
tools/           Developer validation utilities
tests/           Automated regression, safety, matching, and workflow tests
templates/       Automation image assets and OCR references
scenarios/       Saved automation workflows
alerts/          Passive Icon Alert settings and templates
docs/            Architecture and maintenance notes
launcher.pyw     Windows GUI entry point used by the launch scripts
```

Important module ownership is documented in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

Generated caches, logs, and runtime diagnostic captures do not belong in the repository.

## Development checks

Install the development dependencies:

```powershell
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
```

Run the same main checks used by CI:

```powershell
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\python -m ruff check .
.\.venv\Scripts\python -m ruff format --check .
.\.venv\Scripts\python -m mypy macro_clicker tools
.\.venv\Scripts\python -m tools.validate_scenarios
```

The repository's Windows CI runs the test suite, Ruff lint/format checks, mypy, and scenario/template validation on pushes and pull requests.

## Development guidance

This codebase contains mature workflow-specific behavior that has been tuned around real screen transitions and timing. When changing existing automation:

- Prefer small, test-backed changes over broad rewrites.
- Preserve fail-closed behavior when detection, OCR, window state, or input validation is uncertain.
- Keep reusable capture and matching logic in the shared detection layer.
- Keep automation policy in the automation runtime and passive-alert policy in the alert watcher.
- Do not remove delays, recovery paths, revalidation, or specialized actions simply because they look redundant without first checking the tests and the workflow that relies on them.

The current code should be treated as a specialized visual automation and monitoring application built around its existing workflows, not as an empty framework that must remain generic for unrelated uses.
