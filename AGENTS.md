# AI Development Context

Read this file **before making architectural, behavior, UI, configuration, or test-contract changes**.

Also read the focused guides that match the task:

- `README.md` — current product behavior and normal-user workflow.
- `docs/BOT_UI.md` — normal-user control-layer architecture.
- `docs/ARCHITECTURE.md` — module ownership and runtime boundaries.
- `docs/MAINTAINABILITY.md` — protected behavior, refactor rules, commit/documentation policy.
- `docs/TESTING.md` — automated checks and supervised live-verification requirements.
- `docs/AUTO_GATHER.md` — continuous Auto Gather/team-state behavior contract.
- `docs/BOT_ROADMAP.md` — migration phase status and remaining work.

## Mandatory AI handoff rule

This repository is developed heavily with AI assistants. A future AI must be able to understand a change from Git history and Markdown without relying on the chat that produced it.

### Every meaningful commit must be descriptive

Do **not** use vague commit messages such as:

```text
fix gather
update bot
changes
refactor
```

Use a short subject plus a body that records:

1. **What changed** — files/components and behavior.
2. **Why it changed** — real observed problem or product requirement.
3. **Runtime impact** — what behavior is intentionally different.
4. **Safety/compatibility** — important behavior intentionally preserved.
5. **Tests/checks** — regression coverage or CI/live verification performed.
6. **Remaining work** — anything that still needs real-game proof or a later phase.

Example:

```text
Run Auto Gather from visual team availability

Replace the old finite 3-dispatch Bot flow with a persistent TeamStateTracker-driven
service. Auto Gather now waits for a fresh visual Idle state, starts one exact-team
Gather attempt, clicks that exact team before Dispatch, and leaves travelling,
gathering, returning, or otherwise busy teams untouched.

Timers are scheduling hints only; reaching 00:00:00 never promotes a team to Idle.
The existing Rally backend and stored Gather scenario remain unchanged. Add focused
regressions for stale observations, all-busy waiting, disabled teams, exact-team
selection, and fail-closed unconfirmed dispatches. Real-game template/click geometry
still requires supervised Windows verification.
```

### Markdown synchronization is part of the change

When behavior, architecture, UI, configuration, safety policy, testing contracts, or roadmap status changes, update **every living Markdown document that describes the affected area in the same work**.

At minimum check:

- `AGENTS.md`
- `README.md`
- `docs/ARCHITECTURE.md`
- `docs/BOT_UI.md`
- `docs/AUTO_GATHER.md` when Gather changes
- `docs/BOT_ROADMAP.md` when phase/status changes
- `docs/TESTING.md` when verification/test contracts change
- `docs/MAINTAINABILITY.md` when ownership/refactor/development policy changes
- `docs/auto_gather_design.md` when the current Gather design changes

Do not leave one living guide describing old behavior after code has changed.

Dated documents under `docs/superpowers/plans/` and `docs/superpowers/specs/` are historical design records. Do not rewrite history just to match current implementation. If a historical record directly causes confusion, add an explicit superseded/current-state note rather than pretending the original plan never existed.

## Current product direction

The repository is now a **specialized Windows visual-automation bot with passive screen monitoring**. The normal user configures what the bot should do without needing to understand Scenarios, Steps, Conditions, Actions, template regions, OCR crops, or recovery wiring.

The public-facing description should remain neutral. Do not unnecessarily rename the project/docs to explicitly identify the external application being automated unless requested.

## Primary UI vs backend

Normal startup uses `macro_clicker/bot_app.py`.

```text
Bot UI
  -> BotConfig
      -> adapters / service coordination
          -> proven Scenario / MacroEngine
              -> detection / OCR / safe input
```

The original Macro Builder is retained as hidden **Advanced** tooling. Detailed passive-alert configuration remains hidden **Alert Setup** tooling.

Normal users should not need Advanced to change ordinary settings such as Rally levels, Gather starting level, or which teams may gather.

## Bot layer ownership

`macro_clicker/bot/` is the normal-user control/service layer.

- `config.py` — validated/persisted normal-user settings.
- `adapters.py` — translate BotConfig into deep-copied runtime Scenarios, including exact-team Gather attempts.
- `controller.py` — serializes finite jobs and continuous Rally input ownership. Continuous Auto Gather is deliberately not a finite queued stage.
- `team_state.py` — shared thread-safe Team 1/2/3 state model and countdown hints.
- `team_status.py` — read-only world-map expedition-sidebar detector/monitor.
- `continuous_gather.py` — chooses a freshly visually idle configured team and coordinates one exact-team Gather attempt at a time.
- `status.py` — read-only Dashboard summaries.
- `ui.py` / `ui_pages.py` — Bot shell and normal-user pages.
- `ui_runtime.py` — save/apply settings, start/stop services/features, schedule polling, status/log integration.
- `bot_app.py` — application shell layering Bot UI over the mature backend.

Normal BotConfig is written to per-user runtime storage as `bot_config.json`. Normal Bot saves must not rewrite project-owned Scenario JSON.

## Adding a normal-user setting

1. Add a validated BotConfig field with a safe default.
2. Add the simple UI control.
3. Read/write it through Bot UI runtime code.
4. Translate it through the appropriate adapter/service boundary.
5. Add focused regression tests proving the value reaches runtime behavior.
6. Update all living Markdown that describes that setting/feature.
7. Commit with the AI-oriented description format above.

Do not expose internal condition/action fields without a concrete user-facing need.

## Input ownership and BotController

Only one active clicking automation may own mouse/keyboard input at a time.

Finite setup work and Rally are coordinated by `BotController`. Continuous Auto Gather is a separate persistent service driven by actual team state.

Current safe rules:

- Development and Science are finite queued tasks.
- Rally is a continuous clicking scenario and is last in the finite/controller sequence.
- Continuous Auto Gather waits while another engine/controller task owns input.
- Rally and continuous Auto Gather are currently blocked from running together because safe cooperative preemption has not been designed.
- Passive Icon Alerts may run alongside active automation because they observe rather than click.
- Stop Bot must stop continuous Gather, the current controller-owned automation, pending work, and Alerts as appropriate.

Do not create fake Rally/Gather concurrency by allowing independent scenarios to compete for input. If future requirements need time-slicing, first define cooperative yield and known-world-map handoff states.

## Active automation backend

Primary runtime:

- `macro_clicker/engine.py`

Supporting specialized modules include:

- `macro_clicker/rally_matching.py`
- `macro_clicker/resource_gathering.py` (legacy/scenario Gather state compatibility)
- `macro_clicker/level_ocr.py`
- `macro_clicker/models.py`
- `macro_clicker/window_locator.py`
- `macro_clicker/diagnostics.py`

Backend model:

```text
Scenario
  -> Steps
      -> Conditions
      -> Actions
```

Existing `set_step` state transitions may encode important sequencing/recovery behavior. Do not simplify them as cosmetic configuration.

Specialized action types are acceptable. This project does not need to remain generic for unrelated applications.

## Rally is protected mature behavior

Protect:

- same-row reference/target association;
- level OCR and eligibility filtering;
- Team 1 / Team 3 availability and level-cap logic;
- explicit Rally team selection;
- atomic matching/OCR snapshots where possible;
- unreadable OCR retry rather than guessing;
- row revalidation after pre-click delay;
- transition guards after successful Join;
- Back/recovery paths;
- foreground/target-window input safety.

The Bot Rally adapter changes supported user values on a runtime copy; it does not reimplement Rally decisions.

## Continuous Auto Gather contract

The normal Bot Gather behavior is no longer the old finite "send N marches using replacement order 3→2→1" model.

Current components:

- `TeamStateTracker` — stores visual Team 1/2/3 state.
- `TeamStatusMonitor` / `TeamStatusDetector` — read-only expedition-sidebar observation.
- `ContinuousGatherService` — picks a configured team only after a fresh visual Idle observation.
- `adapters.py` selected-team runtime mode — verifies the exact requested team is still idle on the dispatch panel, clicks that team card, then allows Dispatch.
- `scenarios/Gather Gold.json` — proven search/resource/taken-warning state machine reused as the one-attempt backend.

Team states currently include:

```text
IDLE
TRAVELLING
GATHERING
RETURNING
BUSY
UNKNOWN
```

Normal policy:

- resource: Gold;
- start at configured resource level;
- if unavailable, lower one level and search again until found;
- configured gathering teams may be any non-empty subset of Team 1/2/3;
- there is no user-facing team priority;
- if multiple configured teams are visually idle, one stable idle team is selected for an attempt;
- travelling/gathering/returning/busy/unknown teams are never intentionally overwritten;
- when all configured teams are busy, wait;
- before Dispatch, re-verify the exact selected team's idle indicator and explicitly click that team card;
- if that team became busy, close/stop the attempt rather than allowing the game to auto-select a different team;
- if the game reports no free march, close/stop instead of replacing a busy team;
- if a resource is taken, preserve the observed Cancel/retry behavior inside the one-attempt Gather flow;
- after a confirmed dispatch, immediately mark that team non-idle until visual state catches up;
- an unconfirmed/aborted attempt pauses Auto Gather fail-closed instead of blindly retrying.

### Timer rule

Visible `HH:MM:SS` countdowns are **scheduling hints, not state authority**.

A timer reaching zero may request a faster visual refresh, but it must never change a team to Idle by itself. Only fresh visual game state may authorize another dispatch.

This matters because a team can transition from Gathering to Returning, be manually changed, encounter lag, or otherwise differ from a predicted local countdown.

### Legacy Gather state

`resource_gathering.py`, `march_count`, and `replacement_order` remain loadable for backward compatibility and Advanced/scenario behavior. They are not the normal continuous Bot policy and must not reappear in the normal Gather UI as if they control continuous team selection.

`templates/GatherDispatchButton.jpg` must remain a tight stable crop without cursor/changing timer pixels.

## Position workflows

Supported bundled scenarios:

- `scenarios/Apply Development Position.json`
- `scenarios/Apply Science Position.json`

The Bot exposes simple enable/run controls. Retry policy may be configured from BotConfig; low-level scenario steps remain internal.

## Passive Icon Alerts

`macro_clicker/alert_watcher.py` remains a passive observer subsystem. Common alert preferences are exposed in the Bot UI; detailed template tuning stays in Alert Setup.

Do not route passive alerts through active input unless explicitly requested.

## Shared detection foundation

Reusable perception belongs in `macro_clicker/detection_core.py`:

- DPI-aware BGR capture;
- monitor/window-relative regions;
- exact X/Y resolution scaling;
- template preparation/matching;
- colored-text/grayscale/rotation handling;
- low-variance safety checks;
- cancellation-aware matching.

Placement rule:

- reusable perception -> `detection_core.py`;
- active workflow decisions -> engine/specialized modules;
- normal-user control/service policy -> `macro_clicker/bot/`;
- passive notification policy -> alert subsystem.

## Safety invariants

Preserve fail-closed behavior:

- no input when required target-window geometry is unavailable;
- no clicks outside target window;
- no click/key input when target window is not foreground;
- geometry rechecked close to input dispatch;
- `pyautogui.FAILSAFE` preserved;
- kill switch/stop checked throughout waits/capture/matching/OCR/actions;
- unreadable perception remains unknown rather than guessed;
- stale team-state observations cannot authorize Gather;
- local timer expiry cannot authorize Gather;
- unconfirmed selected-team dispatch cannot silently retry another team.

A speed improvement that weakens these checks is a regression unless explicitly approved.

## Testing and CI

Before finishing code changes, run/rely on:

```powershell
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
python -m mypy macro_clicker tools
python -m tools.validate_scenarios
```

Current CI policy:

- **Blocking:** pytest, Ruff lint, scenario/template validation.
- **Informational:** Ruff formatting, mypy.

Continuous Gather changes should protect tests for:

- TeamStateTracker countdown behavior;
- timer zero not becoming Idle;
- stale/hidden sidebar observations not dispatching;
- all-busy waiting;
- configured-team filtering;
- exact-team dispatch selection before Dispatch;
- no-free-march fail-closed behavior;
- unconfirmed attempt pausing;
- Dashboard live team-state summaries.

Automated Windows CI cannot prove real-game visual recognition/click geometry. Runtime perception/input changes require supervised live verification.

## Final AI checklist before committing

1. Read relevant living docs and focused tests.
2. Decide whether the change is product/control layer or backend behavior.
3. Identify behavior that must remain unchanged.
4. Make the smallest coherent implementation.
5. Add/update regression tests.
6. Update **all affected living Markdown files**.
7. Run/check CI as applicable.
8. Record any supervised live test still required.
9. Commit with a detailed subject/body describing what, why, impact, preservation, tests, and remaining work.

If a requirement is ambiguous, preserve proven behavior rather than inventing a risky interpretation.