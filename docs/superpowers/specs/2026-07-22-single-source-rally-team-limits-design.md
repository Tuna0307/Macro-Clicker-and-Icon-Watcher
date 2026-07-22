# Single-Source Rally Team Limits Design

## Problem

The two-team scenario stores Team 1 and Team 3 maximum levels on both the
`Joining` row-click action and the final `Select rally team` action. Only the
final selector values are visible in the editor. Changing Team 3 from 45 to 50
there leaves the hidden Joining value at 45, so OCR correctly reads level 50
but rejects it before `Join.png` or team selection.

The global Joining maximum (`max_level`, currently 65) is a separate safety
ceiling and remains valid.

## Approaches Considered

1. Copy selector values into Joining whenever the editor saves. This retains
   two sources that can drift through JSON edits or older files.
2. Add another pair of controls to the Joining editor. This exposes the
   duplication instead of removing it and requires users to edit four values.
3. Make `Select rally team` authoritative at runtime and retain the row fields
   only as compatibility fallback. This is the chosen approach because one
   visible edit controls the entire flow without slowing detection.

## Runtime Design

Add a rally-matching helper that resolves Team 1 and Team 3 limits:

- Search the current scenario for its `select_rally_team` action.
- If found, return that action's Team 1 and Team 3 maxima.
- If absent, return the current row action's stored maxima for compatibility.

The map availability prefilter and row OCR filter use these resolved values.
The final selector already uses its own values, so both stages then share one
authority. The global Joining `max_level` remains an additional upper bound.

## Editor and Persistence

The existing visible Team 1 and Team 3 Max level fields remain the only team
range controls. Add explanatory text that they also control Joining-stage OCR
eligibility. Action-dialog Save updates the running in-memory scenario; the
main toolbar Save remains required to persist changes across application
restarts.

## Diagnostics

Availability metadata records the resolved team-level limits and their source
(`select_rally_team` or legacy row fallback), making future configuration
problems visible in logs.

## Testing

- Set the visible selector Team 3 maximum to 50 while leaving the hidden row
  value at 45; with Team 1 busy and Team 3 idle, the prefilter must return 50.
- Verify global Joining maximum still rejects a level above 65.
- Verify scenarios without a selector retain their row-action limits.
- Verify final dispatch selects Team 3 for level 50 when its visible maximum is
  50 and its idle template passes.
- Run the complete test, lint, and type-check suites.
