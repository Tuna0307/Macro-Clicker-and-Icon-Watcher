# Rally No-Idle Recovery and Team 3 Calibration Design

## Problem

The two-team rally scenario can reach the dispatch selector with neither
eligible team recognized as idle. The selector correctly avoids clicking a
team or `Attack.png`, but it clears the carried rally level and aborts before
the trailing `set_step` cleanup actions run. `Attack Confirm` therefore stays
enabled and repeatedly reports that no carried level is available.

The latest exact-frame diagnostic also shows a genuine idle Team 3 scoring
`0.8178` against a shared idle threshold of `0.85`, producing a false negative.

## Evidence and Threshold

The current Team 3 template, region, and matching implementation were measured
against the supplied selector screenshots and known busy screenshots:

- Lowest verified genuine Team 3 idle score: `0.8173`.
- Highest verified busy Team 3 score: `0.7355`.

The two-team scenario's idle threshold will change from `0.85` to `0.80`.
This accepts every verified idle frame while retaining a measured margin of
about `0.064` above the highest busy frame. Existing score logging and
exact-frame diagnostics remain enabled so future rendering changes are
visible. The one-second post-Join wait is unchanged.

## Chosen Recovery Flow

If no eligible team reaches the idle threshold, the selector will:

1. Save the existing exact-frame diagnostic and candidate scores.
2. Clear the carried rally level and any pre-entry availability snapshot.
3. Avoid clicking either team and avoid clicking `Attack.png`.
4. Click a safe point above the dispatch panel, derived from the detected
   `Attack.png` anchor as `(anchor_x, anchor_y - 400 * scale_y)`, to dismiss
   the selector without relying on an absolute screen coordinate.
5. Abort unsafe remaining actions, but still execute the trailing `set_step`
   cleanup actions in `Attack Confirm`. The engine skips every intervening
   action type, including waits and ordinary clicks, after the abort.
6. Disable `Joining`, `Attack Confirm`, and `Back if wrong mob`, allowing the
   normal rally-icon entry steps to resume on the next usable game frame.

Cleanup must still run if the dismissal click cannot be made. Preventing the
permanent state-machine loop takes priority, and the log must identify whether
the dismissal click succeeded.

## Alternatives Considered

- Threshold-only recovery was rejected because any future miss would still
  leave `Attack Confirm` looping permanently.
- A second selector capture was rejected because it adds delay to the
  time-critical Join-to-Attack path and is unnecessary for the supplied idle
  frames at `0.80`.
- State cleanup without dismissing the selector was rejected because it could
  stop the loop while leaving the game panel open and preventing normal scans.

## Testing

Regression tests will prove that:

- The supplied low-scoring idle selector frame passes at `0.80`.
- Known busy Team 3 frames remain below `0.80`.
- A no-idle decision never clicks a team or `Attack.png`.
- The recovery click is outside and above the dispatch panel.
- Trailing `set_step` cleanup actions execute after the abort, while intervening
  waits and normal clicks do not.
- The three relevant scenario steps finish disabled and the carried state is
  cleared, preventing the repeated no-level loop.
- Successful Team 3 and Team 1 dispatch paths remain unchanged.
- Focused rally tests and the complete project test suite pass.
