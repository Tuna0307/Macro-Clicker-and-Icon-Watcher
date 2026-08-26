# AI Development Context

Read this file before making architectural or behavior changes.

Also read the focused guides that match the task:

- `docs/BOT_UI.md` — primary product/control-layer architecture and normal-user settings rules.
- `docs/ARCHITECTURE.md` — module ownership and runtime boundaries.
- `docs/MAINTAINABILITY.md` — protected behavior, safe cleanup, and refactor triggers.
- `docs/TESTING.md` — automated checks, screenshot fixtures, Windows/headless limits, and live verification.
- `docs/AUTO_GATHER.md` — Auto Gather behavior and recovery contract.

## Current product direction

This repository began as a more generic macro builder. That is no longer the product direction.

The project is now a **specialized Windows visual-automation bot with passive screen monitoring**. The normal user should configure what they want the bot to do without needing to understand Scenarios, Steps, Conditions, Actions, template regions, OCR crops, or recovery wiring.

The public-facing description should remain neutral. Do not unnecessarily rename the project/docs to explicitly identify the external application being automated unless the user asks.

## Primary UI vs backend

The normal application starts through `macro_clicker/bot_app.py`.

Normal-user flow:

```text
Bot UI
  -> BotConfig
      -> feature adapter / BotController
          -> runtime clone of proven Scenario
              -> MacroEngine
                  -> detection / OCR / safe input
```

The original Macro Builder is **not deleted**. `BotApp` still constructs it and keeps it as hidden **Advanced** tooling for scenario/template debugging. Detailed passive-alert configuration remains hidden **Alert Setup** tooling.

Do not make normal users open Advanced merely to change ordinary behavior such as a Rally level or Gather march count.

## Bot layer ownership

`macro_clicker/bot/` is the normal-user control layer.

- `config.py` — validated/persisted user-facing bot settings.
- `adapters.py` — translate BotConfig into deep-copied runtime Scenarios.
- `controller.py` — serialize active clicking automations; one MacroEngine owns input at a time.
- `ui.py` — BotFrame shell.
- `ui_pages.py` — Dashboard/Rally/Gather/Positions/Alerts/Schedule/Logs/Settings controls.
- `ui_runtime.py` — save/apply settings, start/stop features/alerts, schedule polling, dashboard/log state.
- `bot_app.py` — application shell layering Bot UI over the mature `App` backend.

Normal BotConfig is written to the per-user runtime directory as `bot_config.json`. It must not rewrite project-owned scenario JSON just because a user changed a setting.

### Adding a normal-user setting

Use this pattern:

1. Add a validated field to BotConfig with a safe default matching current working behavior.
2. Add the simple UI control.
3. Read/write it through Bot UI runtime code.
4. Translate it in the appropriate adapter to a cloned runtime Scenario/backend object.
5. Add a focused regression test proving the setting reaches the intended runtime behavior.

Do **not** expose internal condition/action fields directly unless the user has a concrete reason to control them.

## BotController / input ownership

Only one active clicking automation may own mouse/keyboard input at a time.

Start Bot currently serializes enabled finite jobs before continuous Rally:

```text
Development Position
  -> Science Position
      -> Auto Gather
          -> Gold Mob Rally
```

Disabled features are skipped. Direct Run buttons are one-off runs rather than queued bot sessions.

Stop Bot must cancel the current automation and pending queue.

Passive Icon Alerts may run alongside the active MacroEngine because they observe rather than send macro input.

Do not implement fake Rally/Gather concurrency by letting independent scenarios compete for clicks. If future requirements need interruption/time-slicing, first add explicit cooperative yield/known-state boundaries and then build scheduling around them.

## Active automation backend

Main runtime:

- `macro_clicker/engine.py`

Supporting modules include:

- `macro_clicker/rally_matching.py`
- `macro_clicker/resource_gathering.py`
- `macro_clicker/level_ocr.py`
- `macro_clicker/models.py`
- `macro_clicker/window_locator.py`
- `macro_clicker/diagnostics.py`

The backend model remains:

```text
Scenario
  -> Steps
      -> Conditions
      -> Actions
```

Steps are polled repeatedly. Existing workflows use enabled/disabled Step state as practical state machines. Those transitions can encode important sequencing and recovery behavior; do not treat them as cosmetic.

Specialized action types are acceptable. Current behavior includes actions such as:

- `click`
- `click_matching_row`
- `select_rally_team`
- `gather_control`
- `key`
- `wait`
- `set_step`
- `stop`

This project does not need to remain generic for unrelated applications.

## Rally is protected mature behavior

Rally behavior spans `engine.py`, `rally_matching.py`, OCR, scenario JSON, templates, and focused tests.

Important protected behavior includes:

- same-row reference/target association;
- level OCR and eligibility filtering;
- Team 1 / Team 3 availability and level-cap logic;
- team selection behavior;
- atomic screen snapshots for row matches and OCR when possible;
- retrying unreadable OCR instead of guessing;
- revalidating a row after configured pre-click delay;
- carrying relevant team state across transitions;
- guarded transition time after a successful Join click;
- Back/recovery paths for no valid target/slot/team;
- target-window and foreground input safety.

Do not broadly rewrite or simplify Rally just because the normal Bot UI now hides this complexity.

### Atomic Rally snapshot

For level-filtered row decisions, row anchors, candidate targets, and OCR crops intentionally derive from one atomic screenshot whenever possible.

This avoids mixing different moments while a live list changes. Do not replace it with multiple independent captures without understanding the race condition.

### Team selection

The two-team workflow uses Team 1 and Team 3 limits/availability.

Lower eligible mobs may prefer Team 3 when available, with Team 1 as fallback. Higher mobs may require Team 1 according to configured caps.

BotConfig currently exposes user-facing limits, but the adapter only changes supported fields on a runtime copy; it does not reimplement the selection algorithm.

## Auto Gather behavior contract

Current active scenario:

- `scenarios/Gather Gold.json`

State helper:

- `macro_clicker/resource_gathering.py`

Normal user settings are applied by the Gather adapter; the proven state machine remains underneath.

Current behavior:

- resource: Gold;
- starts at configured level (default 12);
- if not found, lowers one level and searches again;
- there is no macro-defined minimum cutoff: continue until found; at the game's own minimum, repeated minus clicks leave the level unchanged and search continues;
- default target: 3 verified successful gathering dispatches;
- free march path: the game auto-selects an available march, then macro clicks Dispatch;
- all-marches-busy path: explicitly replace marches in configured order (default `3 -> 2 -> 1`) before Dispatch;
- replacement pointer advances only after verified successful replacement;
- resource-taken warning: click Cancel and retry the same logical dispatch/replacement without consuming success count/pointer;
- stop after configured verified success count.

Do not recreate the old combinatorial `S1/P3`, `S2/P2`, etc. Step duplication. `GatherController` owns the small persistent state.

`templates/GatherDispatchButton.jpg` is intentionally a tight crop of stable Dispatch-label pixels. Do not recapture it with mouse-cursor or changing timer pixels.

## Position workflows

Bundled supported scenarios include:

- `scenarios/Apply Development Position.json`
- `scenarios/Apply Science Position.json`

These are real supported workflows, not demos. The Bot UI exposes simple enable/run controls while the backend scenarios remain intact.

## Passive Icon Alerts

Main runtime:

- `macro_clicker/alert_watcher.py`

Supporting modules:

- `macro_clicker/alert_settings.py`
- `macro_clicker/alert_ui.py`

Icon Alerts continuously scan configured regions/templates and notify with sound/popup. They do not execute macro actions.

Examples include Dig-related templates and Secret Task detection.

The simple Bot Alerts page may group common templates, but detailed template threshold/region/capture work remains in hidden Alert Setup.

Do not route passive alerts through active macro input unless the user explicitly asks for that behavior.

## Shared detection foundation

Main module:

- `macro_clicker/detection_core.py`

Both active automation and passive alerts use shared perception including:

- DPI-aware BGR capture;
- physical monitor handling;
- target-window/monitor-relative regions;
- exact X/Y resolution scaling;
- template resizing/rotations;
- static/animated/grayscale/colored-text matching;
- low-variance safety checks;
- match scoring/candidate suppression;
- cancellation-aware matching.

Rule:

- reusable perception -> `detection_core.py`;
- active workflow decisions -> engine/specialized automation modules;
- user-facing configuration translation -> `macro_clicker/bot/`;
- passive alert policy -> alert subsystem.

## Safety invariants

Preserve fail-closed behavior.

Important invariants include:

- do not click when required target-window geometry is unavailable;
- do not click outside the configured target window;
- do not send click/key input when the target window is not foreground;
- recheck window geometry close to input dispatch;
- preserve `pyautogui.FAILSAFE`;
- preserve scenario kill switch behavior;
- check stop requests during waits, capture, matching, OCR, and actions;
- treat unreadable OCR as unknown instead of inventing a value;
- retry/stop uncertain states rather than guessing.

A speed improvement that removes these checks is a regression unless explicitly approved.

## Diagnostics

Automation diagnostics exist because many failures are timing/image-dependent.

`macro_clicker/diagnostics.py` records bounded evidence such as:

- template/row matches;
- OCR crops/text/confidence;
- configured limits;
- team decisions;
- row changes/retry reasons;
- final decision metadata.

Keep expensive screenshot encoding/disk writes away from timing-critical matching where possible.

The Bot Logs page is a normal-user view of runtime log messages, not a replacement for detailed diagnostics.

## Persistence boundaries

Project-owned implementation/config/assets:

- `scenarios/`
- `templates/`
- `alerts/settings.json`
- `alerts/templates/`

Use `project_paths.py` for those paths.

Writable per-user state:

- `bot_config.json`
- logs/diagnostics;
- locks;
- UI preferences;
- other runtime state.

Use `runtime_paths.py` for writable state.

Loading project configuration should not silently rewrite it. Normal Bot settings persistence is separate from scenario/template persistence.

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

For Bot changes, also protect focused tests for:

- BotConfig persistence/validation;
- adapter translation without mutating source scenarios;
- BotController serialization/queueing;
- BotApp UI shell/hidden Advanced tools;
- existing Rally/Gather/Alert regressions.

Windows GitHub Actions is appropriate for the full automated suite. Live game timing/click behavior still requires supervised testing on the actual target application when runtime input flow changes.

## Guidance for AI-generated changes

Before editing:

1. Read the relevant docs/tests.
2. Identify whether the task is **normal-user product layer** or **backend behavior**.
3. Preserve current working behavior unless the request explicitly changes it.
4. Prefer small, test-backed changes.
5. Do not generalize specialized logic for hypothetical unrelated applications.
6. Do not expose backend complexity in the Bot UI without a user-facing reason.
7. Update tests/docs when changing contracts.
8. Leave a descriptive commit message explaining **what changed, why, and what was intentionally preserved**.

If a requirement is ambiguous, preserve proven behavior rather than inventing a risky interpretation.