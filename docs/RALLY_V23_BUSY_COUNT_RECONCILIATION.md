# Rally v23: BUSY-count reconciliation and smoother no-match pacing

## Live failure

`pc_macro_builder(10).log` exposed a state-accounting mismatch that remained after
v22.

The fixed Team slots are the authoritative source of Team identity and
availability, but v9 still calculated a post-dispatch world-map expectation as:

```text
last observed sidebar count + 1
```

That is wrong whenever the sidebar is lagging.

At 22:41:34 the fresh fixed-slot state was effectively:

```text
T1=BUSY
T2=IDLE
T3=BUSY
```

The macro dispatched Team 2, so the authoritative post-dispatch state became:

```text
T1=BUSY
T2=BUSY
T3=BUSY
```

However the last world-map sidebar sample was still `0/3`, so the old logic armed
`expected=1/3`.  A second later the sidebar positively showed `3/3`, but v14
misclassified it as a transient value because it did not equal the incorrect
`1/3` expectation.  The v22 all-busy gate then remained active until the stale
expectation was finally discarded more than two minutes later.

The same mismatch also caused partial-busy cache churn.  A fresh fixed-slot cache
with exactly one BUSY Team could later see `1/3`; instead of treating that as
corroboration, the previous `0/3 -> 1/3` observer path could invalidate the exact
cache.  That reopened the broad max80 Rally filter and caused repeated Lv75
formation probes that freshly proved Team 1 was still BUSY.

## v23 policy

v23 reconciles world-map count cardinality with the exact fixed-Team cache.

### Confirmed dispatch

After a confirmed dispatch:

```text
expected world-map count = number of BUSY Teams in the exact fixed-slot cache
```

Examples:

```text
T1=BUSY T2=IDLE T3=IDLE -> expected 1/3
T1=BUSY T2=IDLE T3=BUSY -> expected 2/3
T1=BUSY T2=BUSY T3=BUSY -> expected 3/3
```

The sidebar is never used to identify which Team is BUSY.

### Corroborating count

If the exact cache is still valid and the world-map count equals the number of
BUSY fixed Team slots, that count corroborates the cache.

Example:

```text
exact cache: T1=BUSY T2=IDLE T3=IDLE
world map:   1/3
```

This now preserves the exact cache instead of invalidating it merely because an
older sidebar sample was `0/3`.

If the world-map count differs from the exact BUSY cardinality, v23 delegates to
the existing v12/v14/v18 stable-change policy.  A genuine Team return can still
invalidate stale identity after the existing confirmation rules.

### Full-squad behavior

If all fixed Teams are BUSY, v22 remains the hard safety gate:

```text
T1=BUSY T2=BUSY T3=BUSY
-> no Rally entry
-> no stale-cache formation probe
-> no Rally +
```

v23 only fixes the count bookkeeping behind that gate.  A positive `3/3` now
corroborates all-busy state instead of being rejected because of an incorrect
`1/3` expectation.

### No-match pacing

The live log contained hundreds of safe but visually noisy loops:

```text
open Rally
-> GoldMob exists
-> no same-row Join +
-> Back
-> immediately reopen Rally
```

v23 adds a one-second re-entry debounce after a successful Joining no-match Back
fallback.  This does not change row matching, Team limits, or final Attack
validation; it only removes the tightest open/back/reopen thrash.

The v22 hard-gate Activity message is also throttled from once per second to once
per five seconds while the gate remains active.

## Safety invariants

Unchanged:

- Team identity comes only from fixed formation-screen Team slots.
- Team limits remain Team 1 max80, Team 2 max60, Team 3 max60.
- Rally-page cache/filter state never authorizes Attack.
- Final dispatch still requires:
  1. fresh fixed Team status,
  2. capable IDLE Team,
  3. configured random delay,
  4. fresh `Attack.png` revalidation,
  5. then and only then the Attack click.
- UNKNOWN Team state remains fail-closed.
- Legacy two-team Rally behavior is unchanged.

## Startup marker

```text
[build] JOIN-HOT-RACE-v23 busy-count reconciliation loaded
```

Useful live markers:

```text
[rally-v23] dispatch expectation aligned to exact BUSY count: 1/3 -> 3/3
[rally-v23] world-map squad count 3/3 corroborates exact fixed-Team BUSY count 3/3; cache preserved
```
