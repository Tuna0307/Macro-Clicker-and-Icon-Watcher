# Rally v25: verified all-busy tray dismissal

## Live failure

`logs(2).zip` from the 2026-09-04 v24 test captured the macro getting stuck on
the world map with the bottom fixed squad tray still open.

The decisive sequence was:

```text
23:15:58.361 click matching row
23:15:59.419 all-busy tray candidate observed
23:15:59.873 fixed squad tray shows T1=BUSY T2=BUSY T3=BUSY;
             dismissed at (-702, 1045) without dispatch
23:15:59.874 stable all-busy tray confirmed after transition guard
```

The screenshot taken afterward still showed the bottom tray open.

v7 treated a successful mouse API call as if it proved the tray had closed.  It
then immediately cleared the Rally workflow and released the entry latch.

The v24 trace made the resulting dead state explicit:

```text
latch=0
pending_level=none
cache=INVALID
sidebar=0/3
base_arm=1
```

For more than a minute the normal entry step stayed blocked while `MisClick Base`
kept checking but never fired.  This UI is a squad tray, not the Base popup that
the existing recovery step recognizes.

## Coordinate bug

The old recovery point is:

```text
TRAY_DISMISS_REFERENCE_POINT = (1218, 1045)
```

but the fixed tray anchor region is:

```text
TRAY_ANCHOR_REGION = (650, 880, 630, 200)
```

which spans:

```text
x = 650..1280
y = 880..1080
```

Therefore `(1218, 1045)` is **inside the tray**.

On the live 1920x1080 game window this produced the logged global click
`(-702, 1045)`.  On the user's scaled screenshot it lands in the lower-right
portion of the brown tray, so the click can succeed without dismissing the UI.

## v25 behavior

v25 keeps the existing v10 transition grace and stable BUSY/BUSY/BUSY proof.
Only the final dismissal phase changes.

The outside-tray candidates are:

```text
(600, 1045)   # left of tray
(1320, 1045)  # right of tray
(960, 820)    # above tray
```

For a 1920x1080 window whose left edge is `-1920`, the first two become:

```text
(-1320, 1045)
(-600, 1045)
```

The recovery sequence is now:

```text
stable all-busy tray
-> click a positively outside-tray point
-> KEEP workflow/latch active
-> fresh tray capture
-> if tray remains: log failure and try alternate point
-> if tray anchor disappears once: wait for another fresh absence capture
-> only after repeated closure evidence:
   clear transient Rally state
   release latch
```

A successful `_click_point()` return value is no longer treated as closure proof.

## Risky map click recovery

Any outside-tray click can land on a map entity.  v25 therefore arms the
already-existing bounded Base and Profile recovery gates around each outside
click.

Those recovery paths still require their own positive templates.  No blind Base
or Profile dismissal is added.

## Expected live logs

Startup:

```text
[build] JOIN-HOT-RACE-v25 verified tray dismissal loaded
```

First outside attempt:

```text
[rally-v25] all-busy tray outside-dismiss attempt 1 clicked ...;
workflow remains latched pending fresh tray-closure proof
```

If the first point does not close it:

```text
[rally-v25] tray still positively present ... after outside-dismiss attempt 1;
trying alternate outside point
```

When closure starts to be observed:

```text
[rally-v25] tray anchor absent on first fresh verification; ...;
waiting for second absence proof
```

Final confirmation:

```text
[rally-v25] tray closure confirmed across fresh captures (...);
workflow/latch released
```

The old unsafe sequence should no longer occur:

```text
click returned True
-> immediately claim "dismissed"
-> clear workflow
-> tray actually remains visible
```

## Safety invariants

Unchanged:

- Team identity comes only from fixed Team slots.
- Team limits remain Team 1 max80, Team 2 max60, Team 3 max60.
- An all-busy tray never dispatches.
- UNKNOWN Team state remains fail-closed.
- Final Attack still requires fresh fixed Team status, a capable IDLE Team,
  configured random delay, and fresh `Attack.png` revalidation.
- Legacy two-team Rally behavior is unchanged.
