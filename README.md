# PC Automation Bot

A Windows desktop utility for **visual automation** and **passive screen monitoring**.

The normal interface is a dedicated automation-bot surface. Users configure what they want the bot to do through simple feature pages instead of editing low-level Steps, Conditions, Actions, image regions, or OCR details. The proven Scenario engine remains underneath, and the original editor is still available through **Advanced**.

## Main interface

The Bot interface contains Dashboard, Rally, Gather, Positions, Alerts, Schedule, Logs, and Settings pages. Low-level Scenario editing remains behind **Advanced** and detailed alert tuning behind **Alert Setup**.

For normal Windows use, double-click:

```text
Run PC Automation Bot.bat
```

Runtime data normally lives under:

```text
%LOCALAPPDATA%\Macro Clicker and Icon Watcher
```

## Architecture overview

```text
                          Bot UI
                            │
                        BotConfig
                            │
          ┌─────────────────┼─────────────────┐
          ▼                 ▼                 ▼
   Feature adapters    BotController    Team-state services
          │                 │                 │
          │                 │          Continuous Gather
          └──────────┬──────┴───────────────┬─┘
                     ▼                      ▼
                MacroEngine          read-only monitoring
                     │
             Scenarios / Steps
                     │
          Detection / OCR / Safety
```

The Bot UI is a control layer, not a replacement for the working automation engine.

For development details, read `AGENTS.md` and the living documents under `docs/`.

## Bot configuration

Normal-user settings are stored separately from bundled Scenario JSON in `bot_config.json`. Saving normal Bot settings does **not** rewrite tuned files under `scenarios/`; runtime adapters clone the proven scenario and apply supported values in memory.

## Input ownership

Only one active clicking automation may own the target application at a time.

- Development and Science are finite tasks.
- Rally is continuous.
- Continuous Auto Gather is persistent and driven by actual Team 1/2/3 state.
- Auto Gather waits while another controller/engine task owns input.
- Continuous Rally and continuous Auto Gather are currently blocked from running together until a safe cooperative handoff exists.
- Passive Alerts may observe alongside the active input owner.

## Continuous Auto Gather

User-facing controls include:

- enable/disable Auto Gather;
- resource (currently Gold);
- starting resource level;
- which of Team 1/2/3 may gather.

The normal flow is:

```text
confirm trusted world-map view
        ↓
read busy-march count/identities
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

### Important map-side behavior

The game’s left march-status/deployment queue shows **busy marches only**. This means a blank status list on the confirmed world map is not “status unavailable”; it is the real **0/3 busy** state, so Team 1/2/3 are Idle candidates.

To avoid treating an unrelated blank screen as all-free, the monitor first verifies the world map with the existing Rally icon template. It then reuses the already-proven 1/3, 2/3, 3/3 squad-count assets and Team 1/Team 3 busy portraits. Team 2 is inferred from the count.

The broken prototype dependency on `templates/TeamStatusSidebarHeader.png` has been removed; that file never existed in the repository.

Current map-side monitoring intentionally reports reliable `Idle`, `Busy`, or `Unknown`. The shared tracker can still model `Travelling`, `Gathering`, and `Returning` when future real visual evidence is added.

Important safety behavior remains:

- there is no normal-user `3 -> 2 -> 1` replacement priority;
- busy teams are not intentionally overwritten;
- contradictory map evidence becomes `Unknown`;
- stale/untrusted map observations cannot dispatch;
- before Dispatch, the exact requested team is re-verified via its blue idle indicator and explicitly clicked;
- if that team became busy, or the game reports no free march, the attempt closes/stops;
- resource-taken Cancel/retry remains part of the proven Gather backend;
- an unconfirmed attempt pauses Auto Gather fail-closed.

Legacy `march_count` and `replacement_order` remain for backward compatibility only.

## Team-state tracking

The tracker supports:

```text
Idle
Travelling
Gathering
Returning
Busy
Unknown
```

For the current live map detector, `Idle/Busy/Unknown` are the authoritative observed states. A timer or predicted completion time never makes a team free by itself.

## Rally, Positions, and Alerts

Rally remains mature backend behavior with normal-user level/team-cap controls. Development/Science remain finite Position workflows. Icon Alerts remain passive observers.

## Shared detection and safety

Active input preserves target-window geometry checks, foreground-window requirements, out-of-window rejection, live geometry rechecks, `pyautogui.FAILSAFE`, stop/kill-switch handling, and fail-closed unreadable visual state.

The Gather dispatch-panel exact-team idle check remains the final safety gate before Dispatch.

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

Every meaningful commit must include a descriptive subject/body explaining what changed, why, runtime impact, preserved safety/compatibility, tests/checks, and remaining live verification.

When behavior, architecture, UI, configuration, safety policy, test contracts, or roadmap status changes, update **all affected living Markdown files in the same work**.
