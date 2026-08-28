# Maintainability policy

Reliability first: do not rewrite proven behavior merely for cleaner abstractions.

Keep the normal Runtime Log as a sibling below the tab notebook, not as another page. Preserve its bottom-first pack ordering so the expanding notebook cannot clip the panel, plus the single `bot_log` sink and `append_runtime_log()` routing so UI layout changes do not fork or lose automation diagnostics.

Normal Bot automation is target-window-relative across multiple monitors. Preserve signed absolute coordinates returned for left/top secondary displays and keep automatic activation plus foreground identity checks tied to the same selected window. Do not introduce a hidden assumption that the target begins at `(0, 0)` or lives on physical Monitor 1.

## Protected behavior

Protect mature Rally, exact target-window activation/revalidation safety, kill switch, proven Gather search/resource-taken flow, three-tab search-popup normalization, continuous Gather fresh-visual-Idle requirement, exact-team fixed-position dispatch verification, busy-team protection, and passive Alerts. A user should not need to foreground the target manually, but failed activation or identity validation must still block input.

The Gather search popup remembers its selected tab. Preserve the explicit middle `採集` / Gather click before Gold; do not simplify the Prepare step back to a direct Gold click just because a local test happened to begin on the correct tab.

It also remembers the resource level. Preserve minimum-level clamping followed by `start_level - 1` increments before the first Search. Do not revert to adding the configured value onto whatever level the game remembered. The subsequent decrement loop intentionally searches lower levels until found.

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
- For five seconds after a confirmed exact dispatch, a transient Idle/Unknown observation must not replace any previously non-idle team. This is a bounded render-stabilization guard, not permission to infer activity or make timers authoritative.
- Multiple fresh Idle candidates rotate forward after the last successful configured team. Preserve this coordination-layer fairness rule so a quickly returned low-numbered team cannot starve Team 3; never use the rotation cursor to make a stale/busy team eligible.

### Status/timer rules

Current real labels: Gathering, Returning, Travelling, Rallying. Timer OCR may optimize polling and presentation, but timer expiry cannot change state to Idle.

Detailed `TeamStatus*.png` activity-label assets are optional detail, not core availability authority. If one is missing or unreadable, the detector must skip it and conservatively classify an unresolved activity as generic Busy. Do not let an optional status asset raise out of `_activity()` and invalidate the whole sidebar observation. Core world-map anchor, busy-count, and identity safety evidence remain fail-closed requirements.

Do not equate file existence with a valid visual template. The 2026-08-27 Gathering/Rallying repair was needed because earlier blobs existed in Git but failed OpenCV decoding. Any new or replaced visual asset should have a regression that actually loads it with `cv2.imread()` and verifies a non-empty image.

### Diagnostic-only observability

`[team-diag]` exists to explain perception delays, not to drive behavior. Preserve the ability to see world-map score, individual 1/3-2/3-3/3 busy-count scores, selected count/identity completeness, unreadable-view heartbeat, first PaddleOCR initialization timing, and scans taking at least two seconds while the supervised startup-delay investigation is active.

Never use a diagnostic score, heartbeat, elapsed time, or OCR-init event to mark a team Idle, select a team, or authorize Dispatch. If diagnostics are later reduced, keep enough evidence to diagnose future long blocking scans; change logging separately from perception thresholds/state-machine behavior whenever practical.

### Dispatch remains independent safety authority

Dispatch cards have fixed Team 1/2/3 positions even when portraits change. Exact selected-team blue-idle verification/click remains the final gate. Do not weaken this because map-side tracking becomes richer.

Keep separate decodable `Team1Idle.png`, `Team2Idle.png`, and `Team3Idle.png` assets. A 2026-08-28 supervised run exposed that the Team 2 path was configured but missing, so Team 1 succeeded and the service paused before Team 2. A later run measured the old Team 3 crop at `0.812` against the current card and refreshed it from the live 1920x1080 panel. Tests must validate required files for every selected team and retain the Team 3 live-region `0.85` regression, not merely assert configured path strings.

A later 2026-08-28 run confirmed Team 1 and Team 2 dispatches, then captured a map-anchor/blank-queue transition that reset both to Idle and restarted Team 1. Preserve the post-dispatch stabilization regression that keeps prior busy evidence long enough for Team 3 to remain the next candidate.

Final supervised diagnosis also showed Team 1 can genuinely return during the roughly 30-second first PaddleOCR initialization. Lowest-number-first selection then restarts Team 1 after Team 2 even without a false frame. Preserve the fair Team 1 -> Team 2 -> Team 3 rotation regression independently of the stabilization guard.

Preserve selected-team scenario precedence: Dispatch-button + exact idle-card must be evaluated before the broad no-free banner once the panel opens. This ordering cannot authorize a busy card because the exact idle condition remains mandatory; it only prevents a transient notification from stopping a valid attempt before the stronger proof is checked.

## AI-assisted commit policy

Every meaningful commit needs a descriptive subject/body covering what, why, runtime impact, preserved safety/compatibility, tests/checks, and remaining live verification.

Behavior/architecture/UI/config/safety/testing/roadmap changes require synchronization of all affected living Markdown files listed in `AGENTS.md`. Historical dated plans/specs remain historical.
