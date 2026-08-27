# Dedicated Bot UI architecture

The primary product experience is a **dedicated automation bot**, not a Scenario/Step editor. Normal users configure what they want; template regions, OCR crops, condition indices, and recovery wiring remain behind **Advanced**.

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
```

## Normal-user pages

Dashboard, Rally, Gather, Positions, Alerts, Schedule, Logs, and Settings are normal-user pages. Advanced tooling remains secondary.

Do not expose legacy Gather `march_count` or `replacement_order` as normal controls.

## Continuous Gather UI/service contract

`ContinuousGatherService` is persistent and driven by `TeamStateTracker` + `TeamStatusMonitor`.

The Gather page exposes:

- enable/disable;
- Gold start level;
- configured Team 1/2/3 subset.

The map-side status monitor uses the game’s real semantics: the left queue lists **busy marches only**. Therefore a confirmed world-map view with no busy count/status is the `0/3` state and all Team 1/2/3 are Idle candidates.

The monitor must not infer all-idle from an arbitrary blank screen. It first verifies the world map using `templates/RallyIcon.png`, then uses the committed 1/3, 2/3, 3/3 squad-count templates plus Team 1/Team 3 busy portraits. Team 2 is inferred from count.

Current map-side labels are deliberately conservative:

```text
Team 1   Idle / Busy / Unknown
Team 2   Idle / Busy / Unknown
Team 3   Idle / Busy / Unknown
```

The tracker supports richer states for future evidence, but Travelling/Gathering/Returning/timers are not required for safe Gather scheduling today.

When all configured teams are busy, the UI should say it is waiting. If the world-map observation is unavailable, it should wait for trusted team status rather than treating the screen as idle.

## Exact-team dispatch

For one selected-team Gather attempt, `bot/adapters.py`:

1. reuses the proven Gold search/resource/taken-warning Scenario;
2. reaches the dispatch panel;
3. requires the chosen team’s blue idle indicator;
4. explicitly clicks that exact team card;
5. exits if that team is no longer idle;
6. closes/stops on no-free-march instead of replacing another march.

The dispatch-panel exact-team check remains the final authority even if map-side perception was wrong.

## Input ownership

Development/Science are finite. Rally is continuous/controller-owned. Auto Gather may observe while finite work runs but waits while input is busy. Rally and continuous Gather are currently not allowed together because cooperative preemption is not yet implemented. Alerts remain passive.

## Dashboard contract

Dashboard status is read-only; it must never drive automation. It may show `Idle`, `Busy`, or `Unknown` from the current map-side detector and exact Team N during an in-flight Gather attempt.

Do not show a timer-derived Idle state. Detailed activity/timer presentation can be added after real perception evidence exists.

## Preservation rules

Protect:

- mature Rally behavior;
- target-window/foreground safety;
- Gather search-until-found and resource-taken recovery;
- trusted-world-map gate before treating blank status as 0/3;
- contradictory evidence -> Unknown;
- exact-team dispatch-panel verification;
- busy-team protection;
- fail-closed stale/untrusted state;
- passive alert isolation;
- Advanced tooling availability.

See `AGENTS.md` for mandatory commit and documentation-sync requirements.
