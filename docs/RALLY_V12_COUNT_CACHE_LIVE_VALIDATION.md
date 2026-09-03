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

## Existing mob_2-style refresh path

The three-team `Joining` action already has the same no-match recovery shape used by the working two-team Rally flow:

- OCR/level filtering finds no eligible row;
- `no_match_condition_index` targets `BackButton.png`;
- the Back button closes the Rally page;
- `Joining`, `Attack Confirm`, `Back if wrong mob`, and `Back if no slot` are disabled;
- the world-map Rally scanner immediately resumes and can reopen the Rally page.

Therefore v12 does not add a new refresh click. It fixes the availability state so an invalid level actually reaches this existing Back -> reopen branch **before** any row `+` click.

## v12 correction

v12 installs after v11 and changes only the v9 squad-count staleness policy.

### Confirmed dispatch outranks a lagging sidebar

After a real Dispatch the macro already knows exactly which Team it sent. If the sidebar remains at the old count for the old 0.75-second settle period, v12 no longer invalidates the exact cache.

Instead it logs that the sidebar is lagging and preserves the fixed-team cache while waiting for the expected `count + 1` observation.

A very long 30-second failure to ever observe the expected count still invalidates the cache conservatively.

### Late expected increment is accepted

If the sidebar eventually changes from (for example) `0/3 -> 1/3`, and that is the increment expected from the macro's own confirmed dispatch, the cache remains valid even if the increment arrived several seconds late.

### Count changes require stable world-map evidence

An unrelated count change no longer invalidates exact identity from one sample.

- `1/3`, `2/3`, and `3/3` changes require a short stable confirmation.
- derived `0/3` requires a longer 2.0-second stable confirmation because the repository has no dedicated `0/3` image and v9 infers zero from the `/3` suffix plus absence of the complete 1/2/3 templates.
- count polling is suppressed while Rally entry is latched or the formation-opening guard is active.
- the world-map Rally icon must also be positively visible before the count observer is allowed to use the sidebar sample.

A new exact fixed-slot formation capture immediately cancels any pending count-change candidate because fixed slots are the stronger evidence source.

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

For a Lv70 row the desired sequence is:

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

After Back, the normal scanner may reopen Rally immediately, matching the working `Rally gold mob_ 2 team` refresh behavior.

## Build marker

A current explicit three-team run must now include:

```text
[build] JOIN-HOT-RACE-v12 stable squad-count cache guard loaded
```

The earlier v7-v11 build markers remain expected as well.

## Regression coverage

v12 tests cover:

- a lagging expected dispatch count preserving the exact cache;
- the expected increment arriving late without invalidating the cache;
- derived `0/3` requiring the longer stable confirmation;
- positive count changes using the shorter confirmation;
- squad-count polling being suppressed while Rally entry is latched;
- world-map Rally-icon proof being required before count polling can affect cache validity; and
- Rally tests deriving expectations from the selector maxima loaded from the scenario instead of hard-coding old values.

The legacy two-team scenario is not modified.
