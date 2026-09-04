# Three-Team Rally v16 Lv80+ GoldMob Live Validation — 2026-09-04

## New game artwork confirmed

A user-confirmed Rally screenshot shows that the gold zombie/mob changes appearance from level 80 onward. The existing three-team Rally scenario identifies desired rows with only `templates/GoldMob.png`, which represents the lower-level artwork.

The Lv80 screenshot still shows the same gold-mob Rally type, but the mob portrait is a different zombie riding/standing on a gold vehicle. Without an alternate identity match, the macro can treat a valid Lv80 gold Rally as the wrong mob even when Team 1 is configured to accept level 80.

## v16 behavior

v16 keeps the existing `GoldMob.png` match as the first/authoritative test. Only when that positive GoldMob condition misses in explicit three-team mode does it test the user-confirmed Lv80+ artwork.

The alternate match is returned through the same GoldMob condition index. Therefore existing behavior remains intact:

- v9 Rally-entry progress recognizes the page as valid progress;
- `Joining` treats the detected row as a GoldMob row;
- v15 searches `Join.png` only in the same row band;
- level OCR still reads the actual `Lv.xx` text and applies the available-team ceiling;
- a level above the current ceiling is still rejected before the `+` click;
- final Team selection still comes from the fixed Team 1/2/3 slots; and
- Attack still requires fresh final confirmation.

The legacy two-team path is intentionally unchanged.

## Important distinction

v16 does **not** mean that every level 80 rally is gold.

The row must visually match either:

1. the original lower-level `GoldMob.png`; or
2. the confirmed Lv80+ gold-mob artwork.

Only after that visual identity proof does the existing OCR/team-limit logic decide whether the row is eligible.

## Expected logs

A current build must include:

```text
[build] JOIN-HOT-RACE-v16 high-level GoldMob variant loaded
```

When the alternate artwork is the one that matched, the Activity log should include:

```text
[rally-v16] Lv80+ GoldMob artwork matched; treating row as GoldMob
```

For the currently committed Team limits (`T1=80`, `T2=60`, `T3=60`), a fresh all-idle state may therefore accept Lv80 for Team 1. If Team 1 is already BUSY and the remaining ceiling is 60, the exact same Lv80 gold row must still be rejected before any Join `+` click.

Example accepted path when Team 1 is available:

```text
[rally-v16] Lv80+ GoldMob artwork matched; treating row as GoldMob
[level] ... read 80
[level] ... within available-team max 80 => accepted
```

Example rejected path when only max60 Teams remain:

```text
[rally-v16] Lv80+ GoldMob artwork matched; treating row as GoldMob
[team-cache] using known fixed-team availability; Rally-row ceiling=60
[level] ... read 80
[skip] ... 80 > available-team max 60
[skip] 'Joining' no valid matching row target
[no-match] click condition #2 (...BackButton...)
```

There must be no last-slot `+` click in the rejected case.

## Regression coverage

v16 tests protect these rules:

- the embedded user-confirmed Lv80+ artwork decodes correctly;
- a normal `GoldMob.png` hit remains authoritative and skips the alternate check;
- an original miss can be satisfied by the Lv80+ artwork in three-team mode;
- an alternate miss remains a normal failure;
- non-GoldMob conditions are unchanged;
- negated/competing GoldMob conditions are unchanged; and
- the two-team path is unchanged.
