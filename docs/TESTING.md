# Testing guide

A green unit suite does not prove live game perception/click geometry.

## Automated checks

```powershell
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
python -m mypy macro_clicker tools
python -m tools.validate_scenarios
```

Blocking CI: pytest, Ruff lint, scenario/template validation.

Bot UI regressions must keep `Logs` out of `BotFrame.TAB_NAMES` and verify the Runtime Log panel is parented to the Bot frame, packed on the bottom side before the expanding notebook. This reserves the requested seven-line height and allows the existing `append_runtime_log()` path and auto-scroll behavior to remain visible across every normal page.

Multi-monitor regressions must preserve negative desktop coordinates for a left-hand display and resolve window-relative regions against the target window rather than Scenario monitor index. Automated checks must prove that the same exact/shortest-title target is activated and revalidated before input, while activation failure remains fail-closed. Live checks should run the 1920x1080 game once on each physical monitor with another app initially in front.

## Continuous Gather regressions

Protect:

- resource-search Prepare explicitly clicks middle `採集` / Gather before Gold;
- Gather-tab click is Search-button offset `(0, -480)` and precedes Gold `(+196, -348)`;
- remembered resource level is clamped down before `start_level - 1` increments, so configured Lv3 first searches Lv3 regardless of prior popup state;
- unavailable search continues downward one level at a time; configured Lv3 is not an exact-Lv3-only filter;
- trusted `0/3` -> all Idle candidates;
- 1/3, 2/3, 3/3 busy-count handling;
- compressed ordered-subset mapping rather than fixed row slots;
- missing legacy portrait does **not** imply Team 2;
- `3/3` rows resolve Team 1,2,3 in order;
- exact recent dispatch/history can narrow a row when safe;
- real status crops classify Gathering, Returning, Travelling, Rallying when available;
- every committed status crop must be successfully decoded by OpenCV, not merely exist by path;
- missing/unreadable detailed activity templates degrade to generic Busy without raising or destroying the sidebar observation;
- generic Busy fallback remains non-dispatchable;
- timer parser accepts normal `HH:MM:SS` and common OCR confusions;
- status/timer stay attached to resolved team identity;
- stale/untrusted/Unknown state cannot authorize Gather;
- timer expiry never creates Idle;
- exact fixed dispatch-card blue-idle verification remains required;
- selected-team scenarios for Teams 1, 2, and 3 all pass required-file validation and their separate `TeamNIdle.png` crops decode successfully with OpenCV;
- the supervised current Team 3 40x36 card region matches `Team3Idle.png` at or above the unchanged `0.85` Gather gate;
- a transitional blank queue immediately after confirmed Team 2 dispatch preserves prior Team 1/2 busy state and leaves Team 3 as the next candidate; a later observation after the five-second stabilization window can report them Idle normally;
- when all three teams are freshly Idle again between attempts, confirmed dispatch order rotates Team 1 -> Team 2 -> Team 3 instead of letting a quickly returned Team 1 starve Team 3;
- no-free-march never replaces;
- selected-team runtime ordering evaluates exact Dispatch + Idle before No Free March, while a true full queue still falls through to the unchanged close/stop actions;
- unconfirmed attempt pauses;
- OCR diagnostics emit `initialization started` before model construction and ready/failure timing after it;
- busy-count diagnostics preserve separate 1/3, 2/3, and 3/3 match scores plus the selected count;
- a rejected world-map frame still retains its match score for troubleshooting.

The committed real status-label crops came from supervised 1920x1080 game screenshots. Keep them as visual regression assets; do not replace them with guessed text rendering. Gathering/Rallying were rebuilt on 2026-08-27 after CI caught unreadable earlier blobs; Returning/Travelling already matched the verified local crops.

## Diagnostic logging expectations

`[team-diag]` is observation-only. During startup-delay investigation, the persistent bottom Runtime Log should make the monitor stage visible:

- unreadable map/window heartbeat is rate-limited rather than silent for minutes;
- readable diagnostics include world-map score, selected busy count, all three busy-count candidate scores, and identity completeness;
- first real timer OCR logs `OCR initialization started` before any potentially expensive PaddleOCR construction;
- OCR ready/failure includes elapsed seconds;
- any complete team-monitor pass taking at least two seconds emits `slow scan ...`.

Do not use diagnostic scores as dispatch authority in tests or production.

## Live verification matrix

Test deliberately:

1. Start Bot while another app is active and capture the complete `[team-diag]` timeline until the first Gather search click. Last War should come forward automatically; if there is a pause, identify the last diagnostic before it.
2. Run the same target-window flow with Last War on each monitor; for a left-hand display, confirm capture/click coordinates remain valid when the window rectangle has negative X.
3. Open resource search with `打野` selected; automation must switch to middle `採集`, then Gold.
4. Open resource search with `採集` selected; the normalization click must remain harmless, then Gold.
5. Open resource search with `末日精英` selected; automation must switch to middle `採集`, then Gold.
6. Leave Gold at Lv9/Lv12, configure Lv3, and confirm the first automated search is Lv3 followed by Lv2/Lv1 only when unavailable.
7. Confirm the four current status assets load; if one is deliberately unavailable, the monitor must continue and show generic Busy rather than FileNotFoundError/unreadable team view.
8. 0/3 all free.
9. each single busy team where possible.
10. each two-busy combination.
11. 3/3 all busy.
12. all three busy, then Team 2 becomes free: visible rows must become Team 1 then Team 3.
13. Gathering (`採集中`).
14. Returning (`返回`).
15. Travelling (`去 X/Y`).
16. Rallying (`集結中`).
17. long and near-zero timers, recording the first-OCR initialization duration.
18. timer reaches zero but row remains busy: no dispatch until visual Idle.
19. change a team's lead hero and verify no stale portrait misidentification.
20. exact fixed dispatch card is selected before Dispatch, including continuous Team 1 -> Team 2 -> Team 3 progression with the separate live Team 2 idle crop.
21. after Team 2 dispatch closes, watch for a briefly blank sidebar and confirm it does not cause Team 1 to be selected again; Team 3 should be next.
22. resource taken, no-free-march, F12/unconfirmed safety.

## Cold-start ambiguity

If current portraits are unknown and only part of the team set is busy, expected safe behavior may be `Unknown / waiting for team identity/status confirmation`. That is preferable to guessing. Once identity is learned from unambiguous evidence, compressed layouts should resolve normally.
