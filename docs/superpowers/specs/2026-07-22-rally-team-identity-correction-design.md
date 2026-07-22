# Rally Team Identity Correction

## Goal

Correct the two-team rally scenario so it dispatches only Murphy (Team 1) and
Stetmann (Team 3). Carlie (Team 2) must never be selected, even when idle.

## Team Rules

- Team 1 is Murphy and accepts mob levels 1 through 65.
- Team 2 is Carlie and is always ignored by rally automation.
- Team 3 is Stetmann and accepts mob levels 1 through 45.
- For levels 1 through 45, prefer idle Team 3 and fall back immediately to
  idle Team 1.
- For levels 46 through 65, only Team 1 is eligible.

## Map-Side Availability Detection

Before clicking the rally icon, capture the complete deployment-queue region.
Search that whole region independently for Murphy's and Stetmann's portraits.
A portrait found anywhere in the region means that team is busy; an absent
portrait means that team is idle.

Portrait row positions must not be used as team identity. The game compresses
the active-team list upward: Stetmann is the third row when all teams are busy
and the second row when Murphy is idle. Carlie's portrait is irrelevant to the
rally availability decision.

Availability produces these level caps:

- Murphy idle: cap 65, regardless of Stetmann's state.
- Murphy busy and Stetmann idle: cap 45.
- Murphy busy and Stetmann busy: do not enter the rally page.

## Dispatch-Panel Selection

The dispatch panel keeps fixed left-to-right cards: Murphy, Carlie, Stetmann.
Team 3 idle-icon detection and clicking must target the third card. Team 2's
second card must never be inspected as a candidate or clicked.

Before selecting an eligible card, require the blue `Z` idle icon. For a low
level, check Stetmann first and then Murphy. For a high level, check Murphy
only. If no eligible card is idle, do not click the blue dispatch button.

## Diagnostics and Failure Handling

Log the detected busy/idle state and resulting level cap using the corrected
team identities. Repeated unchanged "both busy" messages may be throttled so
the log remains readable. Preserve the pre-entry availability screenshot for
diagnostics.

## Verification

Automated tests must cover:

- Stetmann found in row three of a 3/3 queue.
- Stetmann found in row two when Murphy is absent.
- Stetmann absent in the supplied 1/3 and Murphy-plus-Carlie screenshots.
- Carlie present without Stetmann does not mark Team 3 busy.
- Team 3 dispatch detection and click coordinates target the third card.
- Carlie is never selected.
- Existing low-level priority, high-level restriction, and fallback behavior.
- Full pytest, Ruff, and mypy checks.
