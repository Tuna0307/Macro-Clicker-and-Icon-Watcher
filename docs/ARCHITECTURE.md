# Architecture and maintenance guide

The project is a specialized Windows visual-automation bot and passive screen-monitoring application.

The mature Scenario engine still powers active automation, but the normal product interface is the dedicated Bot UI.

## Top-level architecture

```text
                         Normal user
                             ↓
                           Bot UI
                             ↓
                          BotConfig
             ┌───────────────┼────────────────┐
             ↓               ↓                ↓
      Feature adapters   BotController   Team-state services
             ↓               ↓                ↓
      runtime Scenarios   finite/Rally    Continuous Gather
             └───────────────┴───────────┬────┘
                                         ↓
                                    MacroEngine
                                         ↓
                            Scenario -> Steps -> Actions
                                         ↓
                         Detection / OCR / safe input

Passive Icon Alerts reuse detection but remain a separate observer runtime.
```

The governing rule is:

> **Bot UI controls what the user wants; specialized backend code decides how to do it safely.**

## Product/control modules

### `bot_app.py`
Primary shell. Shows the dedicated Bot interface while preserving hidden Advanced and Alert Setup tooling.

### `macro_clicker/bot/config.py`
Validated/persisted normal-user settings. Project-owned Scenario JSON is not rewritten by normal Bot saves.

### `macro_clicker/bot/adapters.py`
Deep-copies proven Scenarios and applies supported user-facing values. Continuous Gather selected-team mode adds runtime-only exact-team verification/clicking and fail-closed no-free-march behavior.

### `macro_clicker/bot/controller.py`
Serializes controller-owned clicking work. Development and Science are finite; Rally is continuous. Continuous Auto Gather is deliberately not a finite queued stage.

### `macro_clicker/bot/team_state.py`
Thread-safe Team 1/2/3 state model. Stores activity, countdown hints, freshness, and whether a visual refresh is needed.

### `macro_clicker/bot/team_status.py`
Read-only world-map expedition-sidebar detector/monitor. Identifies Team 1/2/3 and classifies Travelling/Gathering/Returning/Busy/Idle candidates. Timer OCR is lazy and advisory.

### `macro_clicker/bot/continuous_gather.py`
Persistent Gather coordinator. Chooses a configured team only from fresh visual Idle state, starts one exact-team Gather attempt, and waits when all configured teams are busy.

### `macro_clicker/bot/status.py`
Read-only Dashboard summaries for active tasks and team state.

### `macro_clicker/bot/ui*.py`
Normal-user presentation/runtime glue.

## Module ownership

| Module | Responsibility |
| --- | --- |
| `bot/config.py` | user-facing settings/validation/persistence |
| `bot/adapters.py` | runtime Scenario adaptation including exact-team Gather attempt |
| `bot/controller.py` | finite/Rally input-owner sequencing |
| `bot/team_state.py` | shared team-state model/countdown hints |
| `bot/team_status.py` | read-only visual team-state observation |
| `bot/continuous_gather.py` | persistent availability-driven Gather coordination |
| `bot/status.py` | read-only Dashboard state |
| `engine.py` | active automation polling/actions/state transitions/safe input |
| `rally_matching.py` | Rally row/OCR/team logic |
| `resource_gathering.py` | legacy/scenario Gather success/replacement state compatibility |
| `detection_core.py` | shared perception primitives |
| `alert_watcher.py` | passive alert runtime/UI/policy |
| `models.py` | Scenario/Step/Condition/Action models/validation |

## Input ownership

Only one active clicking automation may own mouse/keyboard input at a time.

Current rules:

- finite Position jobs are controller-owned;
- Rally is continuous and controller-owned;
- continuous Auto Gather is a persistent service but starts an engine attempt only when input is free;
- Auto Gather may observe team state while finite tasks run;
- Rally + continuous Gather are currently blocked together because safe cooperative preemption has not been designed;
- passive Alerts may run alongside active automation because they do not click.

Do not let independent MacroEngines compete for input.

## Rally workflow

Rally remains protected mature behavior spanning `engine.py`, `rally_matching.py`, OCR, scenario JSON, templates, and focused tests.

Preserve same-row association, atomic snapshots, OCR retry, Team 1/3 availability/selection, transition guards, recovery, and target-window safety.

## Continuous Auto Gather workflow

Normal Bot Auto Gather is state-driven rather than a finite "send N marches" job.

```text
visual expedition sidebar
        ↓
TeamStatusDetector
        ↓
TeamStateTracker
        ↓
ContinuousGatherService
        ↓
fresh configured Idle team?
   ┌────┴────┐
   No       Yes
   │          │
 wait      start one selected-team Gather runtime Scenario
              ↓
      search Gold until found
              ↓
   re-verify selected team idle
              ↓
      click exact team card
              ↓
           Dispatch
```

Team activities:

- `IDLE`
- `TRAVELLING`
- `GATHERING`
- `RETURNING`
- `BUSY`
- `UNKNOWN`

Important invariants:

- existing busy state before Bot startup is respected;
- no user-facing fixed team priority;
- all-busy means wait, not replace;
- stale/hidden sidebar state cannot authorize a dispatch;
- timer expiry requests a refresh but cannot change a team to Idle;
- exact selected team is re-verified on the dispatch panel;
- no-free-march stops/closes rather than replacing a busy march;
- confirmed dispatch marks that exact team non-idle until visuals catch up;
- unconfirmed attempt pauses fail-closed.

The existing `scenarios/Gather Gold.json` still supplies the proven search/resource/resource-taken state machine for one attempt. Legacy `resource_gathering.py` state remains for backward compatibility/Advanced use.

See `docs/AUTO_GATHER.md`.

## Passive Icon Alerts

Alerts remain passive observers and may run beside one active input owner. Keep passive notification policy out of active macro decision code unless explicitly requested.

## Shared perception

Reusable capture/scaling/matching belongs in `detection_core.py`. Workflow policy stays in specialized active modules or the Bot control/service layer.

## Safety invariants

Preserve:

- target-window/foreground checks;
- out-of-window/monitor rejection;
- geometry refresh near input;
- `pyautogui.FAILSAFE`;
- kill-switch/stop responsiveness;
- unreadable OCR/visual state remains unknown;
- stale team-state observations cannot authorize Gather;
- local countdown reaching zero cannot authorize Gather;
- selected-team mismatch cannot silently dispatch another team.

## Persistence boundaries

Project-owned implementation/assets:

- `scenarios/`
- `templates/`
- `alerts/settings.json`
- `alerts/templates/`

Per-user writable runtime state:

- `bot_config.json`
- logs/diagnostics
- locks/UI preferences

Normal Bot settings modify runtime copies, not stored Scenarios.

## AI-assisted development workflow

Before changing code:

1. read `AGENTS.md` and affected living docs;
2. identify ownership and preserved behavior;
3. make the smallest coherent change;
4. add/update regression tests;
5. update every affected living Markdown file;
6. run/check CI and record live verification still required;
7. commit with a detailed subject/body explaining what, why, runtime impact, preserved invariants, tests, and remaining work.

Dated plan/spec documents are historical records and should not be silently rewritten to erase earlier design context.