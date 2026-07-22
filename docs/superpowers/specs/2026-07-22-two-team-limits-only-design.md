# Two-Team Limits as the Sole Routing Authority

**Date:** 2026-07-22

## Goal

Make the visible Team 1 and Team 3 maximum-level fields the only level
authority whenever smart two-team rally routing is configured. Preserve the
ordinary row maximum for the existing one-team scenario.

## Scope

This change applies only to a `click_matching_row` action configured with the
Team 1 and Team 3 busy-status prefilter and paired with a
`select_rally_team` action.

The one-team `Rally Gold Mob` scenario keeps its ordinary row `max_level`
filter. Removing the one-team mode is explicitly deferred until the two-team
mode has passed live game testing.

## User-Facing Rules

### Two-team mode

- Team 1 and Team 3 maximum fields are the only maximum-level controls.
- A blank team maximum means that team accepts any detected mob level.
- Team 3 remains preferred whenever it is idle and accepts the detected level.
- Team 1 remains the immediate fallback and accepts every level within its own
  configured range.
- An old row/global maximum must not clamp either team maximum.
- Old hidden Team 1 or Team 3 values stored on the row action must not affect
  routing.

Examples:

| Team 1 | Team 3 | Detected level | Idle teams | Result |
|---:|---:|---:|---|---|
| 65 | 50 | 45 | Both | Team 3 |
| 65 | 50 | 55 | Both | Team 1 |
| 65 | 50 | 50 | Team 3 only | Team 3 |
| 65 | 50 | 55 | Team 3 only | Reject row |
| 30 | 55 | 50 | Both | Team 3 |
| blank | 55 | 80 | Team 1 only | Team 1 |
| 65 | blank | 80 | Team 3 only | Team 3 |

### One-team mode

- The ordinary row `max_level` remains visible, editable, and enforced.
- No Team 1/Team 3 selector is required.

## Runtime Design

### Smart availability prefilter

For a smart two-team row action:

1. Capture Team 1 and Team 3 availability once before entering the rally row.
2. Resolve limits from the scenario's sole `select_rally_team` action.
3. Build eligibility from idle teams only.
4. If any idle eligible team has a blank maximum, the effective cap is
   unbounded.
5. Otherwise, the effective cap is the highest maximum among idle teams.
6. Do not combine that cap with the row action's `max_level`.
7. Cache and reuse the exact availability snapshot after the rally page
   changes.

The row-level comparison treats a smart team cap as a replacement for the
ordinary row maximum, not a value to combine with it. The ordinary row maximum
is used only when smart availability is absent.

### OCR requirement

Two-team final dispatch always requires the detected mob level, including when
both team maximums are blank. Therefore:

- OCR warm-up recognizes the presence of smart team selection even when the
  row action has no ordinary minimum or maximum.
- Smart row matching still reads and carries the detected level when the
  effective team cap is unbounded.
- This changes configuration authority only; it adds no second OCR read,
  screen capture, or delay.

### Final team selection

The existing final selection order remains Team 3, then Team 1. Each candidate
is filtered solely by its visible maximum. A blank value is unlimited.

## Validation and Migration

- A scenario using the smart Team 1/Team 3 availability prefilter must contain
  exactly one `select_rally_team` action.
- Zero selectors is a configuration error for smart two-team mode; there is no
  legacy hidden-limit fallback.
- Multiple selectors remain a configuration error because their authority is
  ambiguous.
- Old scenario files containing row-action `team1_max_level` or
  `team3_max_level` keys may still be parsed so files open safely, but those
  values are ignored for row routing.
- Team maximum model defaults become blank rather than fixed at 65/45.
- Saving a smart row action clears its obsolete row `max_level`,
  `team1_max_level`, and `team3_max_level` values.
- The saved two-team scenario stores blank/null values for those three obsolete
  row fields while preserving visible selector values Team 1 = 65 and
  Team 3 = 50.

## Interface Design

- The `Select rally team` editor remains the only place to edit Team 1 and
  Team 3 maximums.
- Its existing guidance continues to explain that these fields control Joining
  OCR eligibility and that the application's main Save button persists them.
- When editing a smart `click_matching_row` action, its ordinary maximum field
  is disabled and presented as controlled by Team 1/Team 3 settings.
- Ordinary one-team row actions retain the editable maximum field unchanged.

## Diagnostics

Availability diagnostics continue to record:

- Team 1 and Team 3 busy scores and states;
- the resolved visible limits;
- selector step/action identity;
- effective cap, including the JSON-safe `unbounded` state.

They must not report obsolete row values as active constraints.

## Testing

Required regressions include:

- a stale row maximum below a visible team maximum does not clamp two-team
  eligibility;
- a visible team maximum above 65 is accepted without changing another field;
- blank Team 1 or Team 3 maximum is unlimited;
- fully unbounded routing performs one availability capture and still carries
  the OCR level to final selection;
- smart two-team validation requires exactly one selector;
- obsolete hidden row values have no runtime effect and are cleared on save;
- the supplied two-team scenario persists row values as blank and selector
  values as 65/50;
- the original one-team scenario still enforces its ordinary maximum;
- the same-engine row-to-selector handoff and Team 3 priority remain intact;
- full pytest, Ruff, and mypy checks pass.

## Non-Goals

- Removing the one-team scenario before live two-team testing succeeds.
- Adding per-team minimum-level fields.
- Changing OCR recognition, team portrait matching, click coordinates, or
  dispatch timing.
- Adding another capture or OCR pass.
