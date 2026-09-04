# Three-Team Rally v12 Count-Cache / Level-Filter Live Validation — 2026-09-03

## Live failure reproduced

The supervised run at about `22:55` proved that the row-level OCR itself was correct, but the availability ceiling feeding it was stale/too broad.

Relevant sequence:

```text
22:54:53.039  fixed slots: T1=IDLE T2=IDLE T3=IDLE
22:54:53.039  Rally Lv65 -> selected T1
22:54:54.394  Dispatch committed
22:54:54.394  cache marks T1=BUSY
...
22:55:01.501  cache invalidated because dispatch expected 1/3 but still observed 0/3
...
22:55:09.623  OCR reads Lv70
22:55:09.623  broad max80 accepts Lv70
22:55:09.624  row + is clicked
22:55:10.075  fresh final fixed slots: T1=BUSY T2=IDLE T3=IDLE
22:55:10.077  no capable idle Team; backing out
```

The same Lv70 row was then clicked again because another fast `1/3 -> 0/3` count observation invalidated the newly refreshed exact cache.

The problem was therefore **not OCR** and **not the final Team selector**. The problem was evidence ordering: a lagging/transitioning sidebar count was allowed to erase stronger exact fixed-slot knowledge too quickly.

## Existing mob_2-style refresh shape

The three-team `Joining` action has the same no-match Back shape used by the working two-team Rally flow:

- OCR/level filtering finds no eligible row;
- `no_match_condition_index` targets `BackButton.png`;
- the Back button closes the Rally page;
- `Joining`, `Attack Confirm`, `Back if wrong mob`, and `Back if no slot` are disabled.

v12's responsibility is to preserve the correct availability evidence long enough for an invalid level to reach this branch **before** any row `+` click.

A later 2026-09-04 live run proved one additional runtime detail was missing: the internal no-match Back bypassed v7's named Back-action latch-release hook. The page closed correctly, but `_rally_hot_entry_latched` remained true and suppressed the visible world-map Rally icon indefinitely. That continuation bug is corrected by v13 and documented in `RALLY_V13_NO_MATCH_BACK_LIVE_VALIDATION.md`.

## v12 correction

v12 installs after v11 and changes only the v9 squad-count staleness policy.

### Confirmed dispatch outranks a lagging sidebar

After a real Dispatch the macro already knows exactly which Team it sent. If the sidebar remains at the old count for the old 0.75-second settle period, v12 no longer invalidates the exact cache.

Instead it logs that the sidebar is lagging and preserves the fixed-team cache while waiting for the expected `count + 1` observation.

A very long 30-second failure to ever observe the expected count still invalidates the cache conservatively.

### Late expected increment is accepted only inside the stale horizon

If the sidebar changes from (for example) `0/3 -> 1/3`, and that is the increment expected from the macro's own confirmed dispatch, the cache may remain valid when the increment arrives late **while the expected-dispatch marker is still younger than 30 seconds**.

v18 tightens the other side of this rule: an expected increment that first appears at or after the existing 30-second stale horizon is no longer accepted as proof of the old dispatch. At that age the same count may represent a Team return or another unrelated world-map change, so it must go through normal stable-count confirmation instead.

### Count changes require stable world-map evidence

An unrelated count change no longer invalidates exact identity from one sample.

- `1/3`, `2/3`, and `3/3` changes require a short stable confirmation.
- derived `0/3` requires a longer 2.0-second stable confirmation because the repository has no dedicated `0/3` image and v9 infers zero from the `/3` suffix plus absence of the complete 1/2/3 templates.
- count polling is suppressed while Rally entry is latched or the formation-opening guard is active.
- the world-map Rally icon must also be positively visible before the count observer is allowed to use the sidebar sample.

A new exact fixed-slot formation capture immediately cancels any pending count-change candidate because fixed slots are the stronger evidence source.

## v14 follow-up: changed-but-non-expected count during own dispatch

A 2026-09-04 live run exposed one remaining gap in v12 after a successful Team 3 dispatch.

The fixed selector correctly produced:

```text
T1=BUSY T2=IDLE T3=IDLE
Rally Lv50 -> selected T3
Attack -> dispatch committed
cache => T1=BUSY T2=IDLE T3=BUSY
```

At that point the previous world count was `1/3`, so the confirmed Team 3 dispatch created an expected count of `2/3`. During the transition the observer instead saw `1/3 -> 0/3`. Because that value was neither the previous count nor the expected count, v12 eventually treated it as an unrelated stable change and invalidated the exact Team cache.

Once that cache was gone, a later Lv70 row fell back to broad max80 and its `+` was clicked. The fresh formation screen then re-proved `T1=BUSY T2=IDLE T3=BUSY` and correctly refused to dispatch, showing that Team 3 selection was never the problem.

v14 now protects only this unresolved own-dispatch window. While an exact cache is valid and an expected post-dispatch count is pending, a **changed but non-expected** count cannot advance a v12 invalidation candidate before the existing 30-second expected-count stale horizon expires.

For the reproduced case the new log is:

```text
[team-cache] transient squad count 1/3 -> 0/3 while confirmed dispatch expects 2/3; preserving exact fixed-team cache
```

This preserves the remaining-Team ceiling of 60 and prevents Lv70 from receiving a row `+` click during the guarded transition. The expected `2/3` may still arrive late inside the stale horizon and is handled normally by v12.

The complete v14 live note is in `RALLY_V14_POST_DISPATCH_COUNT_GUARD.md`.

## v18 follow-up: an old expected increment masked a Team return

A later 2026-09-04 long run exposed the opposite failure mode: the exact cache could remain **too restrictive** after Team availability changed.

The relevant live sequence was:

```text
15:52:35  confirmed dispatch => T3=BUSY
15:52:49  confirmed dispatch => T1=BUSY
...
15:54:10  squad count 0/3 -> 1/3 matches our confirmed dispatch;
           exact-team cache preserved
15:54:11  OCR reads Lv70
15:54:11  70 > available-team max 60
15:54:13  OCR reads Lv70
15:54:13  70 > available-team max 60
```

At that point the old exact cache still effectively said:

```text
T1=BUSY T2=IDLE T3=BUSY
```

so the Rally-page ceiling remained 60 even though the world-map count had changed long after the old dispatch. Because the count happened to equal an old expected increment, v12 accepted it as delayed dispatch proof without checking the age of that expectation.

v18 applies v12's existing 30-second stale horizon to the **arrival** branch too. When `count == expected` first appears at or after 30 seconds:

1. the old expected-dispatch marker is cleared;
2. the sample is treated as ordinary count-change evidence rather than proof of the old dispatch;
3. the normal v12 stable confirmation still applies (`0.45s` for positive counts, `2.0s` for derived zero);
4. only after that stable confirmation is the stale exact-Team cache invalidated; and
5. the next formation screen still requires the existing fresh fixed Team 1/2/3 proof before any Attack input.

Expected v18 log:

```text
[rally-v18] expected squad increment 0/3 -> 1/3 arrived after 80.0s; treating it as ordinary count-change evidence, not old dispatch proof
[team-cache] candidate world-map squad change 0/3 -> 1/3; require 0.45s stable confirmation
[team-cache] invalidated (stable world-map squad count changed 0/3 -> 1/3)
```

This intentionally does **not** infer that Team 1 returned. It only admits that the old identity cache is no longer trustworthy. A later Lv70 row may therefore open the formation screen under the broad configured ceiling, but final dispatch is still fail-closed:

```text
fresh fixed T1=IDLE  -> Lv70 may select T1 and dispatch
fresh fixed T1=BUSY  -> no capable idle Team; back out without Attack
fresh fixed UNKNOWN  -> fail closed; no dispatch
```

The two-team Rally path is unchanged.

## Expected level-rejection behavior

The selector maxima are editable and must never be hard-coded by the runtime or tests. At the time of this validation, the committed three-team selector is:

```text
T1 max = 80
T2 max = 60
T3 max = 60
```

If the exact cache says:

```text
T1=BUSY T2=IDLE T3=IDLE
```

then the Rally-page ceiling must be 60.

For a Lv70 row while that cache is still current, the desired filtering sequence is:

```text
[team-cache] using known fixed-team availability; Rally-row ceiling=60
[level] ... read 70
[skip] ... 70 > available-team max 60
[skip] 'Joining' no valid matching row target
[no-match] click condition #2 (...BackButton...)
```

There must be **no**:

```text
[rally-fast] revalidated last-slot +
click matching row
```

for that rejected Lv70 row.

With v13 installed after v12, a successful Back fallback is followed by:

```text
[rally-v13] no-match Back completed; Rally entry latch released for world-map refresh
```

and the normal scanner may then reopen Rally when `RallyIcon.png` remains visible.

Once v18 has positively invalidated a stale identity cache after a real world-count change, the next formation entry is allowed to refresh exact Team identity. The fixed-slot selector remains the final authority before Attack.

## Build markers

A current explicit three-team run must include:

```text
[build] JOIN-HOT-RACE-v12 stable squad-count cache guard loaded
[build] JOIN-HOT-RACE-v13 no-match Back latch release loaded
[build] JOIN-HOT-RACE-v14 post-dispatch count backtrack guard loaded
[build] JOIN-HOT-RACE-v18 stale expected-count expiry loaded
```

The earlier v7-v11 and later v15-v17 build markers remain expected as well.

## Regression coverage

v12 tests cover:

- a lagging expected dispatch count preserving the exact cache;
- an expected increment arriving late **inside** the stale horizon without invalidating the cache;
- derived `0/3` requiring the longer stable confirmation;
- positive count changes using the shorter confirmation;
- squad-count polling being suppressed while Rally entry is latched;
- world-map Rally-icon proof being required before count polling can affect cache validity; and
- Rally tests deriving expectations from the selector maxima loaded from the scenario instead of hard-coding old values.

v13 separately covers the successful no-match Back -> latch release continuation and its fail-closed guards.

v14 separately covers the Team 3 live regression where a post-dispatch `1/3 -> 0/3` backtrack occurred while `2/3` was expected. That backtrack must preserve the exact Team cache and cannot become a stable invalidation candidate during the guarded own-dispatch window.

v18 separately covers:

- an expected increment younger than 30 seconds still preserving exact identity;
- an expected increment at or beyond 30 seconds becoming ordinary count-change evidence;
- stable confirmation being required before invalidating the stale cache;
- v14's non-expected backtrack guard remaining unchanged inside its horizon;
- invalid-cache behavior remaining unchanged; and
- the legacy two-team path remaining unchanged.

The legacy two-team scenario is not modified.
