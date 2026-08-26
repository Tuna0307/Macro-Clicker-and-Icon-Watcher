# Dedicated Bot migration roadmap

This file tracks migration from the low-level Macro Builder experience to the dedicated Bot interface. It exists so future AI/development sessions can tell what is complete, what still needs live proof, and what should not be rewritten.

## Phase status

| Phase | Status | Current contract / remaining work |
| --- | --- | --- |
| 1. Bot configuration layer | Complete | `bot/config.py` owns normal-user settings/persistence. Gather now exposes configured teams instead of a normal-user replacement order. |
| 2. Feature adapters | Complete for current features | Runtime adapters deep-copy proven Scenarios. Gather selected-team mode re-verifies/clicks an exact requested team and fails closed if it became busy. |
| 3. Bot Controller | V1 complete | One input owner at a time. Development/Science remain finite; Rally is continuous. Continuous Gather is now a separate persistent service rather than a finite queue stage. |
| 4. Bot UI shell | Complete, polish ongoing | Dashboard/Rally/Gather/Positions/Alerts/Schedule/Logs/Settings are normal-user pages. Advanced remains secondary tooling. |
| 5. Rally tab | Core complete; live re-validation required | Normal level/cap/join-delay controls wrap mature Rally behavior. |
| 6. Gather tab | New continuous architecture implemented; live proof required | Gold/start-level/configured teams + live Team 1/2/3 state. Busy teams are protected; all-busy waits; exact team is re-verified/clicked before Dispatch. |
| 7. Alerts tab | Core complete | Common groups/sound/watcher controls are exposed. |
| 8. Positions tab | Core complete; live re-validation required | Development/Science and retry policy are normal-user controls. |
| 9. Advanced mode | Complete | Legacy editor/alert setup retained but isolated from normal Bot hotkeys/auto-start. |
| 10. Dashboard/live status | In progress, substantially advanced | Dashboard can show continuous Gather service state and Team 1/2/3 Idle/Travelling/Gathering/Returning/Busy/Unknown countdown hints. Real-game accuracy still needs proof. |
| 11. Scheduling | Basic implementation complete; live proof pending | Saved start/stop times/weekdays; scheduled starts use last saved config. |
| 12. Remove remaining hard-coded user settings | Ongoing audit | Move genuine user choices only. Legacy Gather replacement fields remain internal/backward compatible. |
| 13. Testing throughout | Ongoing | Continuous Gather regressions now cover stale/hidden state, timer-zero semantics, all-busy waiting, configured-team filtering, exact-team selection, and fail-closed failures. Full Windows CI + real-game verification remain required. |

## Current next work

1. Obtain a green Windows CI run for the latest continuous-Gather + documentation state.
2. Supervised live Windows test with mixed initial team state (for example one Idle, one Gathering, one Travelling).
3. Verify Team 1/2/3 state labels and timers against the real expedition sidebar.
4. Verify the exact intended team card is visibly clicked before Dispatch.
5. Verify all-busy state waits and never replaces an existing busy team.
6. Verify the next team that becomes visually Idle is automatically sent again.
7. Verify resource-taken Cancel/retry still works inside a selected-team attempt.
8. Verify F12/unconfirmed dispatch pauses continuous Gather rather than restarting automatically.
9. Re-test Rally, Positions, Alerts, schedule, Advanced isolation after the architecture change.
10. Design cooperative Rally/Gather handoff only if simultaneous enabled operation is required; do not fake preemption.

## Phase 10 live-status contract

Dashboard status is read-only presentation. It may inspect controller/service/tracker/engine state but must not drive automation.

Current intended example:

```text
BOT RUNNING
Current task: Auto Gather

Gather: Waiting — all configured teams busy — next timer: Team 2 00:00:08
Team 1: Gathering — 04:33:18
Team 2: Returning — 00:00:08
Team 3: Travelling — 00:00:17
Alerts: Watching — Digs + Secret Task
```

During an attempt:

```text
Gather: Running — Team 3 — selecting team and dispatching
```

The Dashboard must never infer `Idle` merely because a locally counted timer reached zero.

## Live verification checklist

Before treating continuous Auto Gather as proven:

- start the Bot while some teams are already busy;
- confirm only visually Idle configured teams are eligible;
- confirm exact-team card selection before Dispatch;
- confirm travelling/gathering/returning teams remain untouched;
- confirm all-busy means wait;
- confirm visible countdown/state transitions are represented correctly;
- confirm timer zero triggers re-observation rather than automatic Idle;
- confirm one team returning to Idle causes a later new Gather attempt for that team;
- confirm resource-taken Cancel/retry remains safe;
- confirm no-free-march does not invoke legacy busy replacement in normal Bot mode;
- confirm F12/failed attempt pauses fail-closed;
- confirm target-window/foreground input safety remains intact.

Also continue general Bot verification for Rally settings, Position buttons/retry, Alerts, Start/Stop, schedule, logs, and Advanced -> Back to Bot.

## Do not regress to old Gather product model

Normal continuous Auto Gather should not be presented as:

```text
send 3 marches
replacement order 3 -> 2 -> 1
```

Those fields/state remain only for backward compatibility with older configs/Advanced scenario behavior.

The normal product model is:

```text
observe actual team state
        ↓
find a configured visually Idle team
        ↓
search resource
        ↓
re-verify/click that exact team
        ↓
dispatch
        ↓
continue monitoring
```

See `AGENTS.md` for mandatory descriptive commit and Markdown synchronization rules.