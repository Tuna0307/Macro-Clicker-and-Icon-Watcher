# Maintainability policy

The project follows **reliability first** maintenance. Working behavior should not be rewritten merely because a file is large or a theoretically cleaner abstraction exists.

## What maintainability means here

- future AI sessions can understand why current behavior exists;
- new features can be added without destabilizing mature workflows;
- uncertain image/OCR/window/team states fail closed;
- important behavior has focused regression coverage;
- documentation and Git history remain trustworthy handoff mechanisms;
- large modules are split only when there is a concrete pressure/ownership boundary.

## Protected mature behavior

Change only for a specific requirement/bug with regression coverage:

- Rally same-row matching and atomic OCR/matching snapshots;
- Team 1/Team 3 Rally eligibility/availability/selection;
- Rally transition guards/recovery;
- foreground/target-window/monitor input safety;
- kill-switch/stop behavior;
- existing proven Gather search/resource-taken flow;
- continuous Gather fresh-visual-Idle requirement;
- continuous Gather timer-as-hint rule;
- continuous Gather busy-team protection;
- continuous Gather exact-team re-verification/click-before-Dispatch;
- passive Alert observation/cooldown behavior.

## Current preferred feature boundaries

### Rally

Keep Rally-specific row/OCR/team decisions in `rally_matching.py` and related focused backend code. Do not broadly rewrite working Rally just because the Bot UI hides its complexity.

### Continuous Auto Gather

Maintain separation between:

- `bot/team_status.py` — read-only visual observation;
- `bot/team_state.py` — shared state/countdown hints;
- `bot/continuous_gather.py` — availability-driven coordination;
- `bot/adapters.py` — exact-team one-attempt runtime Scenario adaptation;
- `scenarios/Gather Gold.json` — proven visual search/resource flow;
- `resource_gathering.py` — legacy/scenario compatibility state.

Do not collapse these into one giant Bot/Gather file.

Do not move timer logic into authority over availability. The visual game state remains authoritative.

### Alerts

Keep passive detection/notification policy separate from active automation input.

## Safe cleanup

Appropriate behavior-preserving work includes:

- type annotations;
- explanatory comments/docstrings;
- pure helper extraction;
- dead diagnostic cleanup;
- focused test fixture cleanup;
- documentation consistency;
- CI feedback improvements that do not weaken runtime checks.

## Refactor triggers

Refactor a hotspot only when there is concrete pressure such as:

1. repeated changes to the same large function;
2. real duplication across features;
3. tests cannot isolate ownership;
4. repeated bugs caused by misplaced state;
5. measured performance hotspot;
6. frequent merge/edit conflicts.

Do not refactor purely for line count.

## AI-assisted commit policy — mandatory

Every meaningful commit must use a descriptive subject **and body** suitable for a future AI that does not have the chat context.

The body should record:

- **What changed**;
- **Why it changed**;
- **Runtime behavior impact**;
- **Important behavior intentionally preserved**;
- **Tests/checks performed or added**;
- **Remaining supervised/live verification or follow-up work**.

Avoid vague subjects/bodies such as `fix bot`, `update gather`, or `refactor` with no context.

Prefer small coherent commits when possible, but do not split one behavioral change in a way that leaves code and required docs contradictory on `main` for long periods.

## Markdown synchronization — mandatory

Whenever behavior, architecture, UI, configuration, safety policy, testing contract, or roadmap status changes, inspect and update all affected living docs in the same work.

Living docs to check:

- `AGENTS.md`
- `README.md`
- `docs/ARCHITECTURE.md`
- `docs/BOT_UI.md`
- `docs/AUTO_GATHER.md`
- `docs/BOT_ROADMAP.md`
- `docs/TESTING.md`
- `docs/MAINTAINABILITY.md`
- `docs/auto_gather_design.md`

Dated documents under `docs/superpowers/plans/` and `docs/superpowers/specs/` are historical records. Do not rewrite them just to pretend old designs never existed. Add a superseded/current-state note only when needed to prevent confusion.

## Change checklist

Before finishing a meaningful change:

1. identify the owning layer;
2. identify preserved invariants;
3. make the smallest coherent implementation;
4. add/update focused tests;
5. update all affected living Markdown;
6. run/check CI as appropriate;
7. note real Windows/game verification still required;
8. write a descriptive AI-oriented commit body.

This policy exists because Git history + Markdown are part of the project's technical handoff, not optional decoration.