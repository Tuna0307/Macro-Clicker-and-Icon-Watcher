# Rally v29 — Persistent 0/3 confirmation continuity

## Live failure

A 2026-09-05 long-run test on `Rally gold mob_ 3 team` exposed a state
continuity bug after all three Teams had been dispatched.

At `00:45:18.516` the fresh fixed Team cache correctly became:

```text
T1=BUSY T2=BUSY T3=BUSY
sidebar=2/3
expected=3/3
```

The world-map detector then repeatedly observed derived `0/3`.

v26 intentionally applies an extra three-second guard because `0/3` has no
dedicated template. That part worked. The bug was what happened after the guard
opened: v26 called `_clear_v12_zero_candidate()` before every delegated sample.

Once the unresolved confirmed-dispatch expectation reached v14/v12's existing
30-second stale horizon, the log repeatedly showed:

```text
[team-cache] candidate world-map squad change 2/3 -> 0/3; require 2s stable confirmation
```

but v24 showed the candidate age resetting to `0.00s` on every sample. The
required two-second stable confirmation could therefore never complete.

The visible game state later showed `0/3`, while the macro remained hard-blocked
by the stale exact cache:

```text
T1=BUSY T2=BUSY T3=BUSY
[rally-v22] ... Rally entry hard-blocked until return evidence
```

This was a logical deadlock, not a Python exception or crash.

## v29 policy

v29 preserves the existing evidence hierarchy.

During the first three seconds of a derived `0/3` that conflicts with exact
`BUSY/BUSY/BUSY`, the downstream v12 candidate is still cleared. After those
three seconds, v12 owns its candidate continuously; later `0/3` samples no
longer restart the confirmation timer.

If the old confirmed-dispatch expectation has already reached the existing
30-second stale horizon, v29 seeds v12's zero candidate with the original
persistent-zero observation time. This lets v12 recognize that the zero state
has already exceeded its two-second stability requirement instead of starting a
new timer after thirty seconds of identical evidence.

A positive broad `3/3` proof still clears the zero candidate immediately and
preserves the exact all-busy cache.

## Safety

v29 only allows stale exact Team identity to be invalidated after persistent
world-map return evidence. It does not infer which Team returned.

The final dispatch authority remains unchanged:

```text
fresh fixed Team 1/2/3 status
-> choose capable IDLE Team
-> configured random delay
-> fresh Attack.png revalidation
-> Attack
```

The legacy two-team Rally path is unchanged.

## Build marker

```text
[build] JOIN-HOT-RACE-v29 persistent-zero confirmation continuity loaded
```

Useful live messages:

```text
[rally-v29] 3s derived-zero guard satisfied ...; v12 candidate timer now remains continuous across samples
```

and, after the unresolved dispatch token ages out:

```text
[rally-v29] derived 0/3 remained persistent through the ...s unresolved-dispatch horizon; reusing its original observation time for v12 stable-change confirmation
```

The expected follow-up is a normal cache invalidation such as:

```text
[team-cache] invalidated (stable world-map squad count changed 2/3 -> 0/3)
```

after which the macro may resume Rally scanning. Any actual Attack still
requires the fresh fixed-slot proof described above.
