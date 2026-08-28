# Architecture and maintenance guide

## Runtime layers

```text
Bot UI
 -> BotConfig
 -> Feature adapters / BotController / Team-state services
 -> MacroEngine
 -> Detection / OCR / safe input
```

`bot/team_status.py` is a read-only observer; `bot/team_state.py` stores shared state/freshness; `bot/continuous_gather.py` coordinates availability; `bot/adapters.py` owns exact-team one-attempt Scenario adaptation. Keep these boundaries separate.

Normal Bot target capture is window-relative and monitor-agnostic. `find_window_rect()` may return negative `left`/`top` desktop coordinates for a secondary display; MSS capture regions and MacroEngine click coordinates must preserve those signed values. A configured target window remains authoritative over legacy Scenario monitor selection.

## Resource-search popup normalization

`scenarios/Gather Gold.json` must normalize the three-tab search popup before choosing Gold. The popup remembers its last selected tab, so the Prepare step first clicks the fixed middle `採集` / Gather tab using the Search-button anchor `(0, -480)`, then clicks Gold at `(+196, -348)`, then applies level/search. This behavior belongs to the proven scenario layer, not team-state perception.

Last War also remembers the previous level. The normal Bot adapter replaces the raw level-up sequence with a deterministic clamp-to-minimum sequence followed by `start_level - 1` increments. The existing Search-unavailable step then descends one level per retry. Thus `start_level` is the first/highest attempted level, not an exact-level-only constraint.

## Continuous Gather perception

The trusted map gate is `templates/GatherSearchIcon.jpg`. After that, busy-count templates determine 0/3 through 3/3.

The sidebar is a compressed ordered list of busy teams, not three permanent row locations. If the busy subset is `{1,3}`, visual row 1 is Team 1 and visual row 2 is Team 3. If Team 2 later becomes free from `{1,2,3}`, the third row disappears and Team 3 moves to the second visual row.

Hero portraits cannot be static identity because changing a lead hero changes both the dispatch and sidebar portrait. The detector therefore combines ordered-subset constraints, exact bot-dispatch history/other unambiguous evidence, and dynamically learned current portraits. Learned portrait crops are per-user runtime state. Old Team1Busy/Team3Busy assets are only positive bootstrap hints.

Current detailed activities are `IDLE`, `TRAVELLING`, `GATHERING`, `RETURNING`, `RALLYING`, `BUSY`, and `UNKNOWN`. Real Chinese status-label crops and timer OCR populate detailed busy states when readable.

Detailed status-label templates are optional perception detail, not an availability prerequisite. If a status-label asset is missing/unreadable, that row falls back to generic `BUSY`; the whole sidebar observation must continue. Core map-anchor, busy-count and identity templates remain required and fail closed if missing.

### Binary template integrity

A template path existing on disk does not prove OpenCV can use it. Windows CI on 2026-08-27 caught earlier Gathering/Rallying blobs that existed but were not reliably decodable. Those two assets were rebuilt from verified supervised crops. Tests for visual assets should call `cv2.imread()` and assert a non-empty image; do not rely only on `Path.is_file()`.

This also applies to dispatch safety assets. `Team2Idle.png` was added from a supervised 1920x1080 dispatch panel after runtime validation stopped the second Gather attempt because only Team 1/3 crops existed. All three exact-team scenarios must validate their required files and decode their own idle crop.

A `3/3` sidebar is inherently unambiguous: rows are Team 1, Team 2, Team 3. A cold ambiguous `1/3` or `2/3` without current identity evidence remains Unknown rather than guessing.

### Perception diagnostics

The team-status observer now exposes read-only `[team-diag]` telemetry during supervised delay investigation. It retains the latest world-map score, the three busy-count candidate scores and selected count, and identity completeness; the monitor rate-limits readable/unreadable heartbeat lines, emits an OCR-start line before PaddleOCR construction plus ready/failure duration afterward, and reports any scan taking at least two seconds. These diagnostics belong to observability only and must not feed availability or dispatch decisions.

## Timer scheduling

Countdown values are used only to choose the next screen-check interval. Long timers can be polled less often, short/expired timers more often. Timer expiry never changes activity to Idle.

## Dispatch safety

Map perception only authorizes a candidate attempt. On the dispatch panel, Team 1/2/3 card positions are fixed independently of hero image. `bot/adapters.py` still requires the exact team's blue idle indicator and clicks that exact card before Dispatch. No-free-march stops/closes rather than replacing a busy march.

## Input ownership and protected behavior

Only one clicking workflow owns input. Rally and continuous Gather remain mutually excluded. Preserve target-window/foreground safety, kill switch, mature Rally behavior, Gather resource-taken recovery, search-tab normalization, stale-state rejection, optional-status fallback, binary-template validation, diagnostic-only observability, and fail-closed ambiguity.
