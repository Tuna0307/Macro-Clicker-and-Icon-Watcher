# Three-Team Rally v13 No-Match Back Live Validation — 2026-09-04

## Live failure

A supervised three-team run proved that v12 correctly rejected a Rally level above the remaining known-IDLE team ceiling, but the workflow did not reopen Rally afterward.

The important sequence was:

```text
[team-cache] using known fixed-team availability; Rally-row ceiling=60
[level] paddleocr_rec read 65 ...
[skip] ... 65 > available-team max 60
[skip] 'Joining' no valid matching row target
[no-match] click condition #2 (...BackButton...)
[team3] cleared transient status (failed Rally transition)
[no-match] step 'Joining' -> disabled
[no-match] step 'Attack Confirm' -> disabled
[no-match] step 'Back if wrong mob' -> disabled
[no-match] step 'Back if no slot' -> disabled
```

The Back click visibly returned the game to the world map and the Rally icon remained present, but no later `Enter Rally after team probe` fired before the scenario was manually stopped.

## Root cause

This is separate from OCR, level filtering, Team availability, and final Team selection.

v7 introduced `_rally_hot_entry_latched` so the world-map Rally entry step cannot fire again while the same Rally/formation workflow is active. The named recovery actions (`Back if wrong mob`, `Back if no slot`, MisClick Base, final abort/dispatch paths) release that latch when they successfully exit the workflow.

The v12 wrong-level refresh uses `click_matching_row`'s internal **no-match fallback** instead. That fallback directly clicks the `BackButton.png` condition and disables the Rally sub-steps. It does not execute the named `Back if wrong mob` or `Back if no slot` action, so the v7 action wrapper never receives an event from which it can release the entry latch.

The result was:

```text
wrong level rejected
-> internal BackButton fallback succeeds
-> Rally page closes
-> Rally sub-steps disabled
-> _rally_hot_entry_latched still True
-> Enter Rally after team probe suppressed forever
```

## v13 correction

v13 installs after v12 and wraps only the existing `_run_no_match_fallback` boundary.

After the original fallback completes, v13 releases the entry latch only when **all** of these are true:

1. the run is explicit three-team Rally mode;
2. the step is `Joining`;
3. the action is `click_matching_row`;
4. its configured no-match condition resolves specifically to `BackButton.png`;
5. the original no-match fallback reports success;
6. the engine is not asking to retry the current step; and
7. the Rally entry latch was actually set before the fallback.

When those conditions are satisfied, v13 performs the same state transition needed by the normal Back recovery:

```text
_rally_hot_entry_latched = False
```

No additional click is sent. No delay is added.

## Expected live behavior

With the currently committed selector values:

```text
T1 max = 80
T2 max = 60
T3 max = 60
```

if exact cached availability is:

```text
T1=BUSY T2=IDLE T3=IDLE
```

then a Lv65 Rally must now behave as:

```text
[team-cache] using known fixed-team availability; Rally-row ceiling=60
[level] ... read 65
[skip] ... 65 > available-team max 60
[skip] 'Joining' no valid matching row target
[no-match] click condition #2 (...BackButton...)
[rally-v13] no-match Back completed; Rally entry latch released for world-map refresh
```

After the game is back on the map and `RallyIcon.png` remains visible, the normal scanner is allowed to fire again:

```text
[fire] Enter Rally after team probe
click (...RallyIcon...)
[rally-fast] Rally entry latched until workflow exit
```

The macro can therefore repeatedly refresh the Rally list by Back -> reopen until a level acceptable to an available Team appears.

## What must not happen

For a rejected level there must still be no row `+` input:

```text
[rally-fast] revalidated last-slot +
click matching row
```

must not occur after the `> available-team max` rejection.

v13 also must not release the latch when:

- the Back fallback click failed;
- the engine marked the fallback for retry;
- the no-match target is not `BackButton.png`;
- the step is not `Joining`; or
- the run is the legacy two-team scenario.

## Build marker

A current explicit three-team run must include:

```text
[build] JOIN-HOT-RACE-v13 no-match Back latch release loaded
```

The v7-v12 markers remain expected before it.

## Regression coverage

`tests/test_rally_hot_path_v13_runtime.py` protects:

- successful three-team Joining Back fallback releases the latch;
- failed fallback keeps the latch;
- retrying fallback keeps the latch fail-closed;
- a non-Back fallback does not release the latch; and
- the two-team path is unchanged.

This change does not modify OCR, configured level maxima, Team identity, Team priority, Attack revalidation, random dispatch timing, scenario JSON, or `Rally gold mob_ 2 team`.
