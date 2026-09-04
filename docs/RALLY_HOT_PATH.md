# Three-Team Rally Hot Path

This document describes the speed-sensitive runtime used only by explicit three-team Rally scenarios. The legacy two-team Rally path is intentionally unchanged.

## Goal

Rally joining is a race. The macro should spend CPU/time on expensive safety work only when that work can actually matter, while preserving fail-closed final Team selection.

The current hot path combines the v6 fast scheduler, v7 entry latch/full-squad recovery, v8 Mob2-paced final dispatch, v9 deadloop/team-availability cache, v10 transition-stable tray recovery, v11 phase-correct entry watchdog, v12 stable squad-count cache guard, v13 no-match Back latch release, and v14 post-dispatch count backtrack guard.

## World-map loop

Three-team Rally runs at a 0.05 second polling interval at runtime. The normal entry condition still detects the small Rally icon first.

Before entry, the runtime can positively prove `3/3` from the left-side squad counter. A proven `3/3` suppresses Rally entry because all three squads are already out. Failure to prove `3/3` is fail-open only for this entry optimization; it never authorizes a dispatch.

The sidebar count never identifies Team 1/2/3. Exact Team identity comes only from the fixed formation slots.

## v9/v12/v14 squad-count observer

v9 samples the world-map squad counter only as a staleness signal for the exact Team cache.

Dedicated templates recognize `1/3`, `2/3`, and `3/3`. The repository has no dedicated `0/3` template, so zero is inferred only from a positively matched `/3` suffix plus absence of the complete 1/2/3 templates.

v12 tightened this evidence after a live run showed that a lagging sidebar could erase stronger fixed-slot knowledge and make an invalid high-level Rally look eligible:

- polling is suppressed while Rally entry is latched or the formation-opening guard is active;
- `RallyIcon.png` must be positively visible before a sidebar sample can affect the cache;
- one unexpected count sample is only a candidate;
- `1/3`, `2/3`, and `3/3` changes require short stable confirmation;
- inferred `0/3` requires a longer 2.0 second confirmation;
- after a confirmed dispatch, the known Team sent by the macro outranks a temporarily lagging sidebar count;
- a late expected `count + 1` still preserves exact Team identity;
- only a prolonged 30-second failure to ever observe that expected increment abandons the cache conservatively.

A fresh validated fixed-slot formation capture cancels any pending count-change candidate because fixed Team slots are stronger evidence.

### v14 post-dispatch count backtrack guard

A 2026-09-04 live run proved that v12 still had one unsafe evidence-ordering gap. After Team 3 was positively selected and dispatched, the exact cache was:

```text
T1=BUSY T2=IDLE T3=BUSY
```

The previous world count was `1/3`, so the macro expected its own dispatch to become visible as `2/3`. Instead the transition briefly/stably exposed `0/3`. v12 treated the changed non-expected count as unrelated, eventually invalidated the exact cache, and the Rally-page filter fell back to broad max80. A later Lv70 row then received a `+` click even though only max60 Team 2 remained idle.

v14 keeps the stronger exact fixed-Team evidence authoritative during that unresolved own-dispatch window. While an expected post-dispatch count is pending and still inside v12's existing 30-second stale horizon, a count that is different from both the previous count and the expected count:

- cannot replace the previous count;
- cannot advance a v12 count-change candidate;
- cannot invalidate the exact Team cache; and
- therefore cannot broaden the Rally-page level ceiling.

The guard logs:

```text
[team-cache] transient squad count 1/3 -> 0/3 while confirmed dispatch expects 2/3; preserving exact fixed-team cache
```

An unchanged count still uses v12's existing lag/stale handling, and the expected increment may still arrive late and be accepted normally. After the existing stale horizon expires, v12's ordinary stable-count policy is allowed to resume.

## Rally-entry latch

v7 latches Rally entry after the first successful world-map Rally click.

While latched, `Enter Rally after team probe` is suppressed so the same visible Rally icon cannot be clicked again in the middle of the same Rally/formation workflow.

The latch is released by normal workflow exits such as:

- `Back if wrong mob`;
- `Back if no slot`;
- MisClick Base recovery;
- final three-team abort;
- successful Attack/dispatch; and
- confirmed all-busy tray recovery.

This is a state guard, not a long cooldown.

## v9/v11 initial-entry deadloop recovery

v9 gives the entry latch a bounded failure exit. After the Rally-icon click it expects positive Rally-page progress.

`RallyPage.png` or `GoldMob.png` proves forward progress. If the world-map Rally icon reappears after the short grace period, or if no Rally progress appears before the bounded timeout, the stale Rally workflow/latch is cleared without a blind click.

v11 makes that watchdog phase-correct: once `Joining` has positively reached the Rally page, a Rally level is carried, or the formation-opening guard is active, the initial-entry watchdog is disarmed. A Rally icon visible behind an opening formation screen can no longer unwind a successful workflow.

## v13 no-match Back latch release

A 2026-09-04 live run exposed a different deadlock after v12 correctly rejected a Rally level above the remaining-team ceiling.

The sequence was:

```text
[team-cache] using known fixed-team availability; Rally-row ceiling=60
[level] ... read 65
[skip] ... 65 > available-team max 60
[skip] 'Joining' no valid matching row target
[no-match] click condition #2 (...BackButton...)
```

The Back click visibly closed Rally and returned to the map, but the Rally icon was never clicked again. The cause was that `click_matching_row` performs its no-match Back through the engine's internal fallback rather than through the named `Back if wrong mob` / `Back if no slot` actions. The Back succeeded, but v7's action-level latch-release hook never saw it.

v13 wraps only that internal no-match boundary. After the original fallback succeeds, it releases `_rally_hot_entry_latched` only when all of these are true:

1. explicit three-team mode;
2. step name is `Joining`;
3. action type is `click_matching_row`;
4. the configured no-match condition is specifically `BackButton.png`;
5. the original fallback reported success;
6. the engine is not retrying the step; and
7. the latch was set before the fallback.

No extra click and no delay are added.

Expected continuation is now:

```text
[no-match] click condition #2 (...BackButton...)
[rally-v13] no-match Back completed; Rally entry latch released for world-map refresh
[fire] Enter Rally after team probe
```

when the Rally icon remains visible on the world map.

A failed fallback, retrying fallback, non-Back fallback, unrelated step, or two-team run does not release the latch.

## Transition pacing

v8 keeps the user-confirmed two-team pacing at the world-map -> Rally boundary with one 0.3 second settle after the Rally icon click.

After Team selection, the configured random dispatch delay is consumed once. Only the exact `Attack.png` target is then freshly revalidated before final input. No fixed Attack coordinate is used.

## MisClick Base and Profile

MisClick Base keeps full-screen coverage but is scheduled only around states where it can matter. A recognized Base popup can safely recover the workflow; no blind Base dismissal is introduced.

MisClick Profile is armed around the Rally-row `+` race. Immediately before input, the selected `Join.png` control is freshly revalidated in a small local crop. If the `+` vanished, the stale click is cancelled before a replacement portrait can receive it.

## Full-squad tray recovery

When all three squads are already out, clicking a valid Rally row `+` can expose only the fixed bottom squad-card tray instead of the normal formation panel.

v7 requires positive evidence:

1. bottom tray anchor `AddSquad.png`;
2. absence of the normal formation anchor;
3. exact fixed slot states `BUSY / BUSY / BUSY`;
4. one user-validated neutral tray dismissal; and
5. no Team-card or Attack input.

Any `IDLE` or `UNKNOWN` evidence fails closed.

v10 prevents a partially rendered normal formation transition from being misclassified as that all-busy tray. Tray recovery is forbidden for the first 1.0 second after a valid row `+` click, then BUSY/BUSY/BUSY must remain stable before v7 performs its own fresh final proof.

Normal `Attack Confirm` polling continues during that guard; it is not a blocking sleep.

## Exact Team availability cache and level filtering

The fixed formation slots remain authoritative:

- fixed slot 1 = Team 1;
- fixed slot 2 = Team 2;
- fixed slot 3 = Team 3;
- ZZ present = `IDLE`;
- validated slot without ZZ = `BUSY`;
- invalid/unproven formation screen = `UNKNOWN`.

After a successful dispatch, the selected Team is immediately marked `BUSY` in the exact cache. The next Rally-page filter computes its ceiling only from remaining known-IDLE Teams.

The currently committed selector values are editable and must be read from the scenario at runtime. At the time of this document they are:

```text
T1 max = 80
T2 max = 60
T3 max = 60
```

Therefore, if cached availability is:

```text
T1=BUSY T2=IDLE T3=IDLE
```

then the effective Rally-page ceiling is 60. A Lv65 or Lv70 row is rejected before any row `+` revalidation or click.

After Team 3 has also been dispatched, the cache may instead be:

```text
T1=BUSY T2=IDLE T3=BUSY
```

The ceiling is still 60 because Team 2 is the only known-IDLE Team. v14 ensures a transient post-dispatch `1/3 -> 0/3` observation cannot discard that exact evidence and accidentally restore broad max80 during the guarded own-dispatch transition.

When no row is eligible, `Joining` uses its configured `BackButton.png` no-match fallback, disables the Rally sub-steps, returns to the world map, and v13 releases the entry latch so the normal Rally scanner can reopen the page and inspect a refreshed list.

If all cached Teams are known BUSY, the ceiling is `none`, so no row `+` qualifies.

The cache can reject unnecessary `+` clicks earlier, but it never authorizes final dispatch and never makes `UNKNOWN` eligible. Final Team selection still requires a fresh fixed-slot check.

## Build markers

A current explicit three-team run should include:

```text
[build] JOIN-HOT-RACE-v7 full-squad recovery loaded
[build] JOIN-HOT-RACE-v8 mob2-paced dispatch loaded
[build] JOIN-HOT-RACE-v9 deadloop+team-cache loaded
[build] JOIN-HOT-RACE-v10 transition-stable tray recovery loaded
[build] JOIN-HOT-RACE-v11 phase-correct entry watchdog loaded
[build] JOIN-HOT-RACE-v12 stable squad-count cache guard loaded
[build] JOIN-HOT-RACE-v13 no-match Back latch release loaded
[build] JOIN-HOT-RACE-v14 post-dispatch count backtrack guard loaded
```

Useful current logs include:

```text
[rally-v9] stale Rally entry recovered (...); workflow/latch cleared, no blind click sent
[rally-v11] entry watchdog disarmed (...)
[team-cache] exact fixed slots cached: T1=... T2=... T3=...
[team-cache] confirmed dispatch => T1=BUSY; remaining known-idle level ceiling recalculated
[team-cache] sidebar count still 0/3 while confirmed dispatch expects 1/3; preserving exact fixed-team cache
[team-cache] transient squad count 1/3 -> 0/3 while confirmed dispatch expects 2/3; preserving exact fixed-team cache
[team-cache] using known fixed-team availability; Rally-row ceiling=60
[rally-v13] no-match Back completed; Rally entry latch released for world-map refresh
```

For a row above the available-team ceiling, expected logs are:

```text
[team-cache] using known fixed-team availability; Rally-row ceiling=60
[level] ... read 65
[skip] ... 65 > available-team max 60
[skip] 'Joining' no valid matching row target
[no-match] click condition #2 (...BackButton...)
[rally-v13] no-match Back completed; Rally entry latch released for world-map refresh
```

There must be no last-slot `+` revalidation/click for that rejected row.

## Regression requirements

Tests must continue proving:

- the legacy two-team path is unchanged;
- positive `3/3` suppresses Rally entry;
- Rally entry is latched after the first world-map click and cannot re-fire inside the same workflow;
- v9 releases genuine failed initial entry without blind clicks;
- v11 prevents the entry-only watchdog from running after Rally-page/formation progress;
- last-moment Join revalidation cancels vanished `+` controls;
- v10 requires transition-stable BUSY/BUSY/BUSY before tray dismissal;
- final Team identity comes only from fixed slots;
- a confirmed dispatch marks only the selected fixed Team BUSY;
- remaining known-IDLE Teams determine the Rally-page ceiling;
- lagging sidebar counts do not erase stronger exact Team state too quickly;
- unrelated count changes require stable world-map confirmation;
- inferred `0/3` uses the longer confirmation window;
- invalid levels never click row `+`;
- a successful invalid-level Back fallback releases the Rally-entry latch;
- failed/retrying/non-Back fallbacks do not release the latch;
- a changed non-expected world count during an unresolved own dispatch cannot erase the exact Team cache before the v12 stale horizon;
- the late expected increment can still resolve after that transient backtrack; and
- the existing fixed-slot screenshot matrix and fail-closed final selection remain green.
