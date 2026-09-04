# Three-Team Rally v14 Post-Dispatch Count Backtrack Guard — 2026-09-04

## Live failure reproduced

A supervised three-team run proved that Team 3 selection and dispatch were correct, but the exact Team-availability cache was discarded immediately afterward because the world-map squad counter moved backward during the post-dispatch transition.

Relevant live sequence:

```text
09:42:22.094  Rally Lv50; fresh final T1=BUSY T2=IDLE T3=IDLE
09:42:22.094  eligible T3(max60), T2(max60); selected T3
09:42:22.095  clicked fixed Team 3 card
09:42:23.498  fresh Attack revalidated
09:42:23.499  dispatch committed
09:42:23.499  cache marks T3=BUSY
09:42:23.807  candidate world-map squad change 1/3 -> 0/3
...
09:42:26.312  exact Team cache invalidated by stable 1/3 -> 0/3
...
09:42:42.632  Lv70 accepted against broad max80
09:42:42.791  Rally row + clicked
09:42:43.102  fresh fixed slots: T1=BUSY T2=IDLE T3=BUSY
09:42:43.103  no capable idle Team; backing out
```

This proves the defect was not the Team 3 card coordinate, Team priority, OCR, or final selector. The fixed Team 3 state was correct and the Attack dispatch was positively confirmed. The unsafe `+` happened only because the exact cache was thrown away after the post-dispatch counter backtrack.

## Why v12 was still insufficient

v12 already preserves exact Team identity when the world-map count remains at the previous value while waiting for the macro's own expected `count + 1` increment. It also accepts that expected increment when it arrives late.

However, v12 treated a *different* stable count as an unrelated change even while the macro was still waiting for its own confirmed dispatch to appear in the sidebar. In this live run the exact cache knew:

```text
T1=BUSY T2=IDLE T3=BUSY
```

and the previous world count was `1/3`, so the confirmed Team 3 dispatch created an expected count of `2/3`. The transient observation `0/3` was neither the previous value nor the expected value. After the ordinary derived-zero confirmation window, v12 invalidated the exact cache.

Once that exact cache was gone, `Joining` had no remaining-Team ceiling and fell back to the broad configured maximum of 80. That is why Lv70 received a Rally-row `+` click even though only max60 Team 2 was actually idle.

## v14 correction

v14 installs after v13 and guards only the unresolved post-dispatch count transition.

While all of the following are true:

1. explicit three-team mode is active;
2. the exact fixed-Team cache is valid;
3. the macro has a pending expected squad count produced by its own positively confirmed dispatch;
4. the newly observed count is different from both the previous count and that expected count; and
5. the existing v12 30-second expected-count stale horizon has not expired;

v14 treats that changed-but-non-expected count as transient evidence only.

It does **not**:

- replace `_rally_v9_last_squad_count`;
- advance a v12 count-change candidate;
- invalidate the exact Team cache;
- broaden the Rally-page level ceiling; or
- authorize any row `+` click.

Instead it logs:

```text
[team-cache] transient squad count 1/3 -> 0/3 while confirmed dispatch expects 2/3; preserving exact fixed-team cache
```

The expected increment may still arrive later and is handled by the existing v12 path:

```text
[team-cache] squad count 1/3 -> 2/3 matches our confirmed dispatch; exact-team cache preserved
```

An unchanged count still goes through v12's existing lag/stale handling. After the existing expected-count stale horizon expires, the ordinary stable-count invalidation policy is allowed to resume rather than hiding a genuinely stale world state forever.

## Expected behavior for the reproduced case

Immediately after Team 3 dispatch, a transient `1/3 -> 0/3` must no longer erase:

```text
T1=BUSY T2=IDLE T3=BUSY
```

Therefore the next Rally-page ceiling remains 60.

For Lv70 the required sequence is:

```text
[team-cache] using known fixed-team availability; Rally-row ceiling=60
[level] ... read 70
[skip] ... 70 > available-team max 60
[skip] 'Joining' no valid matching row target
[no-match] click condition #2 (...BackButton...)
[rally-v13] no-match Back completed; Rally entry latch released for world-map refresh
```

There must be no:

```text
[rally-fast] revalidated last-slot +
click matching row
```

for that rejected Lv70 row.

## Build marker

A current three-team run must include:

```text
[build] JOIN-HOT-RACE-v14 post-dispatch count backtrack guard loaded
```

The v7-v13 markers remain expected.

## Regression coverage

v14 tests cover:

- a `1/3 -> 0/3` backtrack during an expected `2/3` dispatch preserving the exact Team cache;
- repeated backward observations never becoming a stable invalidation candidate during the guarded period;
- a late expected `2/3` increment still resolving normally after the backtrack;
- the original v12 stable-change policy resuming after the expected-count stale horizon; and
- the existing v12 behavior remaining unchanged when no own-dispatch expectation is pending.

The legacy two-team scenario remains untouched.
