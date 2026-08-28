# Auto Gather design notes

## Current design

Normal Bot Gather is continuous and team-state driven:

```text
trusted world map
 -> read busy count
 -> interpret compressed ordered busy rows
 -> attach detailed status + timer when available
 -> otherwise keep unresolved activity safely Busy
 -> choose fresh configured Idle team
 -> open resource search
 -> select middle 採集 / Gather tab
 -> select Gold and configured level
 -> fixed dispatch-panel exact-team recheck/click
 -> Dispatch
 -> resume monitoring
```

Both the observer and selected-team scenario resolve this flow against the Last War window on either monitor. The target rectangle may use negative desktop coordinates on a left-hand secondary display; geometry remains window-relative before capture and click translation. When another app is in front at input time, the exact selected Last War window is activated and then revalidated before the action commits.

## Search-popup design learned from live game

The resource-search popup has three tabs and remembers whichever tab was previously selected. The Gather flow must therefore normalize it every time before selecting Gold. At 1920x1080, the existing Search-button anchor is used for the middle Gather tab `(0, -480)` and then Gold `(+196, -348)`. This ordering is intentional and regression-tested.

The popup also remembers its previous level. Normal Bot adaptation clamps to the minimum with 15 level-down clicks, then raises with `start_level - 1` clicks. The configured value is the first/highest search level: Lv3 tries 3, then the proven unavailable path tries 2 and 1. This normalization occurs on every one-team attempt.

## Sidebar design learned from live game

The queue contains busy teams only. It compresses upward, but preserves team-number ordering among busy teams. Therefore visual row position alone is never permanent identity.

Examples:

```text
{3} -> [3]
{1,3} -> [1,3]
{1,2,3} -> [1,2,3]
Team 2 frees -> {1,3} -> [1,3]
```

Hero portraits change whenever the team's lead hero changes. The sidebar and fixed dispatch card show the same current hero. Static hero faces therefore cannot define Team number.

The detector learns current portraits only from unambiguous assignments (for example 3/3 or other resolved state) and may use them later to recognize compressed rows. Legacy Team 1/Team 3 portrait assets are bootstrap hints only; a non-match is not evidence for Team 2.

After an exact dispatch completes, the map background and deployment queue may not render atomically. The tracker keeps every existing non-idle state for a bounded five-second stabilization window when a frame tries to replace it with Idle/Unknown. This retains confirmed Team 1/2 activity through a blank transitional queue while leaving an already-idle Team 3 selectable; it does not promote or guess any team state.

## Activity/timing design

Confirmed detailed states are Gathering, Returning, Travelling, and Rallying, with row countdowns. Their real `TeamStatus*.png` crops improve display and polling but are optional detail assets. A missing or unreadable detailed status template must degrade the row to generic Busy rather than aborting the whole sidebar observation. Generic Busy is still non-dispatchable.

Gathering and Rallying were rebuilt from verified screenshot-derived crops after CI showed their earlier committed blobs were not reliably OpenCV-decodable. Returning and Travelling already matched the verified local assets. This is why asset regression must test actual decoding rather than file existence only.

Timers are for scheduling the next visual check, never for declaring Idle. Timer OCR may continue even when detailed activity classification is unavailable.

## Diagnostic observability

The current supervised build wraps the perception loop with `[team-diag]` evidence rather than changing its decisions. It records the trusted-world-map match score, all three busy-count candidate scores and selected count, identity completeness, rate-limited unreadable-view/window state, PaddleOCR initialization start and elapsed ready/failure time, and monitor passes taking at least two seconds. These lines flow to the persistent Runtime Log below every normal tab, so diagnosis does not require leaving Gather or Dashboard. Diagnostic values must never be promoted into team-state or dispatch authority.

## Fixed dispatch geometry

Unlike sidebar rows, dispatch-card Team 1/2/3 positions are permanent. The exact team blue-idle indicator at that fixed location remains the final authority before Dispatch even if map-side state was stale, ambiguous, or only generic Busy.

Each card uses its own supervised crop: `Team1Idle.png`, `Team2Idle.png`, and `Team3Idle.png`. The Team 2 crop cannot be substituted with a neighboring card's template at the current confidence gate. Release validation therefore builds all three selected-team scenarios and verifies OpenCV can decode every idle asset.

## Historical MVP

The original finite model searched Gold, sent a target number of marches, and could replace occupied marches in `3 -> 2 -> 1` order. That model is superseded for normal Bot usage. Legacy fields remain compatibility-only.
