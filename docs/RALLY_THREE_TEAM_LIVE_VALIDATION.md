# Three-Team Rally Live Validation — 2026-09-03

## Live failures observed

The first real three-team run exposed two independent problems:

1. `Joining` accepted a Level 70 Rally even though the configured three-team limits were Team 1 = 65, Team 2 = 55, Team 3 = 55.
2. On the Attack/formation screen, the fixed-slot Team 1/2/3 detector returned `UNKNOWN` for all three teams and therefore used the safe back-out click instead of selecting a team and dispatching.

The game visually defaults to Team 1, so a highlighted Team 1 card is not proof that the macro selected Team 1. The macro only considers a team selected after the fixed-slot status check succeeds and the selector logs/clicks the chosen team card.

## Fixes

### Dynamic Rally level ceiling

For explicit three-team mode (`team_priority = [3, 2, 1]`), `Joining` now derives its OCR ceiling from the highest configured enabled-team maximum when the legacy two-team pre-entry availability filter is not active.

With the current live settings:

- Team 1 max = 65
- Team 2 max = 55
- Team 3 max = 55
- Joining ceiling = 65

Therefore Level 66+ rows are rejected before the `+` / Join control is clicked. Level 56–65 can only be dispatched by Team 1 at the final selection stage. Level 55 and below can use Teams 1/2/3 according to the fresh fixed-slot idle/busy state and priority.

### Live Attack/formation screen anchor

The original fixed detector validated `SquadAmount.png` only inside a narrow `(900, 480, 130, 145)` 1920×1080 reference ROI. Real screenshots proved that valid formation panels can place the same anchor lower, causing the entire status read to fail closed as `UNKNOWN`.

Three-team runtime validation now uses the broader 1920×1080 reference ROI `(740, 448, 466, 299)`, matching the already-proven broad `Attack Confirm` panel area. The Team 1/2/3 ZZ slot regions and 0.90 idle threshold remain unchanged.

If the panel still cannot be proven, the runtime remains fail-closed and now logs the detector error and anchor score.

## Screenshot regression evidence

The user committed the real screenshots under `tests/Test Picture/`. Automated regression coverage now verifies:

- `204523` → IDLE / IDLE / IDLE
- `203451` → IDLE / BUSY / IDLE
- `211656` → BUSY / IDLE / IDLE
- `211753` → BUSY / IDLE / BUSY
- `211946` → IDLE / BUSY / BUSY
- `212029` → BUSY / BUSY / BUSY
- `212218` → IDLE / IDLE / BUSY
- `211912` world-map-only → UNKNOWN / UNKNOWN / UNKNOWN

The same regression also proves a Level 70 Rally is ineligible under the current 65 ceiling while Level 65 remains eligible for final team selection.

## Validation

GitHub Actions on code/test HEAD `b1f6529df9c79bd5269f96e5cf4d596bb9f748a9` completed with **576 passed, 1 failed**. The only failure is the previously documented historical Auto Gather mismatch: the test expects 12 level-plus clicks while the restored historical scenario contains 15. No new Rally test failed.

The existing two-team Rally path is intentionally left unchanged by these live three-team runtime corrections.
