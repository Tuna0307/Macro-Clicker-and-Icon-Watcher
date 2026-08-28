# AI Development Context

Read this file before changing architecture, behavior, UI, configuration, perception, safety, or tests. Also read the focused living guides in `docs/`.

## Mandatory AI handoff rule

Every meaningful commit needs a descriptive subject and body that records: what changed, why, runtime impact, safety/compatibility preserved, tests/checks, and remaining live verification.

Whenever behavior, architecture, UI, configuration, safety policy, testing contracts, or roadmap status changes, update every affected living Markdown file in the same work:

- `AGENTS.md`
- `README.md`
- `docs/ARCHITECTURE.md`
- `docs/BOT_UI.md`
- `docs/AUTO_GATHER.md`
- `docs/BOT_ROADMAP.md`
- `docs/TESTING.md`
- `docs/MAINTAINABILITY.md`
- `docs/auto_gather_design.md`

Dated files under `docs/superpowers/plans/` and `docs/superpowers/specs/` are historical records; do not rewrite them merely to match current implementation.

## Product architecture

```text
Bot UI
  -> BotConfig
      -> adapters / service coordination
          -> proven Scenario / MacroEngine
              -> detection / OCR / safe input
```

Normal startup is `macro_clicker/bot_app.py`. The original editor remains hidden Advanced tooling.

Normal Bot runtime logs live in the persistent bottom `Runtime Log` panel below the notebook. The panel is packed bottom-first before the expanding notebook so its seven-line height is reserved. Do not restore a separate Logs tab; Dashboard, Rally, Gather, Positions, Alerts, Schedule, and Settings must all leave the same scrollable log visible.

## Multi-monitor target binding

Normal Bot perception and clicks follow the visible Last War target-window rectangle, not a hard-coded physical monitor. The game must work on either monitor, including a left-hand monitor whose Windows desktop coordinates are negative. Before input, the runtime automatically brings the exact selected target window forward and then revalidates its identity and rectangle; the user does not need to foreground the game manually. Preserve window-relative capture/scaling and fresh activation/window checks; do not rebind normal Bot features to Scenario `monitor_index` when a target window is configured.

## Input ownership

Only one clicking automation owns mouse/keyboard input at a time. Development/Science are finite, Rally is continuous, continuous Auto Gather is a separate persistent service, and passive Alerts may observe in parallel. Rally and continuous Gather remain blocked from simultaneous clicking until a tested cooperative handoff exists.

## Rally is protected mature behavior

Preserve same-row target matching, OCR retry, Team 1/3 availability/selection, transition/recovery paths, and target-window/foreground safety.

## Continuous Auto Gather contract

Normal Gather is state-driven, not the legacy finite `3 -> 2 -> 1` replacement model.

Core modules:

- `bot/team_state.py` — Team 1/2/3 state, timers, freshness, busy-count signal.
- `bot/team_status.py` — read-only world-map/sidebar perception.
- `bot/continuous_gather.py` — chooses a fresh visually Idle configured team.
- `bot/adapters.py` — final exact-team dispatch-panel verification/click.
- `scenarios/Gather Gold.json` — proven resource search/taken flow.

### Resource-search popup normalization

The search popup has three tabs and remembers the previously selected tab. Every Gather prepare pass must explicitly click the fixed middle `採集` / Gather tab before clicking Gold. At the 1920x1080 reference geometry, both clicks are anchored to the existing Search button: Gather tab `(0, -480)`, then Gold `(+196, -348)`. Do not remove or reorder this normalization just because a recording happens to start with Gather already selected.

The popup also remembers the previous resource level. Normal Bot Gather must clamp the level to the minimum, then raise it to `start_level` before the first Search. A configured Lv3 means try Lv3 first, then preserve the proven decrement-and-retry behavior for Lv2 and Lv1; it is not an exact-Lv3-only filter.

### Confirmed game semantics

The left deployment queue contains busy marches only and compresses upward. Rows are **not fixed team slots**. Busy rows remain ordered by team number among the busy subset:

```text
Team 3 busy only       -> row 1 = Team 3
Team 1 + Team 3 busy  -> row 1 = Team 1, row 2 = Team 3
Team 1 + 2 + 3 busy   -> rows = Team 1, Team 2, Team 3
Team 2 becomes free   -> rows = Team 1, Team 3
```

Hero portraits are not permanent team identifiers. Changing the lead hero changes both the fixed dispatch-card portrait and sidebar portrait. Therefore absence of an old Murphy/Stetmann portrait must never be interpreted as Team 2.

The current detector uses:

- `GatherSearchIcon.jpg` as the trusted normal-world-map anchor;
- `1_3Squad.png`, `2_3Squad.png`, `FullSquad3_3.png` for busy count;
- real status crops for `Gathering`, `Returning`, `Travelling`, `Rallying`;
- OCR for row timers;
- ordered-subset constraints plus dynamically learned per-user hero portraits when identity is unambiguous.

The old `Team1Busy.png` / `Team3Busy.png` images are positive bootstrap hints only. A non-match proves nothing.

### Status vocabulary and optional detail assets

Confirmed live labels:

- `採集中` -> `GATHERING`
- `返回` -> `RETURNING`
- `去 X:... Y:...` -> `TRAVELLING`
- `集結中` -> `RALLYING`

Detailed activity-label templates improve status/timer presentation but are **not core availability authority**. If one or more detailed status images are missing/unreadable on a runtime installation, `_activity()` must degrade the row to generic `BUSY` instead of failing the whole sidebar observation. Core world-map, busy-count, and identity evidence remain required/fail-closed.

The 2026-08-27 CI failure proved binary asset existence alone is insufficient: `TeamStatusGathering.png` existed by path but OpenCV could not decode its earlier blob. Gathering and Rallying were rebuilt from the verified screenshot-derived crops. For future visual assets, test actual `cv2.imread()` decoding, not only `Path.is_file()`.

Timers are scheduling hints. Countdown zero never promotes a team to Idle; a fresh screen observation is required.

### Identity and cold-start safety

`3/3` is unambiguous because all teams are busy and rows are Team 1,2,3. An exact bot dispatch also creates trusted short-term team knowledge. Resolved rows teach/cache the current hero portrait under per-user runtime storage.

If the bot starts cold at ambiguous `1/3` or `2/3` and current portraits are not learned/recognized, affected teams remain `UNKNOWN`; the bot waits rather than guessing. This is intentional fail-closed behavior.

After an exact dispatch is confirmed, keep previously non-idle team state through a five-second world-map/sidebar stabilization window. Last War can render the trusted map anchor before its compressed deployment queue repopulates; a transient blank queue during that bounded window must not erase exact dispatch history and restart Team 1 ahead of an available Team 3. The hold may delay a newly free team, but it never makes a team Idle.

Idle-team selection is fair round-robin across the configured Team set, beginning after the last successfully dispatched team and skipping teams that are not freshly visual Idle. Do not revert to always choosing the lowest-numbered Idle team: a short Team 1 trip or slow first OCR initialization can otherwise make Team 1 available again before Team 3 is attempted and starve Team 3 indefinitely.

### Dispatch safety invariants

Preserve:

- trusted world-map gate before blank queue means `0/3`, except that the bounded post-dispatch stabilization hold may conservatively retain prior non-idle state;
- fresh visual Idle required;
- exact selected team must still show its blue idle indicator at its fixed dispatch-panel position;
- `Team1Idle.png`, `Team2Idle.png`, and `Team3Idle.png` must all exist and decode; Team 2 cannot reuse a neighboring card's crop because the live card backgrounds do not meet the `0.85` gate;
- dispatch-panel positions are permanently Team 1 / Team 2 / Team 3 even when heroes change;
- busy teams are never intentionally overwritten;
- no-free-march closes/stops instead of replacing;
- confirmed dispatch immediately marks that exact team non-idle;
- unconfirmed/aborted attempts pause fail-closed;
- timer expiry cannot authorize dispatch.

### Team-monitor diagnostics

`bot/team_status.py` emits observation-only `[team-diag]` lines to diagnose long startup or polling stalls without changing any decision. Preserve these signals while the supervised delay investigation is active:

- world-map anchor score versus the `0.90` gate;
- separate `1/3`, `2/3`, and `3/3` busy-count match scores plus the selected count;
- whether compressed-row team identity is complete or partial;
- rate-limited unreadable-view/target-window heartbeat messages;
- `OCR initialization started` **before** PaddleOCR model construction, followed by ready/failure duration;
- `slow scan ...` whenever one monitor pass takes at least two seconds.

A diagnostic score or timer is never dispatch authority. Use the next supervised log to distinguish map gating, busy-count false positives, OCR initialization, and other slow detection stages before changing behavior.

## Testing

Blocking CI: pytest, Ruff lint, scenario/template validation. Formatting and mypy are informational. Perception changes additionally require supervised Windows/game verification. Image-template tests must verify decodability with OpenCV, not merely filesystem presence.
