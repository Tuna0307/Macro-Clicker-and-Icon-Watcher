# Maintainability policy

Reliability first: do not rewrite proven behavior merely for cleaner abstractions.

## Protected behavior

Protect mature Rally, target-window/foreground safety, kill switch, proven Gather search/resource-taken flow, three-tab search-popup normalization, continuous Gather fresh-visual-Idle requirement, exact-team fixed-position dispatch verification, busy-team protection, and passive Alerts.

The Gather search popup remembers its selected tab. Preserve the explicit middle `採集` / Gather click before Gold; do not simplify the Prepare step back to a direct Gold click just because a local test happened to begin on the correct tab.

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

Detailed `TeamStatus*.png` activity-label assets are optional detail, not core availability authority. If one is missing or unreadable, the detector must skip it and conservatively classify an unresolved activity as generic Busy. Do not let an optional status asset raise out of `_activity()` and invalidate the whole sidebar observation. Core world-map anchor, busy-count, and identity safety evidence remain fail-closed requirements.

Do not equate file existence with a valid visual template. The 2026-08-27 Gathering/Rallying repair was needed because earlier blobs existed in Git but failed OpenCV decoding. Any new or replaced visual asset should have a regression that actually loads it with `cv2.imread()` and verifies a non-empty image.

### Diagnostic-only observability

`[team-diag]` exists to explain perception delays, not to drive behavior. Preserve the ability to see world-map score, individual 1/3-2/3-3/3 busy-count scores, selected count/identity completeness, unreadable-view heartbeat, first PaddleOCR initialization timing, and scans taking at least two seconds while the supervised startup-delay investigation is active.

Never use a diagnostic score, heartbeat, elapsed time, or OCR-init event to mark a team Idle, select a team, or authorize Dispatch. If diagnostics are later reduced, keep enough evidence to diagnose future long blocking scans; change logging separately from perception thresholds/state-machine behavior whenever practical.

### Dispatch remains independent safety authority

Dispatch cards have fixed Team 1/2/3 positions even when portraits change. Exact selected-team blue-idle verification/click remains the final gate. Do not weaken this because map-side tracking becomes richer.

## AI-assisted commit policy

Every meaningful commit needs a descriptive subject/body covering what, why, runtime impact, preserved safety/compatibility, tests/checks, and remaining live verification.

Behavior/architecture/UI/config/safety/testing/roadmap changes require synchronization of all affected living Markdown files listed in `AGENTS.md`. Historical dated plans/specs remain historical.
