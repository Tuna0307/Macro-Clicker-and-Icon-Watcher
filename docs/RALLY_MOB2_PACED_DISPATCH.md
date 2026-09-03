# Three-Team Rally — Mob2-Paced Dispatch Correction

## Live evidence — 2026-09-03

The user supplied two 1920×1080 recordings for comparison:

- `Screen Recording 2026-09-03 191019` — the mature `Rally gold mob_ 2 team` behavior and desired pacing;
- `Screen Recording 2026-09-03 191419` — the three-team run that reached formation/team selection but failed to commit the final Attack.

The accompanying three-team log explains the failure. A representative sequence was:

1. `Attack Confirm` fired.
2. Fresh fixed-slot detection correctly reported `T1=BUSY T2=BUSY T3=IDLE`.
3. Team 3 was correctly selected and its fixed card was clicked.
4. The configured random 1.0–1.5 second wait ran.
5. The generic action loop then logged `conditions changed before action #3` and skipped the final Attack click.

This repeated across several otherwise valid Level 35/40 opportunities.

The failure was not the team policy. The final fixed-slot detector and Team 3 selection were working. The problem was that clicking a different fixed Team card legitimately changes formation-screen pixels. The generic multi-action safety rule therefore considered the original full `Attack Confirm` detection context stale and re-required the entire original condition set before action #3. That was too strict for this specialized transition.

## v8 correction

`macro_clicker/rally_hot_path_v8_runtime.py` is installed after the existing v6 and v7 overlays. It is explicit-three-team only; the two-team path is not changed.

### 1. Restore the two-team entry settle

The v6 hot path intentionally skipped the wait immediately after the world-map Rally icon click. The working two-team reference uses a 0.3 second settle and the user confirmed that pacing is appropriate.

v8 therefore restores exactly a **0.3 second Rally-entry settle** for the three-team path. This does not slow normal opportunity scanning: world-map/Rally polling remains on the existing 30–50 ms hot loop.

### 2. Keep the configured random dispatch wait

After a fixed Team card is selected, v8 still honors the scenario's configured random **1.0–1.5 second** delay. It is consumed once for that `(Rally level, selected Team)` selection.

### 3. Fresh Attack-only revalidation

After the random wait, v8 no longer asks whether every original `Attack Confirm` condition is still visually identical to the pre-selection frame.

Instead it:

1. identifies the exact condition referenced by the configured final Attack click;
2. freshly captures/rechecks that `Attack.png` condition only;
3. allows a short bounded 0.35 second / 50 ms retry window for the button to finish settling;
4. clicks the fresh detected Attack center through the normal safe `_click_point` path;
5. if Attack cannot be positively proven, sends **no Attack click**.

No fixed Attack coordinate is introduced.

### 4. Preserve normal cleanup

When the fresh Attack click succeeds, v8 marks the specialized dispatch portion complete and uses the engine's existing abort-cleanup mechanism to run the later `set_step` cleanup actions. This prevents the generic action loop from reaching the stale old action #3 while preserving the scenario's existing enabled/disabled state transitions.

The Rally-entry latch is released only after the fresh Attack click commits. v7's 3/3 suppression and all-busy tray recovery remain in force.

## v9 follow-up — deadloop recovery and remembered availability

`macro_clicker/rally_hot_path_v9_runtime.py` is installed after v8. It does not change the v8 final Attack path; it closes two remaining efficiency/recovery gaps around it.

### Rally-entry deadloop recovery

After the world-map Rally icon click, v9 starts a bounded `expect Rally page` state. `RallyPage.png` or `GoldMob.png` positively proves progress and disarms the watch.

If the world-map Rally icon becomes positively visible again after a short 0.45 second grace, the workflow/latch is cleared immediately. If no Rally-page progress appears for 2.5 seconds, the same transient Rally workflow is cleared and scanning resumes.

No unrecognized dismissal click is sent. If the world map itself is not proven at timeout, the existing full-screen MisClick Base detector is kept armed briefly so only a positively recognized Base popup can perform its known recovery.

### Remember exact Team state after dispatch

Every validated final fixed-slot read may populate an exact Team 1/2/3 cache. A successful v8 dispatch then marks only the selected Team `BUSY` in that cache.

The next `Joining` level filter can therefore reject Rally rows that no remaining known-IDLE team can handle **before** clicking another `+`.

With T1=65, T2=55, T3=55, if T1 has just been dispatched and T2/T3 are still known IDLE, the effective Rally-page ceiling becomes 55. Lv56-65 rows are rejected before `+` input.

The world-map squad counter is used only as an identity-cache invalidation signal. An unexpected count change means a team may have returned, so exact Team identity is discarded. An expected one-step count increase caused by our own confirmed dispatch preserves the cache.

## Expected live markers

A current three-team test should include:

```text
[build] JOIN-HOT-RACE-v7 full-squad recovery loaded
[build] JOIN-HOT-RACE-v8 mob2-paced dispatch loaded
[build] JOIN-HOT-RACE-v9 deadloop+team-cache loaded
```

If the log does not show the v9 marker, the executable/session is stale and should not be used to judge the new recovery/cache behavior.

## Expected successful dispatch trace

A valid opportunity should still resemble:

```text
[team3] Rally Lv35; fresh final T1=BUSY T2=BUSY T3=IDLE
[team3] eligible T3(...); selected T3
[team3] clicked fixed Team 3 card ...
wait 1.xs (random 1-1.5s)
[team3] fresh Attack revalidated after Team selection score=...
click (...)
[team3] dispatch committed through fresh Attack target
[team-cache] confirmed dispatch => T3=BUSY; remaining known-idle level ceiling recalculated
```

A stalled entry may instead show:

```text
[rally-v9] stale Rally entry recovered (...); workflow/latch cleared, no blind click sent
```

The previous `conditions changed before action #3` line should no longer be the reason a valid three-team dispatch fails.

## Safety invariants retained

- positive `3/3` still suppresses Rally entry;
- all-busy fixed tray still never dispatches;
- final Team status is still fresh fixed-slot ZZ evidence;
- only an exact `IDLE` capable Team is eligible;
- cached Team identity is invalidated when world-map squad count changes unexpectedly;
- cache use can only reduce unnecessary `+` clicks and never authorizes a final dispatch;
- final Attack is still positively image-proven immediately before input;
- deadloop recovery does not blind-click an unrecognized screen;
- target-window containment, fresh geometry, monitor checks, F12, and PyAutoGUI fail-safe remain;
- legacy two-team runtime is unchanged.
