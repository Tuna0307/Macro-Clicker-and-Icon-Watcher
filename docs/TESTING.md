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

- trusted 0-busy evidence -> Team 1/2/3 `IDLE`;
- 1 busy with neither Team 1 nor Team 3 portrait -> Team 2 inferred busy;
- 2 busy with Team 1 portrait -> Team 1/2 busy and Team 3 idle;
- 3 busy -> all busy;
- contradictory count/portrait evidence -> all `UNKNOWN`;
- every detector template path exists in the repository;
- no dependency on nonexistent `TeamStatusSidebarHeader.png`.

The detector intentionally reuses:

- `RallyIcon.png`;
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
- unconfirmed/aborted dispatch pauses.

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

- 0/3 world map with no busy status;
- Team 1-only, Team 2-only, Team 3-only busy;
- two-team combinations;
- 3/3 busy;
- resolution/scaling variants;
- any false positive/negative for the world-map Rally-icon anchor;
- dispatch-panel idle indicator disagreements.

Richer Travelling/Gathering/Returning/timer recognition should not be added from guessed geometry or nonexistent templates. Add real fixtures first.

## Live verification — Continuous Auto Gather

Test these states deliberately:

1. **0/3 busy**: all teams free and no status rows visible. Auto Gather must start.
2. **1/3 busy**: verify correct team identity/inference.
3. **2/3 busy**: verify the only free team is chosen.
4. **3/3 busy**: Auto Gather must wait.
5. **Team 2-only busy**: verify inference from count while Team 1/3 portraits are absent.
6. Open an overlay/non-map screen: blank status must not be treated as all free.
7. On dispatch panel, verify the exact chosen team’s blue idle indicator is required and its card is clicked.
8. Verify busy teams are untouched.
9. Verify resource-taken Cancel/retry.
10. Verify no-free-march does not replace an existing march.
11. Verify F12/unconfirmed attempt pauses instead of restarting.

Current map-side detector reports `Idle/Busy/Unknown`. Do not block live Gather verification waiting for richer state labels/timers; those are a later perception enhancement.

## Bot UI/control layer

Also verify app startup, Advanced isolation, Position serialization, Rally/Gather input exclusion, passive Alerts, schedule, Logs, and Dashboard state.

## AI-assisted change rule

For behavior/config/UI/testing-contract changes: update tests, update every affected living Markdown file, and use a descriptive commit subject/body explaining what, why, runtime impact, preserved behavior, checks, and remaining live verification.
