# Rally Busy-State Hysteresis and Recovery Design

## Problem

After Team 1 joined a level-55 rally, the map prefilter first detected Murphy
as busy with score `1.00`. A later transition frame scored `0.70`, below the
configured busy threshold `0.85`, so the prefilter incorrectly treated Murphy
as idle and allowed a level-50 rally. The final dispatch panel correctly found
Murphy unavailable and correctly excluded Team 3 because level 50 is above its
maximum of 45, but the failed selector retried the same step indefinitely.

## Chosen Approach

Use asymmetric score hysteresis for map-side busy detection. A team that was
previously busy remains busy while its score is at or above a conservative
release threshold of `0.50`. A team that was previously idle still requires the
normal configured threshold (`0.85`) to become busy. This handles transition
frames without adding another screen capture or delaying rally entry.

Alternatives rejected:

- Lowering the normal busy threshold globally would make every first-time scan
  more vulnerable to false positives.
- Requiring two captures would add latency to the time-critical entry path.

## Recovery Flow

When the final dispatch selector has no eligible idle team, it must:

1. Save the existing exact-frame diagnostic.
2. Clear the carried rally level.
3. Abort the remaining actions in `Attack Confirm`, so it cannot click
   `Attack.png` without a selected team.
4. Allow the scenario's existing `Back if wrong mob` step to run instead of
   retrying `Attack Confirm` forever.

The normal successful flow and Team 3 maximum level of 45 remain unchanged.

## Testing

- Reproduce the observed score sequence: busy `1.00`, ambiguous `0.70`, then
  clearly absent below `0.50`.
- Verify the ambiguous scan remains busy and caps the rally at level 45.
- Verify a clearly absent team is released to idle.
- Verify final no-team selection sets abort (not retry), clears the pending
  level, and does not click any team or `Attack.png`.
- Run the focused rally tests and complete project verification.
