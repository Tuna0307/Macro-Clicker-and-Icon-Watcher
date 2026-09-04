# Rally v22: Full-squad hard gate

## Live failure

The 2026-09-04 long run showed that the macro could still try a Rally row `+`
while all three squads were already out.

The important sequence was:

```text
21:50:33.649 confirmed dispatch => T2=BUSY
21:50:34.100 broad 3/3 gate: all squads out; Rally entry suppressed
21:50:35.153 Enter Rally after team probe
...
21:50:35.759 Rally-row ceiling=none
```

Later the exact cache still represented no capable idle Team, but v19/v21 treated
that old all-busy cache as stale and widened it for a formation refresh probe:

```text
21:50:55.565 cached ceiling=none, configured ceiling=80; allowing one formation refresh probe
21:50:55.748 click matching row
21:50:57.254 fixed squad tray shows T1=BUSY T2=BUSY T3=BUSY
```

A second problem was detector flicker: a positive broad `3/3` scan could suppress
one Rally-entry evaluation, then the next evaluation could miss the template and
allow the normal entry step a fraction of a second later.

## v22 policy

Positive evidence that all squads are out is now a hard gate.

Any of these blocks both world-map Rally entry and Rally-row `+` probing:

- exact fixed-Team cache is `T1=BUSY T2=BUSY T3=BUSY`;
- stable world-map squad count is `3/3`;
- a recent positive broad `3/3` detection is still inside the short anti-flicker hold.

The all-busy exact cache is **not** eligible for v19/v21 stale-cache widening.
That refresh mechanism remains available only when at least one exact cached Team
is IDLE but the remaining level ceiling is narrower than the configured ceiling.

The broad `3/3` detector also gets a 2-second sticky hold so one transient
template miss cannot immediately undo a just-proven full-squad gate.

## Safety invariants unchanged

v22 does not change final Attack authorization. A dispatch still requires:

```text
fresh fixed Team status
-> capable IDLE Team
-> configured delay
-> fresh Attack.png revalidation
-> Attack
```

UNKNOWN is never eligible. The legacy two-team Rally path is unchanged.

## Expected live markers

Startup:

```text
[build] JOIN-HOT-RACE-v22 full-squad hard gate loaded
```

When exact all-busy or `3/3` evidence blocks entry:

```text
[rally-v22] ...; Rally entry hard-blocked until return evidence
```

If the macro is already on a Rally page and a row tries to use stale-cache
widening while full-squad evidence exists:

```text
[rally-v22] ...; stale-cache formation probe blocked, no Rally + allowed
```
