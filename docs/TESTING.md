# Testing guide

Different tests answer different questions. A green unit suite does not prove that every live game screen/template/click geometry is correct.

## Automated regression tests

Primary command:

```powershell
python -m pytest -q
```

Important coverage includes Rally matching/OCR/team selection, model/scenario validation, input safety, BotConfig, adapters/controller/UI runtime, continuous Gather state, exact-team dispatch adaptation, Dashboard status, and passive Alerts.

## Continuous Gather perception regressions

`tests/test_team_status.py` protects the map-side availability contract:

- a real normal-world-map Gather-search fixture matches the configured gate;
- trusted 0-busy evidence -> Team 1/2/3 `IDLE`;
- 1 busy with neither Team 1 nor Team 3 portrait -> Team 2 inferred busy;
- 2 busy with Team 1 portrait -> Team 1/2 busy and Team 3 idle;
- 3 busy -> all busy;
- contradictory count/portrait evidence -> all `UNKNOWN`;
- every detector template path exists in the repository;
- no dependency on nonexistent `TeamStatusSidebarHeader.png`.

The current normal-map gate is:

- `templates/GatherSearchIcon.jpg`;
- reference region `(0, 780, 110, 150)` at 1920×1080;
- threshold `0.90`;
- regression fixture `tests/fixtures/team_status/world_map_search_anchor.jpg`.

A supervised real-game screenshot matched the Gather search icon at about **0.99**. The previous `RallyIcon.png` gate matched only about **0.39** because the Rally workflow icon is absent from the normal map. `RallyIcon.png` should not be reintroduced as the generic map gate without new real-screen proof.

After the map gate, the detector reuses:

- `1_3Squad.png`;
- `2_3Squad.png`;
- `FullSquad3_3.png`;
- `Team1Busy.png`;
- `Team3Busy.png`.

`tests/test_continuous_gather.py` and adapter/status/UI tests continue to protect:

- only fresh trusted `IDLE` may start an attempt;
- stale/unavailable observation cannot authorize;
- all configured teams busy means wait;
- disabled teams are ignored;
- exact requested team is re-verified/clicked on dispatch panel;
- selected team becoming busy exits fail-closed;
- no-free-march does not replace;
- confirmed dispatch marks exact team non-idle;
- unconfirmed/aborted dispatch pauses;
- unavailable map state is described as waiting for a readable world-map team view.

Legacy Scenario Gather tests remain because older configs/Advanced behavior still load compatibility state.

## Scenario/template validation

```powershell
python -m tools.validate_scenarios
```

This remains blocking CI.

## Static checks

```powershell
python -m ruff check .
python -m ruff format --check .
python -m mypy macro_clicker tools
```

Blocking CI: pytest, Ruff lint, scenario/template validation. Formatting and mypy are informational.

## Screenshot/fixture tests

Use screenshot fixtures whenever perception is disputed. Especially capture:

- the normal-map Gather-search control used to prove the observation surface;
- 0/3 world map with no busy status;
- Team 1-only, Team 2-only, Team 3-only busy;
- two-team combinations;
- 3/3 busy;
- resolution/scaling variants;
- any false positive/negative for the normal-map Gather-search anchor;
- dispatch-panel idle indicator disagreements.

Do not choose a generic screen gate from a workflow template merely because it sounds related. Validate it against a real screenshot first. Richer Travelling/Gathering/Returning/timer recognition should likewise start from real fixtures, not guessed geometry or nonexistent templates.

## Live verification — Continuous Auto Gather

Test these states deliberately:

1. **Normal map gate**: Gather search icon is visible and the monitor produces a `[team]` observation.
2. **0/3 busy**: all teams free and no status rows visible. Auto Gather must start.
3. **1/3 busy**: verify correct team identity/inference. The latest supplied live screenshot is already a useful 1/3 case.
4. **2/3 busy**: verify the only free team is chosen.
5. **3/3 busy**: Auto Gather must wait.
6. **Team 2-only busy**: verify inference from count while Team 1/3 portraits are absent.
7. Open an overlay/non-map screen that hides the Gather search control: blank status must not be treated as all free.
8. On dispatch panel, verify the exact chosen team’s blue idle indicator is required and its card is clicked.
9. Verify busy teams are untouched.
10. Verify resource-taken Cancel/retry.
11. Verify no-free-march does not replace an existing march.
12. Verify F12/unconfirmed attempt pauses instead of restarting.

Current map-side detector reports `Idle/Busy/Unknown`. Do not block live Gather verification waiting for richer state labels/timers; those are a later perception enhancement.

## Bot UI/control layer

Also verify app startup, Advanced isolation, Position serialization, Rally/Gather input exclusion, passive Alerts, schedule, Logs, and Dashboard state.

## AI-assisted change rule

For behavior/config/UI/testing-contract changes: update tests, update every affected living Markdown file, and use a descriptive commit subject/body explaining what, why, runtime impact, preserved behavior, checks, and remaining live verification.
