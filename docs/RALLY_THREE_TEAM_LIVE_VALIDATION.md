# Three-Team Rally Live Validation — 2026-09-03

## First live failures observed

The first real three-team run exposed two independent problems:

1. `Joining` accepted a Level 70 Rally even though the configured three-team limits were Team 1 = 65, Team 2 = 55, Team 3 = 55.
2. On the Attack/formation screen, the fixed-slot Team 1/2/3 detector returned `UNKNOWN` for all three teams and therefore used the safe back-out click instead of selecting a team and dispatching.

The game visually defaults to Team 1, so a highlighted Team 1 card is not proof that the macro selected Team 1. The macro only considers a team selected after the fixed-slot status check succeeds and the selector logs/clicks the chosen team card.

## First live fixes

### Dynamic Rally level ceiling

For explicit three-team mode (`team_priority = [3, 2, 1]`), `Joining` derives its OCR ceiling from the highest configured enabled-team maximum when the legacy two-team pre-entry availability filter is not active.

With the current live settings:

- Team 1 max = 65
- Team 2 max = 55
- Team 3 max = 55
- Joining ceiling = 65

Therefore Level 66+ rows are rejected before the `+` / Join control is clicked. Level 56–65 can only be dispatched by Team 1 at the final selection stage. Level 55 and below can use Teams 1/2/3 according to the fresh fixed-slot idle/busy state and priority.

### Live Attack/formation screen anchor

The original fixed detector validated `SquadAmount.png` only inside a narrow `(900, 480, 130, 145)` 1920×1080 reference ROI. Real screenshots proved that valid formation panels can place the same anchor lower, causing the entire status read to fail closed as `UNKNOWN`.

Three-team runtime validation uses the broader 1920×1080 reference ROI `(740, 448, 466, 299)`, matching the already-proven broad `Attack Confirm` panel area. The Team 1/2/3 ZZ slot regions and 0.90 idle threshold remain unchanged.

If the panel still cannot be proven, the runtime remains fail-closed and logs the detector error and anchor score.

## Second live failure: full-squad tray and repeated Rally entry

A later supervised run reproduced the all-squads-out case.

The uploaded log shows a successful Rally-page row decision followed by a stale `+` cancellation, then `Enter Rally after team probe` firing again while the Rally workflow was already active. In a later attempt the row `+` was successfully clicked but the game exposed only the fixed bottom squad-card tray over the map. The normal central `SquadAmount` / Attack panel never appeared, so the scenario had no condition that could advance or recover and remained stuck on that tray.

The user confirmed two important game rules:

- when the world-map counter is already `3/3`, all three squads are out and the macro must not dispatch;
- on the tray-only screen, one ordinary click outside the active team controls dismisses the interface.

The live tray screenshot shows the existing bottom `AddSquad.png` control at its proven position, no central `SquadAmount.png` anchor, and no `ZZ` glyph in any of the three fixed Team 1/2/3 status ROIs. This is the exact `BUSY / BUSY / BUSY` state.

## v7 correction

### Rally-entry latch

After the first successful world-map Rally-icon click, v7 latches entry until the current Rally workflow has genuinely exited.

While latched, `Enter Rally after team probe` cannot fire again even if the red Rally icon remains visually detectable.

The latch is released only by:

- wrong-mob / no-slot Back recovery;
- MisClick Base recovery;
- final three-team abort;
- successful Attack/dispatch;
- the new all-busy tray recovery.

This adds no fixed wait.

### Broader `3/3` gate

The historical `FullSquad3_3.png` fast check used a tiny fixed ROI around the original counter calibration. v7 keeps that fast gate and adds a second bounded left-side search band around the counter area.

A positive `3/3` suppresses Rally entry immediately. Failure to prove it remains fail-open only for this optimization; it never authorizes final dispatch.

### All-busy tray recovery

When a Rally row has already been clicked and a carried Rally level exists, v7 performs a bounded tray probe before the normal cycle:

1. validate the bottom fixed-card bar with `AddSquad.png`;
2. check the broad formation anchor for `SquadAmount.png`;
3. if `SquadAmount.png` is present, leave normal `Attack Confirm` behavior untouched;
4. if the tray is present but the formation anchor is absent, read the three fixed `TeamIdleZZ.png` ROIs;
5. require Team 1 = BUSY, Team 2 = BUSY, Team 3 = BUSY;
6. click a neutral padded tray area once to dismiss;
7. never click Team 1/2/3 and never click Attack;
8. clear transient Rally state and resume the 30–50 ms hot loop on the next cycle.

Any `IDLE` or `UNKNOWN` tray state fails closed and does not run this recovery click.

The tray probe uses bounded bottom/formation captures instead of a full-screen template scan.

## Screenshot regression evidence

The existing committed fixed-slot matrix remains:

- `204523` → IDLE / IDLE / IDLE
- `203451` → IDLE / BUSY / IDLE
- `211656` → BUSY / IDLE / IDLE
- `211753` → BUSY / IDLE / BUSY
- `211946` → IDLE / BUSY / BUSY
- `212029` → BUSY / BUSY / BUSY
- `212218` → IDLE / IDLE / BUSY
- `211912` world-map-only → UNKNOWN / UNKNOWN / UNKNOWN

v7 reuses the committed `212029` BUSY / BUSY / BUSY fixture to test the tray-only classifier without adding another multi-megabyte screenshot. The test masks only the central formation-anchor band while preserving the real fixed bottom-card pixels, then verifies:

- bottom `AddSquad.png` anchor remains present;
- central formation anchor is absent;
- Team 1/2/3 fixed ZZ slots all resolve BUSY.

The supervised 2026-09-03 screenshot was also checked directly during implementation: `AddSquad.png` scored 1.000 in the bottom band, the central `SquadAmount.png` score stayed below the formation threshold, and all three ZZ slot scores remained below the idle threshold.

## Safety contract

A positively proven `3/3` must never dispatch.

If the early count check misses and the macro still reaches the tray-only state, `BUSY / BUSY / BUSY` is a second independent no-dispatch guard. The recovery path contains no team-card input and no Attack input.

The normal formation path remains fail-closed: only a positively validated formation screen plus an exact `IDLE` team that satisfies the Rally level limit may dispatch.

## Validation target

The v7 focused tests cover:

- entry latch activation and release;
- blocked re-entry while latched;
- shifted/broader `3/3` detection;
- all-busy fixed-card classification with the formation anchor absent;
- normal formation not being mistaken for tray recovery;
- tray recovery short-circuiting the normal cycle.

The repository still carries the pre-existing historical Auto Gather expectation mismatch documented in earlier validation. Any CI result must distinguish that known failure from new Rally regressions.
