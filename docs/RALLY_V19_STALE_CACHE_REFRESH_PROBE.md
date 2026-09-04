# Rally v19 stale-cache formation refresh probe

## Live failure

A 2026-09-04 explicit three-team run loaded v18 correctly, dispatched Team 1 on Lv65, and then later encountered repeated Lv70 GoldMob rallies with a visible same-row Join `+`.

The repeated sequence was:

```text
[level] ... read 70 conf=1.00
[skip] ... 70 > available-team max 60
[skip] 'Joining' no valid matching row target
[no-match] ... BackButton
[rally-v13] ... latch released
[fire] Enter Rally after team probe
```

This happened several times.  The important later proof was that when a Lv30 row finally opened the formation screen, the fresh fixed-slot capture read:

```text
T1=IDLE T2=IDLE T3=IDLE
```

Therefore the earlier max60 ceiling was stale: Team 1 had already returned, but the sidebar-count observer had not produced usable evidence soon enough to invalidate the old exact-Team cache.

## Why v18 was insufficient

v18 fixes one ambiguity in world-count evidence: an expected increment that first arrives after the existing 30-second stale horizon is not accepted as proof of an old dispatch.

That still requires a usable world-map count sample.  If the sidebar does not provide a positively recognized change before a high-level Rally appears, the exact cache can remain conservative indefinitely and repeatedly reject an otherwise joinable Lv65-Lv80 row.

## v19 policy

v19 adds a bounded active refresh path while preserving fail-closed final dispatch.

When all of the following are true:

1. explicit three-team mode;
2. the exact fixed-Team cache is valid;
3. the cache is at least 30 seconds old;
4. the cached available-team ceiling is narrower than the editable configured selector ceiling; and
5. the `Joining` action is evaluating a Rally row;

then the row prefilter may temporarily use the broad configured selector ceiling for one short refresh-probe window.

With the current editable values:

```text
T1 max = 80
T2 max = 60
T3 max = 60
```

an old cache such as:

```text
T1=BUSY T2=IDLE T3=BUSY
cached ceiling=60
```

may, after 30 seconds, allow one Lv70 GoldMob `+` click so the fixed formation screen can refresh Team identity.

Expected log:

```text
[rally-v19] exact Team cache is stale (123.4s; cached ceiling=60, configured ceiling=80); allowing one formation refresh probe
```

## Safety boundary

The widened ceiling authorizes only entry to the formation screen.  It does **not** authorize Attack.

The existing final fixed-slot selector remains authoritative:

```text
fresh T1=IDLE  -> Lv70 may select T1 -> fresh Attack revalidation -> dispatch
fresh T1=BUSY  -> no capable idle Team -> back out without Attack
fresh UNKNOWN  -> fail closed -> no dispatch
```

A successful fresh fixed-slot capture immediately ends the probe and refreshes the exact-cache timestamp.

If the formation screen does not become valid, the probe window expires and a 10-second retry cooldown prevents rapid repeated `+` races against the same stale cache.

The legacy two-team Rally path is unchanged.

## Build marker

A current three-team run must include:

```text
[build] JOIN-HOT-RACE-v19 stale-cache formation refresh probe loaded
```

## Regression coverage

v19 tests cover:

- a stale restrictive cache widening from 60 to the editable configured ceiling;
- the widened ceiling remaining consistent through the same Joining revalidation window;
- a cache younger than 30 seconds remaining restrictive;
- a stale all-busy cache being allowed one formation refresh probe;
- retry cooldown after an unsuccessful probe;
- a fresh cache timestamp cancelling the old probe window;
- no widening when the cached ceiling is already the configured ceiling;
- the two-team path remaining unchanged; and
- the configured ceiling being derived from the scenario selector rather than hard-coded.
