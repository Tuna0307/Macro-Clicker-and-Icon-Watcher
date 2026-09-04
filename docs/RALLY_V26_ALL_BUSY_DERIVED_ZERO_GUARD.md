# Rally v26: protect exact all-busy state from false derived 0/3

## Live contradiction

The same `logs(2).zip` run that exposed the tray-dismissal bug also exposed an
upstream count-evidence bug.

At 23:15:53 the macro had:

```text
exact fixed Team cache:
T1=BUSY T2=BUSY T3=BUSY

world map:
3/3
```

v23 correctly logged that the `3/3` sidebar corroborated the exact BUSY
cardinality.

Immediately afterward the derived `0/3` detector began reporting zero.  Because
there is no dedicated `0/3` repository template, zero is inferred from:

```text
"/3" suffix visible
AND
1/3 not matched
AND
2/3 not matched
AND
3/3 not matched
```

After the existing two-second v12 zero debounce, that weak negative inference
invalidated the exact all-busy cache:

```text
23:15:57.043 [team-cache] invalidated
(stable world-map squad count changed 3/3 -> 0/3)
```

The macro immediately entered Rally.

Five seconds later the fresh fixed tray again proved:

```text
T1=BUSY T2=BUSY T3=BUSY
```

No dispatch occurred between those observations, so the `3/3 -> 0/3` transition
was false evidence.

## v26 policy

Only this specific contradiction receives additional protection:

```text
exact cache = BUSY/BUSY/BUSY
observed world count = derived 0/3
```

The derived zero must now survive an extra three-second guard before it is
released to v12's existing two-second stable zero confirmation.

Therefore a pure all-busy `3/3 -> derived 0/3` invalidation takes about five
seconds of persistent evidence instead of about two seconds.

If v7's bounded broad detector positively sees `3/3` during that guard, the
derived zero is cancelled immediately and the exact cache is preserved.

## Explicit return evidence remains fast

Positive explicit templates are unchanged:

```text
3/3 -> 2/3
3/3 -> 1/3
```

Those values still enter the existing v12 stable-change path immediately.

So when a Team actually returns and an explicit count is visible, v26 adds no
extra delay.

Only the weak derived-zero case gets the additional guard.

## Expected logs

Startup:

```text
[build] JOIN-HOT-RACE-v26 all-busy derived-zero guard loaded
```

First contradictory zero:

```text
[rally-v26] derived 0/3 conflicts with exact T1=BUSY T2=BUSY T3=BUSY;
starting extra return-evidence guard before v12 confirmation
```

While held:

```text
[rally-v26] holding derived 0/3 contradiction for ...;
exact all-busy cache preserved
```

If broad 3/3 proves the cache is still correct:

```text
[rally-v26] derived 0/3 contradicted by positive broad 3/3
while exact Teams are all BUSY; ignored and cache preserved
```

Only after persistent zero evidence:

```text
[rally-v26] derived 0/3 persisted ...;
releasing it to normal v12 stable-change confirmation
```

## Safety invariants

Unchanged:

- world-map count never identifies Team identity;
- fixed Team slots remain authoritative for Team 1/2/3;
- all-busy state hard-blocks Rally entry and Rally `+`;
- UNKNOWN remains fail-closed;
- final Attack requirements are unchanged;
- legacy two-team Rally behavior is unchanged.
