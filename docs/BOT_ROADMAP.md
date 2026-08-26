# Dedicated Bot migration roadmap

This file tracks the product migration from the low-level Macro Builder experience
to the dedicated Bot interface.  It preserves the original phased plan so future
AI/development sessions can tell what is complete, what still needs live proof,
and what should not be rewritten.

The governing rule remains:

> Change what the normal user sees and configures while preserving proven
> Rally, Gather, detection, OCR, alert, and input-safety behavior underneath.

## Phase status

| Phase | Status | Current contract / remaining work |
| --- | --- | --- |
| 1. Bot configuration layer | Complete | `macro_clicker/bot/config.py` owns normal-user settings, validation, tolerant loading, and `%LOCALAPPDATA%/.../bot_config.json` persistence. Project Scenario JSON is not rewritten by normal Bot saves. |
| 2. Feature adapters | Complete for current features | `macro_clicker/bot/adapters.py` deep-copies bundled scenarios and translates Rally/Gather/Position settings into runtime copies. Do not reimplement proven backends merely to make separate adapter files. |
| 3. Bot Controller | V1 complete | One clicking automation owns input at a time. Full Bot cycles serialize Development -> Science -> Gather -> Rally. Passive Alerts may run beside one active MacroEngine. Start failures stop the queue rather than silently skipping ahead. |
| 4. Bot UI shell | Complete, polish ongoing | Normal startup opens the dedicated Bot surface with Dashboard, Rally, Gather, Positions, Alerts, Schedule, Logs, and Settings. Advanced tools are explicit secondary surfaces. |
| 5. Rally tab | Core complete; live re-validation required | Normal controls expose eligible min/max levels, Team 1/3 caps, enable state, and join delay. Existing row matching, OCR, availability, team selection, recovery, and transition guards remain backend-owned. |
| 6. Gather tab | Core complete; live Bot-layer re-validation required | Gold, start level, march count, and busy replacement order are configurable. Search-until-found, resource-taken retry, free-march behavior, and replacement-state handling remain backend-owned. |
| 7. Alerts tab | Core complete | Common alert groups, sound/volume, watcher Start/Stop, and Advanced Alert Setup are exposed. Add more normal-user alert settings only when an existing backend option clearly warrants it. |
| 8. Positions tab | Core complete; live Bot-layer re-validation required | Development and Science can be enabled for a Bot cycle or run directly. Scenario internals remain hidden. |
| 9. Advanced mode | Complete | The original Scenario/Step/Condition/Action and detailed alert tools remain available, but hidden Scenario hotkeys and legacy auto-start are inactive while normal Bot mode is in use. |
| 10. Dashboard/live status | In progress | Phase 10 now has a read-only status model and live cards for current/next task, Rally state, Gather success/replacement progress, Positions, Alerts, and Schedule. Real Windows presentation and live-state accuracy still need supervised verification. |
| 11. Scheduling | Basic implementation complete; live proof pending | Bot start/stop times and weekdays are saved. Scheduled starts deliberately use the last explicitly saved configuration rather than half-edited UI values. A real scheduled start/stop run is still required. |
| 12. Remove remaining hard-coded user settings | Ongoing audit | Move only genuine user choices into `BotConfig`. Keep confidence thresholds, OCR geometry, template regions, recovery timing, row tolerances, and other implementation tuning internal unless there is a concrete user need. |
| 13. Testing throughout | Ongoing | Existing Rally/Gather/Alert tests remain mandatory. New BotConfig, adapters, controller, scheduling/isolation, and Dashboard status behavior have focused tests. Full Windows CI must be green before a migration milestone is called complete, followed by supervised real-game checks for behavior GitHub Actions cannot exercise. |

## Current next work

1. Restore fully green Windows CI after the Bot entrypoint/test migration.
2. Supervised real-Windows verification of Run Rally, Run Gather, Position buttons,
   Start Bot/Stop Bot queue ownership, Alerts beside active automation, and
   Advanced -> Back to Bot.
3. Verify the Phase 10 Dashboard against real engine activity, especially
   Gather `0/3 -> 3/3`, busy replacement pointer changes, and Rally Team/level
   summaries.
4. Perform a real scheduled start and scheduled stop test.
5. Audit remaining normal-user settings and expose only values that users should
   reasonably change.
6. Final UI polish after functionality and status behavior are proven.

## Phase 10 live-status contract

Dashboard status is a read-only presentation layer. It may inspect existing
controller/engine state, but it must not drive automation.

Current intended summaries include:

```text
BOT RUNNING
Current task: Auto Gather
Next task: Gold Mob Rally

Gather: 2/3 successful — dispatching march — next busy replacement: March 2
Rally: Enabled — eligible Lv1-70
Alerts: Watching — Digs + Secret Task
Schedule: Active — 06:00-23:00 — Mon Tue Wed Thu Fri Sat Sun
```

For Rally, the Dashboard may report an already-existing pending mob level/team
selection. For Gather, it may report `GatherController.successful_dispatches`
and the current replacement pointer. It must not create new Rally/Gather state
or duplicate their logic.

## Live verification checklist

GitHub Actions cannot interact with the actual game window. Before treating the
migration as complete, perform supervised checks on the real Windows desktop:

- Save a changed Rally level in the Bot tab and verify the runtime scenario uses
  it while existing OCR/team-selection behavior still works.
- Run Gather for the configured march count and verify free marches, `3 -> 2 ->
  1` busy replacement, resource-taken Cancel/retry, and search-until-found.
- Run Development and Science from their normal-user buttons.
- Enable multiple finite tasks plus Rally and confirm serialized handoff.
- Stop a running Bot cycle and verify queued work is discarded.
- Keep passive Alerts active beside Rally/Gather and verify they do not take
  ownership of macro input.
- Open Advanced and return to Bot; confirm the hidden editor hotkey/schedule do
  not run while normal Bot mode is active.
- Verify Dashboard text follows the real active feature and progress.
- Verify one scheduled start and one scheduled stop using saved settings.

## Do not regress to the old product model

Normal users should configure **what they want the Bot to do**, not how template
matching or Scenario state machines are implemented.

Do not expose low-level Step/Condition/Action details on normal Bot pages just
because those fields exist internally.  Keep Advanced available for debugging,
but ordinary changes such as mob levels or gathering march count belong in
BotConfig and adapters.
