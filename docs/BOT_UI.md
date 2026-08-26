# Dedicated Bot UI architecture

The primary product experience is a **dedicated automation bot**, not a Scenario/Step editor.

Normal users configure what they want the bot to do. Template regions, OCR crops, condition indices, `set_step` wiring, and recovery internals remain implementation details behind **Advanced**.

## Product layers

```text
Normal user
    ↓
Bot UI
    ↓
BotConfig
    ├───────────────┬────────────────────┐
    ↓               ↓                    ↓
Feature adapters  BotController   Team-state services
    ↓               ↓                    ↓
runtime Scenario  finite/Rally      Continuous Gather
    └───────────────┴──────────────┬─────┘
                                   ↓
                              MacroEngine
                                   ↓
                         Detection / OCR / safety
```

Passive Icon Alerts remain separate observers and may run beside one active clicking automation.

## Normal-user pages

- **Dashboard** — overall state, current/next task, Rally/Gather/Positions/Alerts/Schedule summaries, and Team 1/2/3 live state.
- **Rally** — enable Rally and configure supported mob/team level limits plus join delay.
- **Gather** — enable continuous Auto Gather, choose Gold start level, and choose which Team 1/2/3 may gather.
- **Positions** — enable/run Development and Science workflows.
- **Alerts** — common passive-alert controls.
- **Schedule** — saved Bot start/stop times and weekdays.
- **Logs** — runtime activity.
- **Settings** — target window and access to Advanced tools.

Do not expose legacy Gather `march_count` or `replacement_order` as normal controls. They remain backward-compatible backend fields only.

## Advanced isolation

The legacy App remains available as **Advanced** and **Alert Setup**, but normal Bot mode must keep hidden legacy Scenario start hotkeys and legacy auto-start behavior inactive.

Normal users should not need Advanced for ordinary Rally levels, Gather team selection, schedules, or common alerts.

## BotConfig

`macro_clicker/bot/config.py` owns validated user-facing settings persisted under per-user runtime storage.

Important Gather settings now include:

- `enabled`;
- `resource` (currently Gold);
- `start_level`;
- `teams_enabled`.

Legacy `march_count` and `replacement_order` remain valid for compatibility with older configs/Advanced behavior, but continuous normal Bot gathering does not use them as a team priority or permission to replace a busy march.

UI collection remains transactional: invalid edits must not partially mutate the last known-good live config.

## Feature adapters

`macro_clicker/bot/adapters.py` deep-copies proven scenarios and applies supported settings.

For continuous Gather, selected-team runtime mode:

1. reuses the existing Gold search/resource/taken-warning scenario;
2. changes one engine run into exactly one intended team-dispatch attempt;
3. requires the chosen team's idle indicator on the dispatch panel;
4. explicitly clicks that exact team card before Dispatch;
5. adds a stale-team guard that exits if that team is no longer idle;
6. changes the no-free-march branch to close/stop instead of replacing another march.

Stored scenario JSON is not rewritten.

## BotController vs ContinuousGatherService

`BotController` serializes finite Position jobs and continuous Rally input ownership.

Continuous Auto Gather is **not** a finite queued stage. It is a persistent service driven by `TeamStateTracker` and `TeamStatusMonitor`.

Current rules:

- Development/Science run as finite setup jobs.
- Rally is continuous and remains controller-owned.
- Auto Gather may monitor team state while finite Position work runs, but it waits while input is busy.
- Auto Gather starts one exact-team MacroEngine attempt only when a fresh visual Idle observation is available.
- Rally and continuous Auto Gather are currently not allowed to run together because safe cooperative preemption has not yet been implemented.
- Alerts may run beside either because they are passive.

Do not fake concurrency by letting separate engines compete for clicks.

## Gather UI/status contract

The Gather page should communicate actual team-state behavior, for example:

```text
Auto Gather                 ON
Resource                    Gold
Starting level              12
Teams                       [✓] 1  [✓] 2  [✓] 3

Team 1   Gathering   04:33:18
Team 2   Returning   00:00:08
Team 3   Idle
```

If Team 3 is the only fresh Idle team, the next dispatch must target Team 3 specifically.

When all configured teams are busy, the UI should say it is waiting rather than implying a replacement will occur.

Visible timers are presentation/scheduling hints. UI countdown reaching zero must not imply the team is available until fresh visual state says `Idle`.

## Dashboard contract

Dashboard status is read-only. It may display existing tracker/service/engine state but must not drive automation.

Useful summaries include:

```text
Current task: Auto Gather
Gather: Waiting — all configured teams busy — next timer: Team 2 00:00:08
Team 1: Gathering — 04:33:18
Team 2: Returning — 00:00:08
Team 3: Travelling — 00:00:17
```

or during an attempt:

```text
Gather: Running — Team 3 — selecting team and dispatching
```

## Schedule semantics

Bot scheduling uses the last validated/saved BotConfig. A scheduled start must not silently activate half-typed UI edits.

Manual Start Bot still validates/saves current controls first.

## Adding a normal-user setting

1. Add a validated BotConfig field with a safe default.
2. Add the simple control.
3. Read/write it through `ui_runtime.py`.
4. Translate it through the correct adapter/service boundary.
5. Add focused tests.
6. Update all affected living Markdown docs.
7. Use a detailed AI-oriented commit subject/body.

## Preservation rules

Protect:

- mature Rally matching/OCR/team/recovery behavior;
- target-window/foreground input safety;
- Gather search-until-found and resource-taken recovery;
- exact-team verification before continuous Gather Dispatch;
- busy-team protection;
- timer-not-authority semantics;
- fail-closed stale/unknown state behavior;
- passive alert isolation;
- Advanced tooling availability without making it part of normal use.

See `AGENTS.md` for mandatory commit and documentation-sync requirements.