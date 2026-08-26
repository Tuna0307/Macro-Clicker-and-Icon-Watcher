# PC Automation Bot

A Windows desktop utility for **visual automation** and **passive screen monitoring**.

The normal interface is a dedicated automation-bot surface. Users configure what they want the bot to do through simple feature pages instead of editing low-level Steps, Conditions, Actions, image regions, or OCR details.

The proven Scenario engine remains underneath, and the original editor is still available through **Advanced** for development/debugging.

## Main interface

The Bot interface contains:

- **Dashboard** — bot status, current task, passive-alert status, live team state, Start/Stop, and quick actions.
- **Rally** — configure supported mob/team level limits and join delay.
- **Gather** — configure Gold gathering start level and which Team 1/2/3 may be used.
- **Positions** — enable or run Development and Science workflows.
- **Alerts** — start/stop passive image alerts and common alert groups.
- **Schedule** — bot start/stop times and active weekdays.
- **Logs** — runtime activity.
- **Settings** — target-window settings and Advanced access.

Low-level internals remain available through:

- **Advanced** — Scenario / Step / Condition / Action editor and diagnostics.
- **Alert Setup** — detailed passive-alert template configuration.

The hidden Advanced start hotkey/legacy scheduler do not remain active during normal Bot use.

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

`Run PC Automation Bot.vbs` provides the same no-console launch. Older Macro Builder launchers remain for compatibility.

Runtime logs are stored under the per-user data directory, normally:

```text
%LOCALAPPDATA%\Macro Clicker and Icon Watcher\logs
```

Set `MACRO_CLICKER_DATA_DIR` to override writable runtime storage.

## Architecture overview

```text
                          Bot UI
                            │
                        BotConfig
                            │
          ┌─────────────────┼─────────────────┐
          ▼                 ▼                 ▼
   Feature adapters    BotController    TeamState services
          │                 │                 │
          │                 │          Continuous Gather
          └──────────┬──────┴───────────────┬─┘
                     ▼                      ▼
                MacroEngine          read-only monitoring
                     │
             Scenarios / Steps
                     │
          Detection / OCR / Safety
                     │
          ┌──────────┴──────────┐
          ▼                     ▼
   Active automation       Passive alerts
```

The Bot UI is a control layer, not a replacement for the working automation engine.

For development details, read:

- `AGENTS.md`
- `docs/BOT_UI.md`
- `docs/ARCHITECTURE.md`
- `docs/MAINTAINABILITY.md`
- `docs/TESTING.md`
- `docs/AUTO_GATHER.md`
- `docs/BOT_ROADMAP.md`

## Bot configuration

Normal-user settings are stored separately from bundled Scenario JSON:

```text
%LOCALAPPDATA%\Macro Clicker and Icon Watcher\bot_config.json
```

Saving normal Bot settings does **not** rewrite tuned files under `scenarios/`. Runtime adapters clone the proven scenario and apply supported values in memory.

## Input ownership

Only one active clicking automation may own the target application at a time. Passive Alerts may run alongside it because they observe rather than click.

Current coordination rules:

- Development and Science are finite tasks.
- Rally is continuous.
- Continuous Auto Gather is a persistent service driven by Team 1/2/3 visual state, not a finite queued 3-dispatch job.
- Auto Gather waits while another controller/engine task owns input.
- Continuous Rally and continuous Auto Gather are currently blocked from running together until a safe cooperative handoff/preemption design exists.

Do not run independent active automations concurrently and let them compete for mouse/keyboard input.

## Rally configuration

The Rally page exposes normal-user values such as:

- minimum eligible mob level;
- maximum eligible mob level;
- Team 1 maximum level;
- Team 3 maximum level;
- pre-join delay.

The existing backend still owns row matching, OCR, team availability/selection, transition guards, retries, and safe input.

## Continuous Auto Gather

The normal Bot Gather feature is continuous and state-driven.

User-facing controls include:

- enable/disable Auto Gather;
- resource (currently Gold);
- starting resource level;
- which of Team 1/2/3 may gather.

The normal flow is:

```text
read Team 1/2/3 visual state
        ↓
configured team visually Idle?
   ┌────┴────┐
   No       Yes
   │          │
 wait      search Gold
              ↓
      lower level until found
              ↓
        open dispatch panel
              ↓
 re-verify exact selected team is idle
              ↓
      click that exact team card
              ↓
           Dispatch
              ↓
      watch team state again
```

Important behavior:

- there is no normal-user `3 -> 2 -> 1` replacement priority;
- the bot does not intentionally overwrite travelling/gathering/returning/busy teams;
- if all configured teams are busy, it waits;
- existing team activity present before bot startup is respected;
- visible countdowns are scheduling hints only;
- a timer reaching zero never makes a team Idle without fresh visual confirmation;
- before Dispatch, the exact requested team is re-verified and explicitly clicked;
- if that team became busy, or the game reports no free march, the attempt closes/stops instead of allowing another team to be auto-selected/replaced;
- resource-taken Cancel/retry remains part of the proven Gather backend;
- an unconfirmed attempt pauses Auto Gather fail-closed rather than retrying blindly.

See `docs/AUTO_GATHER.md` for the authoritative contract.

Legacy `march_count` and `replacement_order` fields remain for backward compatibility with older configs/Advanced scenario behavior, but they are not the normal continuous Bot policy.

## Team-state tracking

The shared team tracker models:

```text
Idle
Travelling
Gathering
Returning
Busy
Unknown
```

The world-map expedition sidebar is the visual source of truth. Countdown OCR is used only to choose efficient recheck timing.

Example:

```text
Team 1  Gathering  04:33:18
Team 2  Returning   00:00:08
Team 3  Travelling  00:00:17
```

The bot waits. When a team is later **visually confirmed Idle**, that team may be sent.

## Position workflows

Supported bundled workflows include:

- `scenarios/Apply Development Position.json`
- `scenarios/Apply Science Position.json`

The Bot exposes normal enable/run controls and keeps internal click/retry steps hidden.

## Passive Icon Alerts

Icon Alerts are a separate passive-monitoring subsystem. They scan configured regions/templates and notify with sound/popup without executing active macro actions.

Passive alerts may continue while one active clicking automation owns input.

## Shared detection and safety

Automation and Alerts reuse `macro_clicker/detection_core.py` for capture, scaling, template preparation/matching, colored-text/grayscale handling, monitor/window-relative geometry, and cancellation-aware perception.

Active input preserves:

- target-window geometry checks;
- foreground-window requirement;
- out-of-window/monitor-bound rejection;
- live geometry recheck near dispatch;
- `pyautogui.FAILSAFE`;
- scenario kill-switch/stop handling;
- unreadable OCR/visual states treated as unknown rather than guessed.

## Project layout

```text
macro_clicker/bot/   Bot config, adapters, controller, team-state services, UI/status
macro_clicker/       Existing engine, Rally logic, detection, OCR, alerts, safety, Advanced UI
tests/               Regression/safety/workflow tests
templates/           Visual assets
scenarios/           Bundled active-automation workflows
alerts/              Passive alert settings/templates
docs/                Living architecture, UI, testing, roadmap, and behavior guides
```

## Development checks

```powershell
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
python -m mypy macro_clicker tools
python -m tools.validate_scenarios
```

Blocking CI checks are pytest, Ruff lint, and scenario/template validation. Ruff formatting and mypy are informational.

## AI-assisted development rule

This repository is developed heavily with AI. Before changing behavior, read `AGENTS.md` and the relevant docs.

Every meaningful commit must include a descriptive subject/body explaining:

- what changed;
- why it changed;
- intended runtime impact;
- important behavior intentionally preserved;
- tests/checks performed;
- remaining live verification or follow-up work.

When behavior, architecture, UI, configuration, safety policy, test contracts, or roadmap status changes, update **all affected living Markdown files in the same work**. Do not leave future AI with conflicting documentation.