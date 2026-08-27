# Maintainability policy

The project follows **reliability first** maintenance. Working behavior should not be rewritten merely because a theoretically cleaner abstraction exists.

## Protected mature behavior

Change only for a specific requirement/bug with regression coverage:

- Rally same-row matching, OCR, Team 1/3 eligibility/selection, transition guards, and recovery;
- foreground/target-window/monitor input safety;
- kill-switch/stop behavior;
- proven Gather search/resource-taken flow;
- continuous Gather fresh trusted-visual-Idle requirement;
- continuous Gather busy-team protection;
- continuous Gather exact-team re-verification/click-before-Dispatch;
- passive Alert observation behavior.

## Continuous Auto Gather feature boundary

Maintain separation between:

- `bot/team_status.py` — read-only visual availability observation;
- `bot/team_state.py` — shared state/freshness;
- `bot/continuous_gather.py` — availability-driven coordination;
- `bot/adapters.py` — exact-team one-attempt Scenario adaptation;
- `scenarios/Gather Gold.json` — proven resource flow;
- `resource_gathering.py` — legacy/scenario compatibility.

### Protected perception contract

The world-map busy queue contains busy marches only. On a **trusted normal-world-map view**, absence of busy status/count is legitimate `0/3` evidence and means all three teams are Idle candidates.

Do not weaken this into “blank anywhere = idle.” The current trusted normal-map gate is the committed `templates/GatherSearchIcon.jpg` control in reference region `(0, 780, 110, 150)` at 1920×1080. A supervised real screenshot matched it at about **0.99**.

The previous implementation incorrectly used `templates/RallyIcon.png` as a generic world-map gate. It scored only about **0.39** on the supplied normal-map screenshot and caused Auto Gather to wait forever. RallyIcon remains valid for the Rally workflow, but workflow-specific templates must not be promoted to generic screen gates without real fixture evidence.

`tests/fixtures/team_status/world_map_search_anchor.jpg` is the focused real-screen regression for the current map gate.

Reuse the existing committed busy-count and Team 1/3 identity assets after the map gate passes. Team 2 is inferred from count. Contradictory evidence must fail closed as `UNKNOWN`.

Do not invent or reference visual template files that are not committed. The previous `TeamStatusSidebarHeader.png` dependency caused a runtime `FileNotFoundError` and blocked Gather entirely. Any future Travelling/Gathering/Returning/timer detector must start from real fixtures/templates and focused tests.

Map-side availability is only the first gate. The dispatch-panel exact-team blue-idle check remains the independent final gate before Dispatch.

When the map observation cannot be trusted, status/log wording should describe **waiting for a readable world-map team view** rather than implying a sidebar is required.

## Safe cleanup

Appropriate work includes type annotations, explanatory comments/docstrings, pure helper extraction, focused fixture cleanup, documentation consistency, and CI improvements that do not weaken runtime checks.

## Refactor triggers

Refactor only for concrete pressure: repeated changes/bugs, real duplication, untestable ownership, measured hotspots, or frequent conflicts. Do not refactor purely for line count.

## AI-assisted commit policy — mandatory

Every meaningful commit must use a descriptive subject **and body** suitable for a future AI without chat context.

The body should record:

- what changed;
- why;
- runtime behavior impact;
- important behavior intentionally preserved;
- tests/checks;
- remaining live verification/follow-up.

## Markdown synchronization — mandatory

Whenever behavior, architecture, UI, configuration, safety policy, testing contract, or roadmap status changes, inspect and update all affected living docs:

- `AGENTS.md`
- `README.md`
- `docs/ARCHITECTURE.md`
- `docs/BOT_UI.md`
- `docs/AUTO_GATHER.md`
- `docs/BOT_ROADMAP.md`
- `docs/TESTING.md`
- `docs/MAINTAINABILITY.md`
- `docs/auto_gather_design.md`

Dated plans/specs are historical records and should not be rewritten merely to make history match the current design.

## Change checklist

1. identify owning layer;
2. identify preserved invariants;
3. make smallest coherent implementation;
4. add/update focused tests;
5. update all affected living Markdown;
6. run/check CI;
7. note real Windows/game verification still required;
8. write a descriptive AI-oriented commit body.
