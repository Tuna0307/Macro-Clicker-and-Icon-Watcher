# Testing guide

Different tests answer different questions. A green unit suite does not prove that every live game screen/template/click geometry is correct.

## Automated regression tests

Primary command:

```powershell
python -m pytest -q
```

Important coverage includes:

- Rally row association, OCR, team selection, transition/recovery behavior;
- model/scenario validation;
- target-window and input safety;
- BotConfig persistence/validation;
- Bot adapters/controller/UI-runtime contracts;
- continuous Gather TeamStateTracker behavior;
- exact-team selected Gather adaptation;
- Dashboard status formatting;
- passive Alerts.

### Continuous Gather regressions

Protect at least:

- `IDLE` team may be selected only from fresh visual state;
- stale Idle observations cannot authorize a dispatch;
- hidden/missing team sidebar cannot authorize a dispatch;
- timer reaching `00:00:00` does **not** change a busy team to Idle;
- all configured teams busy means wait;
- disabled gathering teams are ignored;
- one selected-team attempt records only that requested team;
- dispatch runtime requires the selected team's idle indicator and clicks that exact card before Dispatch;
- selected team becoming busy triggers fail-closed exit rather than game auto-selection;
- no-free-march in continuous mode does not replace another busy team;
- confirmed dispatch marks the exact team non-idle immediately;
- unconfirmed/aborted dispatch pauses the service rather than blindly retrying;
- Dashboard can show Idle/Travelling/Gathering/Returning/Busy/Unknown and countdown hints.

Legacy Scenario Gather tests should remain because `scenarios/Gather Gold.json`, `resource_gathering.py`, and legacy fields still need backward-compatible behavior for Advanced/older configs.

## Scenario/template validation

```powershell
python -m tools.validate_scenarios
```

This is blocking CI. Runtime Bot adapters deep-copy stored scenarios, so adapter tests must also prove user settings/runtime guards are applied without mutating project-owned JSON.

## Static checks

```powershell
python -m ruff check .
python -m ruff format --check .
python -m mypy macro_clicker tools
```

Blocking CI:

- pytest
- Ruff lint
- scenario/template validation

Informational:

- Ruff formatting
- mypy

## Screenshot/fixture tests

Use screenshot fixtures when perception is the problem, especially:

- team status label/portrait not recognized;
- Idle/busy classification disagreement;
- Gathering/Returning/Travelling status confusion;
- timer OCR error;
- wrong team dispatch-card idle indicator match;
- resolution/scaling issue;
- false template match.

A good fixture records source image, templates, expected state/match/OCR, reference resolution, and a note describing the real failure.

## Live verification

GitHub Actions cannot interact with the real target game, so meaningful visual/input changes need supervised Windows proof.

### Rally

Verify target window, row/level selection, Team 1/3 behavior, recovery, and kill switch.

### Continuous Auto Gather

Start with a deliberately mixed state, for example:

```text
Team 1  Idle
Team 2  Gathering
Team 3  Travelling
```

Verify:

1. the monitor reports the three real states correctly;
2. only Team 1 is eligible;
3. search starts from configured Gold level and keeps lowering until found;
4. the dispatch panel re-verifies Team 1 is idle;
5. the automation visibly clicks Team 1 before Dispatch;
6. Team 2/3 are untouched;
7. after Team 1 dispatch, all-busy state waits rather than replacing a march;
8. visible timers/countdowns update reasonably;
9. timer reaching zero causes fresh observation, not automatic Idle;
10. when one team becomes visually Idle, that exact team is dispatched next;
11. resource-taken Cancel/retry remains safe;
12. no-free-march does not invoke legacy replacement behavior;
13. F12 or an unconfirmed attempt pauses Auto Gather and does not auto-restart.

### Bot UI/control layer

Also verify:

- app opens directly to Bot UI;
- Advanced/Alert Setup remain hidden until explicitly opened;
- Rally/Gather/Position settings affect runtime as intended;
- Position finite tasks serialize safely;
- continuous Gather waits while another input owner is active;
- Rally + continuous Gather cannot accidentally compete for input;
- Alerts can remain passive alongside active automation;
- schedule Start/Stop uses saved settings;
- Logs receives runtime activity;
- Dashboard team statuses match the live screen.

## AI-assisted change rule

For any behavior/config/UI/testing-contract change:

1. update tests;
2. update every affected living Markdown file;
3. use a descriptive commit subject/body explaining what, why, runtime impact, preserved behavior, checks, and remaining live verification.

See `AGENTS.md` for the full required format.