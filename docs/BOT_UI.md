# Dedicated Bot UI architecture

The primary product experience is now a **dedicated automation bot**, not a Scenario/Step editor.

Normal users should configure what they want the automation to do. They should not need to understand template regions, condition indices, OCR crops, `set_step` transitions, or specialized action internals.

The mature Scenario/Step system remains the implementation backend and is still available through **Advanced** for development and debugging.

## Product layers

```text
                         Normal user
                             │
                             ▼
                         Bot UI
                Dashboard / Rally / Gather
             Positions / Alerts / Schedule
                             │
                             ▼
                         BotConfig
                             │
                  user-understandable values
                             │
               ┌─────────────┴─────────────┐
               ▼                           ▼
        Feature adapters              BotController
     clone/configure scenarios      serialize active jobs
               │                           │
               └─────────────┬─────────────┘
                             ▼
                    Existing MacroEngine
                             │
               Scenarios / Steps / Actions
                             │
              Detection / OCR / input safety

Passive Icon Alerts remain a separate watcher and may run beside one active
clicking automation.
```

## Normal-user interface

`macro_clicker/bot/ui.py`, `ui_pages.py`, and `ui_runtime.py` own the simple bot-facing interface.

The current pages are:

- **Dashboard** — bot status, current task, alert status, Start/Stop, and quick actions.
- **Rally** — enable Rally and edit mob/team level limits plus join delay.
- **Gather** — enable gathering, choose starting level, march count, and replacement order.
- **Positions** — enable/run Development and Science position workflows.
- **Alerts** — simple passive-alert controls, with detailed template setup available separately.
- **Schedule** — start/stop time and weekdays for the bot session.
- **Logs** — normal runtime log output without requiring the Advanced editor.
- **Settings** — target-window title and access to Advanced tools.

Do not add Step/Condition/Action fields to these pages merely because those fields exist in the backend. Only expose settings a normal user has a reason to understand and change.

## Advanced tools

The original `App` is still built by `BotApp`, but its tabs are hidden during normal use:

- **Advanced** — Scenario/Step/Condition/Action editor, template testing, regions, diagnostics controls.
- **Alert Setup** — detailed passive-alert template manager and detection settings.

These are intentionally retained because they are valuable for debugging, capture, tuning, and future development. They are not the normal product workflow.

A bot feature should not require the user to open Advanced just to change ordinary behavior such as a mob level or gather march count.

## BotConfig

`macro_clicker/bot/config.py` owns the normal-user settings model.

The configuration is persisted to:

```text
%LOCALAPPDATA%\Macro Clicker and Icon Watcher\bot_config.json
```

or the directory selected by `MACRO_CLICKER_DATA_DIR`.

`BotConfig` currently groups:

- Rally settings;
- Gather settings;
- Position toggles;
- Alert preferences;
- schedule settings;
- target-window title.

This runtime file is intentionally separate from project-owned Scenario JSON. Normal Bot UI saves must not rewrite the tuned bundled scenarios.

## Feature adapters

`macro_clicker/bot/adapters.py` is the translation boundary between simple settings and the existing backend.

The adapter pattern is:

```text
load proven bundled Scenario
          ↓
deep-copy runtime Scenario
          ↓
apply BotConfig values to known supported fields
          ↓
validate configured copy
          ↓
run it through MacroEngine
```

The stored Scenario file remains unchanged.

### Rally adapter

The Rally adapter currently maps normal-user values to the mature two-team Rally workflow:

- minimum eligible mob level;
- maximum eligible mob level;
- Team 1 maximum level;
- Team 3 maximum level;
- pre-join delay.

It does not reimplement same-row matching, OCR, availability detection, team selection, transition guards, or recovery.

### Gather adapter

The Gather adapter currently maps:

- starting resource level;
- number of successful marches to send;
- busy-march replacement order.

The proven Gather search/retry/resource-taken state machine and `GatherController` remain the backend.

The current implemented resource is Gold. Do not expose another resource as functional until the corresponding backend flow/templates have actually been implemented and tested.

## BotController

`macro_clicker/bot/controller.py` owns coordination between **active clicking automations**.

Only one `MacroEngine` may own game input at a time. Do not run Rally, Gather, or Position scenarios concurrently and let them compete for the mouse/keyboard.

For **Start Bot**, the initial safe serialized order is:

```text
Development Position
        ↓
Science Position
        ↓
Auto Gather
        ↓
Gold Mob Rally
```

Disabled features are skipped.

The finite workflows run before Rally because Rally is normally continuous. If Rally ran first, later finite tasks would never receive control.

The direct **Run Rally / Run Gather / Run Position** buttons remain one-off actions and do not create a multi-feature queue.

**Stop Bot** cancels both the current active automation and any remaining queued active jobs.

Passive Icon Alerts are different: they do not send automation input, so they may continue beside the currently active MacroEngine.

## Future controller work

Do not implement fake concurrency by rapidly switching two independent scenarios.

If future requirements need Rally to interrupt Gather or a recurring finite task to run while Rally is active, add explicit cooperative task boundaries/return-to-known-state behavior first. A scheduler may then hand ownership between workflows safely.

Until those boundaries exist, serialized input ownership is the safety contract.

## Adding a new normal-user setting

When a backend behavior should become configurable from the Bot UI:

1. Add a validated field to the appropriate `BotConfig` section.
2. Preserve a safe/backward-compatible default matching current proven behavior.
3. Add the control to the relevant Bot page.
4. Read/write it through `ui_runtime.py`.
5. Translate it in the feature adapter rather than directly editing project scenario files.
6. Add focused tests proving the value reaches the intended runtime Scenario/action.
7. Update this guide when the product contract changes.

Do not bypass validation or modify stored Scenario JSON merely because it is easier than creating a proper adapter.

## Adding a new bot feature

Prefer this sequence:

1. Make the feature reliable as an isolated backend workflow/module.
2. Add focused regression tests and safe recovery behavior.
3. Add a `BotConfig` section containing only user-relevant choices.
4. Add a feature adapter that reuses the working backend.
5. Add the Bot UI page/controls.
6. Decide whether the feature is finite or continuous and place it correctly in controller scheduling.
7. Keep Advanced tools available for diagnostics, but do not make them required for ordinary use.

## What remains backend-only by default

These details should normally remain hidden from the normal Bot UI:

- template confidence thresholds;
- capture/detection regions;
- condition indices;
- comparison templates;
- OCR crop geometry;
- state-machine `set_step` wiring;
- retry/transition guards;
- row tolerances;
- diagnostic sampling internals;
- exact recovery click coordinates.

Expose one of these only when there is a concrete user-facing need, not simply because it is configurable internally.

## Preservation rule

The bot migration is a **product/control-layer refactor**, not permission to rewrite mature automation internals.

In particular, continue to protect:

- Rally same-row matching;
- atomic OCR/matching snapshots;
- Team 1 / Team 3 availability and selection behavior;
- rally transition/recovery guards;
- Auto Gather search-until-found behavior;
- resource-taken Cancel/retry behavior;
- busy-march replacement state;
- target-window/foreground input safety;
- kill switch and PyAutoGUI failsafe;
- passive alert confirmation/cooldown behavior.

The desired end state is a simple bot interface backed by the same reliable specialized automation engine.