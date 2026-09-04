# Rally v32: Bounded cross-crop level OCR

## Live failure

A 2026-09-05 v30/v28 run reached a valid Lv80+ GoldMob row:

```text
[rally-v16] Lv80+ GoldMob artwork matched; treating row as GoldMob
GoldMob=YES
LastSlot+=YES
row-cap=80
```

The level crop eventually returned:

```text
paddleocr_rec read 80 conf=0.97 text='80'
[rally-v28] ... row read completed in 18.649s
level read 80; within ... max 80 => accepted
last-slot + vanished before input; stale click cancelled
```

The safety result was correct: the Join button was freshly revalidated and no
stale click was sent. The performance was not acceptable.

The expensive part was inside one OCR crop. A bare `80` cannot be trusted as a
single strong result, so the OCR reader walked many sharpened/threshold variants
inside that crop looking for corroboration.

## Existing stronger structure

`RallyMatchingMixin._read_level_for_row()` already captures up to six independent
vertical crops of the same row. It already treats sub-strong results as
provisional and requires at least two matching provisional crops before accepting
a level.

v32 uses that existing cross-crop consensus rather than spending many seconds
trying to build consensus inside one crop.

## v32 behavior

For explicit three-team Rally rows only:

- literal high-confidence `Lv80` / `Level 80` remains immediately strong;
- high-confidence bare `80`, corrected-prefix text such as `ly80`, or another
  acceptable but non-fast result is capped below the strong threshold and sent
  to the outer crop-consensus path;
- low-confidence single-crop numbers stay unreadable;
- at least two matching provisional crops are still required before a bare
  number can be accepted.

All original level limits remain unchanged. The fresh same-row `+` revalidation
still runs immediately before input. Final Attack still requires fresh fixed Team
status, a capable IDLE Team, the configured delay, and fresh `Attack.png`.

The legacy two-team Rally path is unchanged.

## Startup marker

```text
[build] JOIN-HOT-RACE-v32 bounded cross-crop level OCR loaded
```

## Diagnostic timing

Every three-team row read now logs:

```text
[rally-v32] cross-crop level OCR result=80 elapsed=...s literal=... provisional=... unread=...
```

For the problematic bare-80 case, the expected healthy pattern is multiple cheap
provisional crops reaching consensus in roughly the low-single-second range
rather than an 18-second inside-one-crop fallback. If crops disagree, the row
still fails closed.
