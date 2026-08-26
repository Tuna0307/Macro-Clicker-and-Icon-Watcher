# PC Automation Bot

A Windows desktop utility for **visual automation** and **passive screen monitoring**.

The normal interface is now organized like a dedicated automation bot: users configure the behavior they want through simple feature pages instead of editing low-level Steps, Conditions, Actions, image regions, or OCR details.

The existing scenario engine remains underneath as the proven automation backend, and the original editor is still available through **Advanced** for development and debugging.

## Main interface

The Bot interface currently contains:

- **Dashboard** — bot status, current task, passive-alert status, Start/Stop, and quick actions.
- **Rally** — configure supported mob/team level limits and join delay without editing the Rally scenario.
- **Gather** — configure Gold gathering start level, number of marches, and busy-march replacement order.
- **Positions** — enable or run the bundled Development and Science position workflows.
- **Alerts** — start/stop passive image alerts and control common alert groups.
- **Schedule** — configure bot start/stop times and active weekdays.
- **Logs** — view runtime activity directly from the normal interface.
- **Settings** — target-window settings and access to Advanced tools.

Low-level automation internals remain available when needed:

- **Advanced** — Scenario / Step / Condition / Action editor and debugging tools.
- **Alert Setup** — detailed passive-alert template configuration.

These advanced surfaces are hidden during normal use and can be opened from the Bot interface. The legacy Scenario-editor start hotkey is registered only while **Advanced** is actually open, so a normal Bot session cannot accidentally start a hidden Advanced scenario.

## Setup

Using a project virtual environment is recommended:

```powershell
py -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m macro_clicker
```

For normal Windows use, double-click:

```text
Run PC Automation Bot.bat
```

`Run PC Automation Bot.vbs` provides the same no-console launch directly. The older `Run PC Macro Builder.bat` / `.vbs` names are retained as compatibility launchers so existing shortcuts continue to work.

Startup failures are shown in a dialog and recorded under:

```text
%LOCALAPPDATA%\Macro Clicker and Icon Watcher\logs
```

Set `MACRO_CLICKER_DATA_DIR` to use a different writable runtime-data location.

If the target application runs elevated, run this utility with matching privileges or Windows may block input sent by `pyautogui` / `keyboard`.

## Architecture overview

```text
                          Bot UI
                            │
                        BotConfig
                            │
                 user-facing settings
                            │
              ┌─────────────┴─────────────┐
              ▼                           ▼
       Feature adapters              BotController
       configure runtime           serializes clicking
          scenarios                    features
              │                           │
              └─────────────┬─────────────┘
                            ▼
                       MacroEngine
                            │
                  Scenario / Steps
                            │
               Detection / OCR / Safety
                            │
              ┌─────────────┴─────────────┐
              │                           │
              ▼                           ▼
      Active automation             Passive alerts
                                    alert_watcher.py
```

The dedicated Bot UI is a **control layer**, not a replacement for the working automation engine.

For development details, see:

- `AGENTS.md`
- `docs/BOT_UI.md`
- `docs/ARCHITECTURE.md`
- `docs/MAINTAINABILITY.md`
- `docs/TESTING.md`
- `docs/AUTO_GATHER.md`

## Bot configuration

Normal-user settings are stored separately from the bundled scenario files:

```text
%LOCALAPPDATA%\Macro Clicker and Icon Watcher\bot_config.json
```

This configuration contains user-facing choices such as Rally levels, Gather settings, enabled features, alert preferences, scheduling, and target-window title.

Saving normal Bot settings does **not** rewrite the tuned scenario files in `scenarios/`. Instead, the application loads a bundled scenario, clones it in memory, applies the configured user values, validates the result, and runs that runtime copy.

This keeps low-level image matching, recovery, OCR, and safety details protected from ordinary settings changes.

## Active automation coordination

Only one active clicking automation owns the target application at a time. Passive alerts can run alongside it because they observe rather than click.

The initial **Start Bot** cycle runs enabled finite tasks before continuous Rally:

```text
Development Position
        ↓
Science Position
        ↓
Auto Gather
        ↓
Gold Mob Rally
```

Disabled features are skipped.

This is intentionally serialized rather than allowing multiple automation scenarios to fight over mouse/keyboard input. If a queued feature cannot establish its expected starting state, the Bot cycle stops rather than silently skipping ahead to another clicking workflow.

Direct **Run Rally**, **Run Gather**, and Position buttons remain available for one-off runs.

## Rally configuration

The Bot Rally page exposes the values a normal user is expected to change, while the mature matching/OCR workflow remains internal.

Current user-facing controls include:

- minimum eligible mob level;
- maximum eligible mob level;
- Team 1 maximum level;
- Team 3 maximum level;
- pre-join delay.

Underneath, the existing Rally backend still handles:

- visual row/reference matching;
- same-row target selection;
- level OCR;
- Team 1 / Team 3 availability;
- team selection;
- transition guards;
- retries and recovery;
- target-window and input safety.

The Rally implementation is mature behavior and should not be broadly rewritten merely because it is hidden behind a simpler UI.

## Resource gathering

The Bot Gather page currently configures the proven **Gold** gathering workflow.

User-facing controls include:

- starting resource level;
- number of successful gathering marches to send;
- busy-march replacement order.

The backend behavior includes:

```text
start at configured level
        ↓
search
        ↓
not found → lower one level → search again
        ↓
continue until found
        ↓
Gather
        ↓
free march available → game auto-selects it → Dispatch
        │
        └─ all busy → explicitly replace configured march → Dispatch
        ↓
verify success
        ↓
repeat until configured successful-dispatch count
```

The default busy-march replacement priority is:

```text
3 → 2 → 1
```

If a resource is taken before dispatch completes, the workflow uses the observed Cancel/retry path without consuming a successful-dispatch count or advancing the replacement pointer.

See `docs/AUTO_GATHER.md` for the behavior contract.

## Position workflows

Bundled supported workflows include:

- `scenarios/Apply Development Position.json`
- `scenarios/Apply Science Position.json`

The Bot interface exposes them as simple feature toggles/run buttons instead of requiring users to open their internal steps.

## Passive Icon Alerts

Icon Alerts are a separate passive-monitoring subsystem.

The watcher continuously scans configured screen regions for enabled image/text templates and can notify with sound and popup alerts without running macro actions.

Typical flow:

```text
capture region
    ↓
find configured template
    ↓
confirm when required
    ↓
apply appearance/cooldown policy
    ↓
sound / popup
```

The simple Bot Alerts page controls common alert behavior. The detailed **Alert Setup** tool remains available for template capture, thresholds, regions, and other development-level settings.

Passive alerts can continue running while one active automation feature owns input.

## Shared detection foundation

Automation and Icon Alerts both use `macro_clicker/detection_core.py` for reusable perception.

Shared capabilities include:

- DPI-aware BGR screen capture;
- physical-monitor handling;
- target-window and monitor-relative regions;
- exact X/Y resolution scaling;
- multi-scale template preparation;
- static and rotated template variants;
- colored-text isolation;
- grayscale matching;
- low-variance safety checks;
- match scoring and duplicate suppression;
- cancellation-aware matching.

Workflow-specific policy remains outside this shared detection layer.

## Window targeting and input safety

When a target window is configured:

- window-relative regions follow the target window;
- detection follows its physical monitor where appropriate;
- missing target-window geometry fails closed;
- clicks are rejected outside the configured target window;
- click/key actions require the selected target window to be foreground;
- geometry is rechecked near input dispatch so stale coordinates are not used after a window move/resize;
- the application does not automatically raise/focus the target window.

`pyautogui.FAILSAFE` remains enabled, and scenario kill switches are checked throughout waits, capture, matching, OCR, and actions.

## Advanced automation model

The backend model remains:

```text
Scenario
  → ordered Steps
      → Conditions
      → Actions
```

Steps may enable/disable other steps to implement state machines and recovery flows.

Supported action types include specialized actions such as:

- `click`
- `click_matching_row`
- `select_rally_team`
- `gather_control`
- `key`
- `wait`
- `set_step`
- `stop`

These details are primarily relevant to **Advanced** development rather than normal bot usage.

## Diagnostics

Automation scenarios can collect bounded diagnostic evidence under:

```text
%LOCALAPPDATA%\Macro Clicker and Icon Watcher\logs\diagnostics
```

The collector is selective so long-running automation does not save a screenshot for every poll.

Evidence can include:

- annotated context screenshots;
- OCR crops/text/confidence;
- template scores;
- row/target geometry;
- configured decision limits;
- final decision metadata.

Runtime decision metadata is also written to a bounded rotating JSONL log.

The normal Bot **Logs** page mirrors runtime activity while the existing detailed diagnostic system remains available for investigation.

## Project layout

```text
macro_clicker/bot/   Normal-user bot config, controller, adapters, and UI
macro_clicker/       Existing engine, detection, OCR, alerts, safety, and Advanced UI
tools/               Developer validation utilities
tests/               Automated regression/safety/workflow tests
templates/           Automation visual assets and OCR references
scenarios/           Bundled active-automation workflows
alerts/              Passive alert settings and templates
docs/                Architecture, bot UI, testing, and maintenance guides
launcher.pyw         Windows GUI entry point
```

Generated caches, logs, bot runtime configuration, and diagnostic captures do not belong in the repository.

## Development checks

Install development dependencies:

```powershell
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
```

Run the main checks:

```powershell
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\python -m ruff check .
.\.venv\Scripts\python -m ruff format --check .
.\.venv\Scripts\python -m mypy macro_clicker tools
.\.venv\Scripts\python -m tools.validate_scenarios
```

Current CI policy treats pytest, Ruff lint, and scenario/template validation as blocking. Ruff formatting and mypy remain informational maintenance feedback.

## Development guidance

The normal development direction is now:

```text
user-facing choice
      ↓
BotConfig
      ↓
feature adapter/controller
      ↓
existing specialized backend
```

When adding a new normal-user setting, prefer updating BotConfig + the relevant adapter + Bot UI + focused tests instead of exposing internal Steps directly.

When adding a new automation feature, first make its isolated backend reliable, then add the simple Bot-facing configuration/control layer.

Existing tuned timing, matching, OCR, recovery, and safety behavior should be preserved unless a change is specifically required and test-backed.