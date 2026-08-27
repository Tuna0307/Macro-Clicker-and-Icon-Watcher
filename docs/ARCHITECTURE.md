# Architecture and maintenance guide

The project is a specialized Windows visual-automation bot and passive screen-monitoring application. The mature Scenario engine still powers active automation; the normal product interface is the dedicated Bot UI.

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
                         Detection / OCR / safe input
```

Passive Icon Alerts reuse detection but remain a separate observer runtime.

## Product/control modules

- `bot/config.py` — validated/persisted normal-user settings.
- `bot/adapters.py` — runtime Scenario copies, including exact-team Gather verification/clicking.
- `bot/controller.py` — finite jobs and Rally input ownership.
- `bot/team_state.py` — thread-safe Team 1/2/3 state/freshness model.
- `bot/team_status.py` — read-only trusted-world-map march availability detector.
- `bot/continuous_gather.py` — persistent availability-driven Gather coordinator.
- `bot/status.py` — read-only Dashboard summaries.
- `bot/ui*.py` — normal-user presentation/runtime glue.

## Input ownership

Only one active clicking automation may own mouse/keyboard input at a time.

- finite Position jobs are controller-owned;
- Rally is continuous and controller-owned;
- continuous Auto Gather is persistent but starts an engine attempt only when input is free;
- Rally + continuous Gather are currently blocked together;
- passive Alerts may run alongside active automation.

## Continuous Auto Gather perception

Normal Bot Auto Gather is state-driven rather than a finite “send N marches” job.

```text
trusted world-map anchor (RallyIcon)
        ↓
busy-count indicator / compressed busy queue
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
 wait      selected-team Gather attempt
              ↓
   dispatch-panel blue-idle recheck
              ↓
       exact team card click
              ↓
           Dispatch
```

### Why blank status can mean Idle

The game’s left deployment queue contains busy marches only. On the confirmed world map:

- no busy count/status row = 0/3 busy = all three teams are Idle candidates;
- 1/3, 2/3, 3/3 identify how many teams are busy;
- Team 1 (Murphy) and Team 3 (Stetmann) are identified with the existing Rally busy-portrait templates;
- Team 2 (Carlie) is inferred by elimination from the count.

This detector reuses committed assets already exercised by Rally. It does not depend on the nonexistent `TeamStatusSidebarHeader.png` or uncommitted Team-status label/portrait templates.

A blank area on an untrusted screen remains unusable: if the Rally-icon world-map anchor is absent, the tracker marks the observation surface unavailable and continuous Gather cannot start from it.

### Current map-side state resolution

The current detector emits:

- `IDLE`
- `BUSY`
- `UNKNOWN`

The tracker still supports `TRAVELLING`, `GATHERING`, and `RETURNING` for future richer observations. Detailed state/timer recognition is intentionally deferred until real committed fixtures/templates exist; it must not block safe availability detection.

Contradictory busy-count and portrait evidence fails closed to `UNKNOWN`.

## Dispatch safety

Map-side availability only authorizes starting a candidate attempt. Before the actual Dispatch action, `bot/adapters.py` still:

1. requires the normal Dispatch button;
2. requires the exact selected team’s blue idle indicator;
3. clicks that exact team card;
4. exits if the selected team is no longer idle;
5. does not replace an occupied march on no-free-march.

This second check is intentionally independent from the map-side detector.

## Rally workflow

Rally remains protected mature behavior spanning `engine.py`, `rally_matching.py`, OCR, scenario JSON, templates, and focused tests. Preserve same-row association, OCR retry, Team 1/3 availability/selection, transition guards, recovery, and target-window safety.

## Safety invariants

Preserve:

- target-window/foreground checks;
- out-of-window/monitor rejection;
- geometry refresh near input;
- `pyautogui.FAILSAFE`;
- kill-switch/stop responsiveness;
- untrusted/contradictory visual state remains unknown;
- stale team observations cannot authorize Gather;
- local countdown reaching zero cannot authorize Gather;
- selected-team mismatch cannot silently dispatch another team.

## Persistence boundaries

Project implementation/assets live under `scenarios/`, `templates/`, `alerts/`, and source code. Per-user writable state includes `bot_config.json`, logs/diagnostics, locks, and UI preferences.

## AI-assisted development workflow

Read `AGENTS.md`, identify preserved behavior, make the smallest coherent change, add focused tests, update every affected living Markdown file, check CI, record live verification still required, and commit with a descriptive AI-oriented subject/body.
