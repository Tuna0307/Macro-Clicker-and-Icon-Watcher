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

Timers are scheduling hints. Countdown zero never promotes a team to Idle; a fresh screen observation is required.

### Identity and cold-start safety

`3/3` is unambiguous because all teams are busy and rows are Team 1,2,3. An exact bot dispatch also creates trusted short-term team knowledge. Resolved rows teach/cache the current hero portrait under per-user runtime storage.

If the bot starts cold at ambiguous `1/3` or `2/3` and current portraits are not learned/recognized, affected teams remain `UNKNOWN`; the bot waits rather than guessing. This is intentional fail-closed behavior.

### Dispatch safety invariants

Preserve:

- trusted world-map gate before blank queue means `0/3`;
- fresh visual Idle required;
- exact selected team must still show its blue idle indicator at its fixed dispatch-panel position;
- dispatch-panel positions are permanently Team 1 / Team 2 / Team 3 even when heroes change;
- busy teams are never intentionally overwritten;
- no-free-march closes/stops instead of replacing;
- confirmed dispatch immediately marks that exact team non-idle;
- unconfirmed/aborted attempts pause fail-closed;
- timer expiry cannot authorize dispatch.

## Testing

Blocking CI: pytest, Ruff lint, scenario/template validation. Formatting and mypy are informational. Perception changes additionally require supervised Windows/game verification.
