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

Do **not** use vague commit messages such as `fix gather`, `update bot`, `changes`, or `refactor`.

Use a short subject plus a body that records:

1. **What changed** — files/components and behavior.
2. **Why it changed** — real observed problem or product requirement.
3. **Runtime impact** — what behavior is intentionally different.
4. **Safety/compatibility** — important behavior intentionally preserved.
5. **Tests/checks** — regression coverage or CI/live verification performed.
6. **Remaining work** — anything that still needs real-game proof or a later phase.

### Markdown synchronization is part of the change

When behavior, architecture, UI, configuration, safety policy, testing contracts, or roadmap status changes, update **every living Markdown document that describes the affected area in the same work**.

At minimum check:

- `AGENTS.md`
- `README.md`
- `docs/ARCHITECTURE.md`
- `docs/BOT_UI.md`
- `docs/AUTO_GATHER.md`
- `docs/BOT_ROADMAP.md`
- `docs/TESTING.md`
- `docs/MAINTAINABILITY.md`
- `docs/auto_gather_design.md`

Dated documents under `docs/superpowers/plans/` and `docs/superpowers/specs/` are historical design records. Do not rewrite history merely to match the current implementation.

## Current product direction

The repository is a **specialized Windows visual-automation bot with passive screen monitoring**. The normal user configures what the bot should do without needing to understand Scenarios, Steps, Conditions, Actions, template regions, OCR crops, or recovery wiring.

Normal startup uses `macro_clicker/bot_app.py`.

```text
Bot UI
  -> BotConfig
      -> adapters / service coordination
          -> proven Scenario / MacroEngine
              -> detection / OCR / safe input
```

The original Macro Builder remains hidden **Advanced** tooling.

## Bot layer ownership

`macro_clicker/bot/` is the normal-user control/service layer.

- `config.py` — validated/persisted normal-user settings.
- `adapters.py` — runtime Scenario copies, including exact-team Gather attempts.
- `controller.py` — finite work and continuous Rally input ownership.
- `team_state.py` — shared thread-safe Team 1/2/3 state model.
- `team_status.py` — read-only world-map march-availability detector/monitor.
- `continuous_gather.py` — availability-driven continuous Gather coordinator.
- `status.py` — read-only Dashboard summaries.
- `ui.py`, `ui_pages.py`, `ui_runtime.py` — normal-user presentation/runtime glue.

Normal BotConfig is written to per-user runtime storage as `bot_config.json`. Normal Bot saves must not rewrite project-owned Scenario JSON.

## Input ownership

Only one active clicking automation may own mouse/keyboard input at a time.

- Development and Science are finite queued tasks.
- Rally is continuous and controller-owned.
- Continuous Auto Gather is a separate persistent service.
- Auto Gather waits while another engine/controller task owns input.
- Rally and continuous Auto Gather are currently blocked from running together until safe cooperative preemption is designed.
- Passive Icon Alerts may observe alongside active automation.

Do not create fake Rally/Gather concurrency by allowing independent scenarios to compete for input.

## Rally is protected mature behavior

Protect:

- same-row reference/target association;
- level OCR and eligibility filtering;
- Team 1 / Team 3 availability and level-cap logic;
- explicit Rally team selection;
- unreadable OCR retry rather than guessing;
- transition/recovery paths;
- foreground/target-window input safety.

The Bot Rally adapter changes supported user values on a runtime copy; it does not reimplement Rally decisions.

## Continuous Auto Gather contract

Normal Bot Gather is a persistent state-driven service, not the legacy finite "send N marches using replacement order 3 -> 2 -> 1" model.

Current components:

- `TeamStateTracker` — stores Team 1/2/3 visual state and freshness.
- `TeamStatusMonitor` / `TeamStatusDetector` — read-only world-map availability observation.
- `ContinuousGatherService` — chooses a configured team only after fresh `IDLE`.
- `adapters.py` selected-team runtime mode — re-verifies the exact requested team on the dispatch panel, clicks that card, then allows Dispatch.
- `scenarios/Gather Gold.json` — proven Gold search/resource/resource-taken state machine.

### World-map availability semantics

The game’s left deployment queue contains **busy marches only**. Therefore:

- a visible busy row means that march is occupied;
- when the bot is on a trusted world-map view and there is no busy-count/status row, that is the real `0/3` state and Team 1/2/3 are Idle candidates;
- blank queue state must **not** be interpreted as idle on arbitrary overlays/screens.

`team_status.py` first requires the existing `templates/RallyIcon.png` world-map anchor. It then reuses committed, already-proven assets:

- `templates/1_3Squad.png`
- `templates/2_3Squad.png`
- `templates/FullSquad3_3.png`
- `templates/Team1Busy.png` (Murphy / Team 1)
- `templates/Team3Busy.png` (Stetmann / Team 3)

Team 2 (Carlie) is inferred from busy count plus Team 1/3 identity because there is intentionally no Team 2 portrait template.

Do **not** reintroduce a dependency on nonexistent `TeamStatusSidebarHeader.png`, `Team*MarchPortrait.png`, or `TeamStatusTravelling/Gathering/Returning.png` assets unless real committed fixtures/templates are added and tested.

The current map-side detector intentionally emits `IDLE`, `BUSY`, or `UNKNOWN`. The tracker still supports richer `TRAVELLING`, `GATHERING`, and `RETURNING` states for future visual evidence, but those detailed labels/timers are not required to authorize Gather.

### Safety invariants

Preserve:

- map-side `IDLE` is trusted only when the world-map anchor is visible;
- contradictory busy-count/portrait evidence fails closed as `UNKNOWN`;
- stale observations cannot authorize Gather;
- exact selected team must still show its blue idle indicator on the dispatch panel;
- busy teams are never intentionally overwritten;
- no-free-march closes/stops rather than replacing;
- confirmed dispatch immediately marks that team non-idle;
- an unconfirmed/aborted exact-team attempt pauses Auto Gather fail-closed;
- local timer expiry can never authorize Gather by itself.

The dispatch-panel exact-team idle check is the final authority before clicking Dispatch even if map-side availability was misclassified.

### Legacy Gather state

`resource_gathering.py`, `march_count`, and `replacement_order` remain loadable for backward compatibility/Advanced behavior. They are not the normal continuous Bot policy.

## Shared detection foundation

Reusable perception belongs in `macro_clicker/detection_core.py`; workflow/service policy belongs in specialized backend or `macro_clicker/bot/`.

## Testing and CI

Before finishing code changes, run/rely on:

```powershell
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
python -m mypy macro_clicker tools
python -m tools.validate_scenarios
```

Blocking CI: pytest, Ruff lint, scenario/template validation. Formatting and mypy are informational.

Continuous Gather changes should protect tests for:

- trusted world-map `0/3` -> all Idle candidates;
- 1/3, 2/3, 3/3 busy-count classification;
- Team 2 inference from busy count + Team 1/3 portraits;
- contradictory evidence -> `UNKNOWN`;
- every detector template path exists;
- stale/untrusted visual state does not dispatch;
- all-busy waiting;
- configured-team filtering;
- exact-team dispatch-panel verification/click;
- no-free-march no-replacement;
- unconfirmed attempt pause.

Automated CI cannot prove real-game visual recognition/click geometry. Perception/input changes require supervised Windows verification.

## Final AI checklist before committing

1. Read relevant living docs and focused tests.
2. Identify ownership and preserved behavior.
3. Make the smallest coherent implementation.
4. Add/update regression tests.
5. Update **all affected living Markdown files**.
6. Run/check CI as applicable.
7. Record any supervised live test still required.
8. Commit with a detailed AI-oriented subject/body.
