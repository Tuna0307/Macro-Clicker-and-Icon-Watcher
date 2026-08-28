# Continuous Auto Gather workflow

This is the authoritative behavior contract for normal Bot Auto Gather.

## Architecture

```text
TeamStatusMonitor
 -> TeamStatusDetector
 -> TeamStateTracker
 -> ContinuousGatherService
 -> selected-team Gather scenario
 -> MacroEngine
```

Normal Gather is persistent and state-driven. It does not use the legacy `3 -> 2 -> 1` replacement policy.

Team monitoring and the one-team scenario both follow the Last War target-window rectangle on either physical monitor. Signed desktop coordinates are valid when the 1920x1080 game is on a left-hand secondary display. Before a due input, the exact selected game window is automatically activated and its identity/rectangle are revalidated; exact-team dispatch gates remain unchanged.

## Resource-search popup normalization

The search popup has three tabs and remembers the last selected one. Therefore every Gather Prepare pass must explicitly normalize the popup before choosing Gold:

```text
open resource search
 -> click middle 採集 / Gather tab
 -> click Gold
 -> clamp remembered level to the minimum
 -> raise to the configured start/maximum level
 -> Search
```

At the 1920x1080 reference geometry, both clicks use the existing Search-button anchor: Gather tab `(0, -480)`, then Gold `(+196, -348)`. This must happen even when Gather already appears selected; relying on remembered UI state is not safe.

Level state is remembered too. The normal Bot adapter performs 15 safe level-down clicks to reach the clamped minimum, then `start_level - 1` level-up clicks. A configured Lv3 therefore searches Lv3 first and uses the existing one-level decrement retry for Lv2/Lv1 when unavailable. Exact-Lv3-only search is not the current product contract.

## Trusted world map and busy count

`GatherSearchIcon.jpg` proves the normal map view. Only after that may absence of busy status mean `0/3`.

Busy count uses existing `1_3Squad.png`, `2_3Squad.png`, and `FullSquad3_3.png` assets.

## Sidebar row model

The deployment queue contains busy teams only and compresses upward. Rows are ordered by Team number within the current busy subset:

```text
{3}       -> row 1 = Team 3
{1,3}     -> row 1 = Team 1, row 2 = Team 3
{1,2,3}   -> row 1 = Team 1, row 2 = Team 2, row 3 = Team 3
{1,2,3} -> Team 2 free -> {1,3} -> rows Team 1, Team 3
```

Never implement `row 1 = Team 1`, `row 2 = Team 2`, `row 3 = Team 3` as a general rule.

## Hero identity

The sidebar portrait is the current lead hero portrait and changes when that team's hero changes. The same hero appears on that team's fixed dispatch card. Therefore static Murphy/Stetmann portrait matching cannot be authoritative.

Current strategy:

1. use count + ordered-subset constraints;
2. use identity evidence only when positive;
3. when an assignment is unambiguous, learn/cache that team's current sidebar portrait in per-user runtime storage;
4. use learned current portraits to resolve later compressed layouts;
5. never interpret failure to match an old portrait as Team 2.

`3/3` is unambiguous and teaches all three current portraits. A just-confirmed exact bot dispatch can also provide useful identity history. A cold ambiguous `1/3` or `2/3` with no usable identity evidence remains Unknown and waits.

### Post-dispatch stabilization

The normal map anchor can reappear before the compressed deployment queue finishes repopulating after Dispatch closes. For five seconds after a confirmed exact-team dispatch, the tracker therefore refuses to replace any previously non-idle team with Idle/Unknown from a transitional frame. Teams that were already Idle remain eligible, so after Team 1 and Team 2 are confirmed the service can select Team 3 even if one frame briefly reports a blank queue. This bounded hold can only delay availability; it cannot create Idle authority.

## Detailed status and timer

Confirmed game labels:

```text
採集中             -> GATHERING
返回               -> RETURNING
去 X:... Y:...     -> TRAVELLING
集結中             -> RALLYING
```

Detailed status-label images are optional enhancements. Missing/unreadable activity assets must never make the whole sidebar unreadable. `_activity()` skips unavailable detailed templates and falls back to generic `BUSY` when it cannot classify a specific status. Busy remains non-dispatchable, and timer OCR can still be used when available.

The Gathering and Rallying PNG blobs were repaired on 2026-08-27 after Windows CI reproduced OpenCV decode failure despite the Gathering path existing. Returning and Travelling already matched verified local crops. Real status assets must be validated by successful `cv2.imread()` decoding, not only filesystem presence.

Each busy row may provide an `HH:MM:SS` countdown. OCR tolerates common digit confusions. The tracker uses timers to reduce polling frequency:

- near expiry -> check frequently;
- long remaining time -> check less often;
- timer reaches zero -> request fresh visual observation.

**Timer zero never means Idle.** Only fresh screen evidence can authorize a new dispatch.

## Team-monitor delay diagnostics

The current supervised build emits `[team-diag]` lines in the persistent, full-height bottom Runtime Log to make long waits explainable without altering behavior. A readable/state-change diagnostic records the world-map score, chosen busy count, all `1/3` / `2/3` / `3/3` candidate scores, and whether identity is complete. Unreadable map/window messages are rate-limited. The first timer OCR use logs `OCR initialization started` before PaddleOCR model construction and then logs ready/failure duration. Any complete monitor pass lasting at least two seconds logs `slow scan ...` with map/count context.

These values are evidence only. A high score, a timer, or a diagnostic line cannot by itself make a team Idle or authorize Dispatch.

## Exact-team dispatch second gate

The dispatch panel is different from the sidebar: Team 1/2/3 card positions are permanently fixed even when hero portraits change. Before Dispatch the runtime still:

1. requires the normal Dispatch button;
2. requires the selected team's exact blue idle icon at its fixed position;
3. clicks that exact team card;
4. runs Dispatch;
5. verifies success via the proven scenario.

If the selected team is no longer idle, the attempt exits. No-free-march never replaces another busy team.

`Team1Idle.png`, `Team2Idle.png`, and `Team3Idle.png` are separate required safety assets. Team 2's live card background differs enough that a Team 1/3 crop cannot safely replace it at the existing `0.85` gate. Automated validation must build all three selected-team scenarios and decode each crop before release.

## Safety invariants

Preserve search-tab normalization, trusted-map gating, fresh Idle, fail-closed Unknown, generic-Busy fallback for optional status assets, no timer-to-Idle promotion, no busy-team replacement, exact-team fixed-position verification, resource-taken Cancel/retry, kill switch, target-window safety, and diagnostic-only observability.

## Live verification required

Test starting the search popup from each of its three tabs; then test 0/3, each 1/3 identity, each 2/3 combination, 3/3, all four statuses, timer OCR, missing-status-asset fallback, hero changes, Team 2 disappearing from a 3-row list and Team 3 compressing upward, exact-team dispatch clicking, resource-taken retry, and no-free-march safety. For the current delay investigation, also capture the full `[team-diag]` sequence from Start Bot until the first Gather click so map score, count scores, OCR startup, and slow scans can be compared with timestamps.
