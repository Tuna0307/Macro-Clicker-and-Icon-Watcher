# Continuous Auto Gather workflow

This document is the behavior contract for **normal Bot Auto Gather**.

The normal Bot no longer treats gathering as a one-shot scenario that sends a configured number of marches or replaces busy teams in a fixed `3 -> 2 -> 1` order.

Instead, Auto Gather is a persistent service driven by the visually observed state of Team 1/2/3.

## Current architecture

```text
TeamStatusMonitor
      ↓
TeamStatusDetector
      ↓
TeamStateTracker
      ↓
ContinuousGatherService
      ↓
selected-team runtime Gather scenario
      ↓
MacroEngine
```

Relevant modules:

- `macro_clicker/bot/team_state.py` — shared thread-safe Team 1/2/3 state and countdown hints.
- `macro_clicker/bot/team_status.py` — read-only expedition-sidebar detection and timer OCR.
- `macro_clicker/bot/continuous_gather.py` — decides when one configured team is safe to send.
- `macro_clicker/bot/adapters.py` — converts the proven Gather scenario into one exact-team runtime attempt.
- `scenarios/Gather Gold.json` — proven Gold search/resource/taken-warning flow reused underneath.
- `macro_clicker/resource_gathering.py` — legacy/scenario state compatibility; not the normal continuous Bot policy.

## Normal-user policy

- Resource: Gold.
- Start at the configured resource level.
- If the search panel remains visible after Search, lower one level and search again.
- There is no macro-defined minimum cutoff; at the game's own minimum, repeated level-down attempts leave the level unchanged and searching continues.
- Users choose which of Team 1/2/3 may be used for gathering.
- There is no user-facing team priority.
- A team must be **freshly visually confirmed Idle** before Auto Gather may start an attempt for it.
- Travelling, Gathering, Returning, Busy, or Unknown teams are left alone.
- If every configured team is busy, Auto Gather waits.
- Auto Gather never intentionally recalls/replaces a busy team in normal Bot mode.
- Before Dispatch, the runtime Gather attempt re-verifies the exact chosen team's idle indicator on the dispatch panel and clicks that exact team card.
- If the chosen team became busy before the dispatch screen is ready, the attempt closes/stops rather than allowing the game to auto-select another team.
- If the game reports no free march, the selected-team attempt closes/stops instead of replacing an occupied march.
- A confirmed dispatch immediately marks that team non-idle in the tracker until fresh visual state catches up.
- An unconfirmed/aborted exact-team attempt pauses Auto Gather fail-closed instead of retrying blindly.

## Team states

The shared tracker currently models:

```text
IDLE
TRAVELLING
GATHERING
RETURNING
BUSY
UNKNOWN
```

The world-map expedition sidebar is the visual source of truth when visible.

A team can already be busy before the Bot starts. Auto Gather must respect that existing game state rather than assuming the Bot created it.

Example:

```text
Team 1  Idle
Team 2  Gathering  03:52:10
Team 3  Returning   00:00:12
```

Expected behavior:

```text
Team 1 -> eligible for Gather
Team 2 -> leave alone
Team 3 -> leave alone
```

When Team 3 later becomes visually Idle, it may then be used.

## Timer rule: hint, never authority

Visible `HH:MM:SS` values are useful for deciding **when it is worth checking again**.

They are not proof that a team is free.

Example:

```text
Gathering 00:00:01
      ↓ local countdown
Gathering 00:00:00
```

The tracker must **not** turn that into `IDLE` automatically.

Instead:

```text
countdown reached zero
      ↓
request/perform a fresh visual check
      ↓
maybe game now shows Returning 00:00:14
      ↓
keep waiting
```

Only fresh visual state may authorize another dispatch.

This protects against return travel, lag, manual changes, depleted nodes, and OCR/timing error.

## Exact-team dispatch

The normal continuous Bot must not rely on the game's automatically recommended team.

For a selected team, the runtime adapter:

1. opens/searches using the existing Gather scenario;
2. reaches the dispatch panel;
3. requires the normal Dispatch button to be visible;
4. requires the selected team's idle indicator to be visible;
5. clicks that exact team's card;
6. waits briefly;
7. runs the existing Dispatch action;
8. verifies success through the existing Gather success path.

If the selected team's idle indicator is absent at the dispatch panel, a runtime-only guard exits the attempt instead of sending a different team.

## All-busy behavior

Normal continuous Auto Gather behavior is:

```text
all configured teams busy
        ↓
       wait
        ↓
visual monitor keeps state updated
        ↓
one team eventually becomes Idle
        ↓
start one exact-team Gather attempt
```

Do **not** reintroduce a normal Bot setting such as:

```text
replacement order 3 -> 2 -> 1
```

unless the product requirement explicitly changes back to interrupting busy teams.

## Resource-taken behavior

The proven Gather scenario still contains the observed resource-taken Cancel/retry path.

Within a one-team attempt, a taken resource should not be treated as a successful dispatch. The flow returns to searching rather than advancing an external team-order pointer because continuous Bot mode has no such pointer.

## Dispatch-button template

`templates/GatherDispatchButton.jpg` is intentionally a tight crop of stable pixels from the Dispatch button.

Do not include:

- mouse cursor;
- changing travel/gather timers;
- other dynamic text.

A previous loose crop caused later dispatch panels to miss detection even when the button was visibly present.

## Legacy compatibility

The stored scenario and older helper still support legacy fields such as:

- `march_count`;
- `replacement_order`;
- `gather_control` replacement state.

These remain loadable so older configs/Advanced workflows do not break.

They are **not** the normal continuous Bot contract.

Do not expose those legacy fields as if they control continuous Auto Gather.

## Safety invariants

Preserve:

- fresh visual Idle required before starting an attempt;
- stale Idle observations cannot dispatch;
- hidden/missing sidebar cannot authorize a dispatch;
- timer expiry cannot promote a team to Idle;
- exact selected team must still be idle at the dispatch panel;
- busy teams are never intentionally overwritten;
- no-free-march closes/stops rather than replacing;
- all clicks still go through `MacroEngine` safe input paths;
- foreground-window/target-window/monitor/kill-switch protections remain active;
- an unconfirmed attempt pauses fail-closed.

Do not move Gather clicking to direct `pyautogui` calls.

## Regression coverage

Important automated coverage includes:

- `tests/test_continuous_gather.py`
- `tests/test_bot_adapters.py`
- `tests/test_bot_status.py`
- `tests/test_bot_ui_runtime.py`
- existing legacy/scenario Gather tests in `tests/test_resource_gathering.py` and `tests/test_auto_gather_scenario.py`

Protect at least:

- all-busy waiting;
- configured-team filtering;
- stale/hidden visual state fail-closed behavior;
- zero timer not becoming Idle;
- exact-team click before Dispatch;
- no-free-march no-replacement behavior;
- successful dispatch marking the exact team non-idle;
- failed/unconfirmed attempt pausing the service.

## Live verification still required

Windows CI cannot prove the real game templates/click geometry.

A supervised real-game test should deliberately begin with mixed team state, for example:

```text
Team 1  Idle
Team 2  Gathering
Team 3  Travelling
```

Verify that:

1. only Team 1 is chosen;
2. Team 1 is explicitly clicked before Dispatch;
3. Team 2 and Team 3 remain untouched;
4. after dispatch, all-busy state waits;
5. whichever configured team becomes visually Idle next is sent;
6. displayed timers/statuses follow Travelling/Gathering/Returning transitions;
7. F12 or an unconfirmed dispatch does not automatically restart another attempt.