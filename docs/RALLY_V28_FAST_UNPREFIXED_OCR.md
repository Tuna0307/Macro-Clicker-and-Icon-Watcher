# Rally v28 — Fast Safe Unprefixed OCR Consensus

## Live symptom

A v27 three-team run proved that the entry-path optimization worked: the first
`Joining` scan began about 0.30 seconds after the world-map Rally-icon click.

One separate row still looked frozen. A Lv85 GoldMob row reached the normal
team-level ceiling check, then the OCR result did not arrive for about 16
seconds. The final read was high-confidence text `85` without a literal `Lv`
prefix. The row was safely rejected because 85 exceeded the available-team
maximum of 80.

## Cause

`LevelOcrReader` deliberately does not trust one bare number. A strong literal
`Lv`/`Level` result can use the one-read fast path, but a high-confidence bare
number is provisional until another independent OCR observation agrees.

That safety rule was correct. The performance issue was fallback ordering.
The fallback variants were grouped per crop as:

```text
plain
sharpened
threshold
```

before moving to the next crop. On the live bare `85`, this could force many
expensive recognition passes before a second independent plain crop confirmed
the same number.

## v28 behavior

Only while an explicit three-team Rally row is being read, v28 reorders the
already-existing fallback variants so independent plain crops are tried first.
If they do not establish safe consensus, every original sharpened and threshold
variant remains available afterward.

The fast crop itself remains in the position the core reader expects and is
still skipped as a duplicate during fallback.

The acceptance policy is unchanged:

- one unprefixed number remains provisional;
- repeated high-confidence agreement is still required;
- prefixed `Lv`/`Level` handling is unchanged;
- uncertain/conflicting OCR still fails closed.

Legacy two-team Rally does not opt into the reordering.

## Expected logs

Startup:

```text
[build] JOIN-HOT-RACE-v28 fast unprefixed OCR consensus loaded
```

Only when the uncertain/unprefixed fallback is actually used:

```text
[rally-v28] unprefixed/uncertain level OCR used plain-crop-first consensus ordering; row read completed in 0.xxxs
```

A bare Lv85 remains subject to the same level ceiling and should still end as
something equivalent to:

```text
[level] ... read 85 ... text='85'
[skip] ... level read 85; 85 > available-team max 80
```

The goal is to remove the long OCR stall, not to make a bare number easier to
accept.

## Safety invariants unchanged

v28 does not change:

- GoldMob identity or the Lv80+ artwork variant;
- same-row Join `+` pairing;
- Team 1/2/3 level limits;
- fixed-slot Team identity;
- stale Team-cache/probe policy;
- the final Join `+` micro-revalidation;
- Team selection;
- the configured final dispatch delay; or
- fresh `Attack.png` revalidation.

Final Attack remains fail closed.
