# Maintainability policy

Reliability first: do not rewrite proven behavior merely for cleaner abstractions.

## Protected behavior

Protect mature Rally, target-window/foreground safety, kill switch, proven Gather search/resource-taken flow, continuous Gather fresh-visual-Idle requirement, exact-team fixed-position dispatch verification, busy-team protection, and passive Alerts.

## Continuous Gather perception boundary

`team_status.py` observes; `team_state.py` stores state/freshness; `continuous_gather.py` decides when to request an attempt; `adapters.py` performs exact-team dispatch-panel verification through the existing scenario.

### Do not regress sidebar semantics

- Sidebar is busy-only and compressed upward.
- Visible row index is not a permanent Team number.
- Busy rows preserve Team-number order within the subset.
- Hero portraits change with the configured lead hero.
- Old Team1Busy/Team3Busy assets are positive bootstrap hints only.
- Missing portrait evidence must never be converted into Team 2.
- Current portraits may be learned only after identity is unambiguous and cached only in per-user runtime storage.
- Ambiguous identity stays Unknown.

### Status/timer rules

Current real labels: Gathering, Returning, Travelling, Rallying. Timer OCR may optimize polling and presentation, but timer expiry cannot change state to Idle.

### Dispatch remains independent safety authority

Dispatch cards have fixed Team 1/2/3 positions even when portraits change. Exact selected-team blue-idle verification/click remains the final gate. Do not weaken this because map-side tracking becomes richer.

## AI-assisted commit policy

Every meaningful commit needs a descriptive subject/body covering what, why, runtime impact, preserved safety/compatibility, tests/checks, and remaining live verification.

Behavior/architecture/UI/config/safety/testing/roadmap changes require synchronization of all affected living Markdown files listed in `AGENTS.md`. Historical dated plans/specs remain historical.
