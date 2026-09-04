# Three-Team Rally v15 Multi-Row No-Slot Live Validation — 2026-09-04

## Live failure reproduced

A supervised three-team run exposed a Rally-page stall when a second, non-gold rally appeared above the desired GoldMob rally.

The visible layout had two rows:

- an unrelated Lv30 rally on the upper row with open `+` slots; and
- the desired gold Lv60 rally on the lower row with no available Join `+`.

The desired GoldMob therefore moved from the normal first-row location around `y=283` to the second-row location around `y=560`.

The uploaded log repeatedly showed:

```text
[join-check] BLOCKED at LastSlot+ | GoldMob=YES n=1 y=[560] | LastSlot+=NO
```

The Rally page remained open for several seconds before a slower recovery eventually fired.

## Root cause

The engine already performs row-local target matching for `click_matching_row`:

1. find every desired GoldMob reference row;
2. build a bounded y-band around those GoldMob rows using the configured row tolerance; and
3. search `Join.png` only inside those bands.

That row-local behavior was correct and safely ignored the upper non-gold rally's `+` controls.

The bug was the generic AND-condition gate. `Joining` required the target condition to be true before the `click_matching_row` action could execute. When the row-local search correctly returned no Join target near the lower GoldMob row, the step returned `BLOCKED at LastSlot+` immediately. This prevented the action from reaching its already-existing `no_eligible_row -> BackButton.png` fallback.

## v15 correction

v15 installs after v14 and wraps only the row-local target-evaluation boundary for explicit three-team `Joining`.

When:

- the scenario is explicit three-team Rally;
- the step is `Joining`;
- a GoldMob reference row has been positively matched; and
- the original row-local Join search returns zero targets;

v15 returns `pass-with-no-target` for that target condition.

This does not create a fake Join target. It only lets the step continue far enough for `click_matching_row` to see an empty selection set and run the existing no-match Back fallback.

Expected sequence:

```text
[rally-v15] GoldMob found but no same-row Join +; routing to no-match Back refresh
[fire] Joining
[join-rows] GoldMob y=[560]; LastSlot+ y=[]; mob_y=560->plus_y=[]
[skip] 'Joining' no valid matching row target
[no-match] click condition #2 (...BackButton...)
[rally-v13] no-match Back completed; Rally entry latch released for world-map refresh
[fire] Enter Rally after team probe
```

## Safety properties

- A `+` belonging to the upper non-gold rally is never substituted for the lower GoldMob row.
- The original row-local Join search remains authoritative.
- If a valid same-row Join target exists, v15 leaves the original success result untouched.
- If no GoldMob reference exists, v15 leaves the original failure untouched.
- Unrelated steps are unchanged.
- The legacy two-team path is unchanged.
- Final Team selection and Attack safety are unchanged.

## Build marker

A current explicit three-team run must include:

```text
[build] JOIN-HOT-RACE-v15 multi-row no-slot Back routing loaded
```

## Regression coverage

v15 tests cover:

- GoldMob reference + zero same-row Join targets -> pass-with-no-target;
- no non-gold/global target is introduced by v15;
- an existing valid same-row Join result remains unchanged;
- missing GoldMob references remain blocked;
- two-team behavior remains unchanged; and
- unrelated steps remain unchanged.
