# Rally v30: All-busy count fallback watcher

## Live failure

A 2026-09-05 v29 run was stopped and started several times, but every restart
cleanly reset the runtime state.  The final freeze was not caused by state leaking
across Stop/Start.

The final run instead reached a valid all-busy cache after dispatching the last
idle Team.  Normal world-map count polling then stopped receiving samples for
about 113 seconds.  v12 intentionally requires `RallyIcon.png` to be visible
before reading the tiny squad-count ROI, so a world-map UI state that hid that
icon starved the all-busy cache of return evidence.  v22 therefore continued to
hard-block Rally even though later sidebar evidence showed that squads were
returning.

## v30 behavior

v30 keeps the normal RallyIcon-gated count poll as the primary path.  It only
adds a fallback when all of the following are true:

- explicit three-team Rally mode;
- exact fixed-slot cache is `T1=BUSY T2=BUSY T3=BUSY`;
- no Rally workflow is latched;
- no row-to-formation transition guard is active; and
- the normal count path has produced no valid count sample for at least 2 seconds.

The fallback sends **no input**.  It reads the same tiny squad-count ROI directly
and sends the observation to the existing v29/v12 confirmation logic.

- explicit `1/3` or `2/3` remains return evidence only after the existing stable
  confirmation policy;
- `3/3` corroborates the all-busy state;
- derived `0/3` remains weak evidence and still passes through v29's additional
  guard before it may invalidate stale Team identity.

Invalidating the stale cache never identifies which Team returned.  Any later
Attack still requires a fresh fixed Team 1/2/3 capture, a capable IDLE Team,
the configured dispatch delay, and a fresh `Attack.png` revalidation.

The legacy two-team path is unchanged.

## Expected startup marker

```text
[build] JOIN-HOT-RACE-v30 all-busy count fallback watcher loaded
```

## Expected recovery trace

When the normal RallyIcon-gated path has gone silent while all Teams are cached
BUSY:

```text
[rally-v30] normal count polling silent for 2.xx s; direct tiny-ROI fallback observed 1/3 (no input sent)
```

For derived zero:

```text
[rally-v30] normal count polling silent for 2.xx s; direct tiny-ROI fallback observed 0/3 (no input sent); 0/3 remains derived evidence under v29 guard
```

The subsequent v29/v12 logs should show the normal guard/confirmation process.
