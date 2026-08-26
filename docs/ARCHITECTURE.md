# Architecture and maintenance guide

The project is a specialized Windows visual-automation bot and passive screen-monitoring application.

It originally started from a more generic macro-builder idea. The mature Scenario engine still powers automation, but the **normal product interface is now the dedicated Bot UI** rather than the Scenario/Step editor.

## Top-level architecture

```text
                         Normal user
                             │
                             ▼
                         Bot UI
                     macro_clicker/bot/
                             │
                         BotConfig
                             │
              ┌──────────────┴──────────────┐
              ▼                             ▼
       Feature adapters                BotController
    clone/configure scenarios       serialize input owners
              │                             │
              └──────────────┬──────────────┘
                             ▼
                       MacroEngine
                         engine.py
                             │
             Scenario -> Steps -> Actions
                             │
                 ┌───────────┴───────────┐
                 ▼                       ▼
          Specialized logic        Shared detection
        Rally / Gather / etc.      detection_core.py

Passive Icon Alerts use the same detection foundation but remain a separate
observer runtime and may run beside one active clicking automation.
```

The key architectural rule is:

> **Bot UI controls what the user wants; the existing specialized backend decides how to do it.**

The bot migration is not a reason to rewrite working Rally, Gather, OCR, recovery, or safety code.

## Product / control layer

### `bot_app.py`

Primary application shell.

`BotApp` subclasses the mature `App` so existing lifecycle, logging, engine integration, Advanced scenario tools, and AlertWatcherFrame remain available.

At startup it presents the dedicated Bot interface and hides the implementation/debugging tabs:

- **Advanced** — original Scenario/Step/Condition/Action editor;
- **Alert Setup** — detailed passive-alert template manager.

Those tabs remain alive and can be explicitly revealed from the Bot UI.

### `macro_clicker/bot/config.py`

Owns validated normal-user configuration and persistence.

Examples:

- Rally min/max and per-team maximum levels;
- Rally join delay;
- Gather start level, march count, replacement order;
- Position feature toggles;
- common alert preferences;
- Bot schedule;
- target-window title.

Runtime config is written under the per-user runtime directory as `bot_config.json`.

It is intentionally separate from `scenarios/*.json`.

### `macro_clicker/bot/adapters.py`

Translation boundary between normal-user settings and the proven backend.

Adapters:

1. load a bundled Scenario;
2. deep-copy it;
3. apply supported BotConfig values to known backend fields;
4. validate/run the copy.

Normal Bot settings therefore do not rewrite tuned project Scenario files.

### `macro_clicker/bot/controller.py`

Coordinates active clicking workflows.

Only one `MacroEngine` may own target-window input at a time.

The initial Start Bot queue is serialized:

```text
Development Position
  -> Science Position
      -> Auto Gather
          -> Gold Mob Rally
```

Disabled features are skipped.

Finite jobs run before continuous Rally. Direct feature buttons run only that feature and do not create a queue.

Passive Icon Alerts may run in parallel because they do not send macro input.

Do not replace serialization with multiple competing automation engines. Future interruption/time-slicing requires explicit cooperative yield/known-state boundaries first.

### `macro_clicker/bot/ui*.py`

Normal-user presentation and runtime glue:

- `ui.py` — BotFrame and page container;
- `ui_pages.py` — Dashboard, Rally, Gather, Positions, Alerts, Schedule, Logs, Settings;
- `ui_runtime.py` — configuration save/apply, feature start/stop, alert integration, scheduling, status, runtime-log mirroring.

Backend implementation fields should not automatically become Bot UI controls.

## Module ownership

| Module | Responsibility |
| --- | --- |
| `bot_app.py` | Primary bot-style application shell and bridge to the mature App backend |
| `bot/config.py` | Normal-user bot settings, validation, and persistence |
| `bot/adapters.py` | Translate BotConfig into cloned configured runtime Scenarios |
| `bot/controller.py` | Serialize active clicking features and track bot-session state |
| `bot/ui.py`, `bot/ui_pages.py`, `bot/ui_runtime.py` | Dedicated normal-user interface and runtime controls |
| `app.py` | Mature Advanced scenario editor, application lifecycle, engine UI plumbing, step previews, legacy scenario scheduling |
| `editors.py` | Advanced Condition/Action/Step dialogs and region-preview helpers |
| `alert_watcher.py` | Passive Icon Alert template manager, watcher thread, confirmation/cooldown/sound policy, detailed alert UI |
| `alert_settings.py` | Icon Alert settings model, validation, loading, persistence |
| `alert_ui.py` | Screen/region pickers and alert popup presentation |
| `engine.py` | Active automation polling, conditions, action dispatch, state transitions, retry/recovery, safe input |
| `rally_matching.py` | Specialized row matching, atomic rally snapshots, OCR decisions, team availability/selection support, rally evidence |
| `resource_gathering.py` | Small state controller for verified gather successes and busy-march replacement pointer |
| `detection_core.py` | Shared capture, monitor/window geometry, scaling, template preparation/matching, colored text, rotations, suppression |
| `models.py` | Scenario/action/condition dataclasses, validation, serialization, persistence |
| `hotkeys.py` | Physical hotkey normalization and cross-feature conflict detection |
| `level_ocr.py` | OCR lifecycle, preprocessing, text/confidence parsing |
| `diagnostics.py` | Asynchronous bounded automation evidence and rotating decision metadata |
| `atomic_io.py` | Crash-safe JSON/PNG replacement |
| `project_paths.py` | Stable project-owned scenario/template/alert paths |
| `runtime_paths.py` | Writable per-user bot config, logs, diagnostics, locks, UI state |
| `window_locator.py` | Window discovery, foreground checks, coordinate conversion |
| `capture_tool.py` | Interactive template/region capture |
| `ui_components.py` | Shared Tk styles and reusable controls |
| `app_helpers.py` | Scenario/step duplication and reference remapping |
| `log_maintenance.py` | Runtime log rotation/cleanup |

`tools/` contains developer utilities and is not part of normal startup.

## Active automation backend

The mature automation model is still:

```text
Scenario
  -> ordered Steps
      -> Conditions
      -> Actions
```

Each polling cycle evaluates enabled Steps from top to bottom. A Step whose conditions are not satisfied is skipped for that cycle rather than blocking the entire Scenario.

Existing workflows use `set_step` actions as practical state machines. Enabled/disabled transitions may encode sequencing/recovery state and are not merely editor configuration.

Specialized action types are accepted by design, including:

- `click`;
- `click_matching_row`;
- `select_rally_team`;
- `gather_control`;
- `key`;
- `wait`;
- `set_step`;
- `stop`.

The project does not require every action to be generic for unrelated applications.

## Rally workflow

Rally behavior spans:

- `engine.py`;
- `rally_matching.py`;
- `level_ocr.py`;
- scenario JSON;
- stored templates;
- focused tests.

Important behavior includes:

- desired row-reference and candidate-slot detection;
- closest valid same-row association;
- configured leftmost/rightmost target selection;
- level OCR and eligibility;
- Team 1 / Team 3 availability and level-cap decisions;
- team selection on the confirmation screen;
- carrying relevant availability state across UI transitions;
- retries when OCR is unreadable or a row changes;
- guarded transition time after a successful Join click;
- recovery when no valid target/slot/team is available.

The Bot Rally adapter changes only supported user-facing values on a cloned runtime Scenario. It does not reimplement these algorithms.

### Atomic matching/OCR snapshot

Level-filtered row decisions intentionally use one shared screen snapshot for row references, target matches, and OCR crops whenever possible.

This avoids mixing information from different moments while the live list changes. Do not replace this with independent captures without understanding the race it prevents.

### Rally transition guard

A valid slot can disappear immediately after click while the next screen is still opening. `MacroEngine` suppresses relevant recovery checks during this brief transition so normal UI change is not mistaken for a failed join.

This is intentional specialized behavior.

### Team selection

The two-team workflow uses Team 1 and Team 3 level limits plus availability.

Lower eligible levels may prefer Team 3 when idle, with Team 1 as fallback. Higher levels may require Team 1 according to configured limits.

Focused tests are the behavioral contract; normal Bot UI simply supplies supported caps.

## Auto Gather workflow

Current active workflow:

- `scenarios/Gather Gold.json`.

The visual state machine is scenario-driven while `resource_gathering.py` owns only the small persistent state that should not be represented by duplicated Step combinations.

Current contract:

- start at configured Gold level;
- if unavailable, lower one level and search again;
- no macro-defined minimum cutoff; continue until found;
- free march: game auto-selects available march, then Dispatch;
- all busy: explicitly replace configured march before Dispatch;
- default replacement order `3 -> 2 -> 1`;
- resource taken: Cancel and retry same logical dispatch/pointer;
- success count/pointer advance only after verified dispatch;
- stop after configured verified success count.

Do not recreate combinatorial Step families such as old `S1/P3`, `S2/P2` states.

See `docs/AUTO_GATHER.md`.

## Position workflows

Bundled supported scenarios:

- `scenarios/Apply Development Position.json`;
- `scenarios/Apply Science Position.json`.

They are finite automation jobs and therefore fit before continuous Rally in the serialized Start Bot queue.

## Passive Icon Alerts

Icon Alerts remain intentionally separate from active automation.

```text
capture configured region
  -> detect template
      -> confirm when needed
          -> appearance/cooldown policy
              -> sound / popup
```

Passive alerts do not click and may run alongside one active MacroEngine.

The Bot Alerts page controls common user-facing preferences. Detailed templates, thresholds, capture regions, and tuning remain in hidden Alert Setup.

## Shared detection foundation

`detection_core.py` owns reusable perception for active automation and alerts:

- DPI-aware BGR capture;
- physical-monitor handling;
- monitor/window-relative regions;
- exact resolution-derived X/Y scaling;
- multi-scale template variants;
- independent X/Y resizing for aspect changes;
- static/animated/rotated/grayscale/colored-text matching;
- low-variance safety checks;
- bounded candidate generation;
- match scoring and duplicate suppression;
- cancellation-aware matching.

Use this placement rule:

- reusable perception -> `detection_core.py`;
- specialized active decision/action -> engine/specialized automation module;
- normal-user configuration translation -> `macro_clicker/bot/`;
- passive notification policy -> alert subsystem.

## Window targeting and input safety

Active automation fails closed when target state is uncertain.

Preserve:

- target-window-relative region behavior;
- physical-monitor resolution;
- missing-window fail-closed behavior;
- rejection of clicks outside configured target window;
- foreground-window requirement for click/key actions;
- geometry recheck near input dispatch;
- `pyautogui.FAILSAFE`;
- scenario kill switch;
- stop checks during waits/capture/matching/OCR/actions;
- unreadable OCR treated as unknown.

Do not trade these guarantees for small performance improvements without explicit approval.

## Persistence/data boundaries

Project-owned implementation/config/assets:

- `templates/`;
- `scenarios/`;
- `alerts/settings.json`;
- `alerts/templates/`.

Use `project_paths.py` for project-owned paths.

Writable per-user state includes:

- `bot_config.json`;
- logs;
- diagnostics;
- locks;
- UI preferences/runtime state.

Use `runtime_paths.py` for writable state.

Normal Bot saves must not silently rewrite bundled Scenarios/templates. Adapter-based runtime clones are the intended boundary.

## Diagnostics

Automation diagnostics explain timing/image-dependent decisions without turning every poll into disk I/O.

Keep expensive screenshot encoding/file writes asynchronous or rate-limited when possible.

The Bot Logs page mirrors runtime messages for normal users; it does not replace detailed diagnostic evidence.

## Safe extension rules

1. **Product layer first for user choices.** Add BotConfig + UI + adapter for normal settings rather than exposing backend Steps.
2. **Preserve mature behavior.** Rally guards/retries/OCR/team logic and proven Gather recovery are not default refactor targets.
3. **One active input owner.** Serialize clicking automations until explicit cooperative scheduling exists.
4. **Keep perception shared, policy separate.** Reusable detection belongs in `detection_core.py`.
5. **Specialized code is acceptable.** Do not generalize only for hypothetical unrelated applications.
6. **Fail closed under uncertainty.** Detection/OCR/window failure should retry/skip/stop safely rather than guess.
7. **Persist models completely.** New BotConfig or backend fields need parse/validate/save/UI/default/test coverage.
8. **Keep timing-critical paths lean.** Diagnostic encoding/writes stay off hot paths.
9. **Respect project/runtime paths.** Never treat writable bot runtime config as a project asset.
10. **Add focused regression tests and descriptive commits.** Explain both the change and preserved behavior.

## Complexity hotspots

Large modules are not automatically refactor targets.

Current hotspots include:

- `alert_watcher.py`;
- `engine.py`;
- `rally_matching.py`;
- `detection_core.py`;
- `models.py`;
- the legacy Advanced `app.py`/editor surface.

The new `macro_clicker/bot/` layer is intentionally split into small responsibilities so new user-facing controls do not make those hotspots larger.

If backend extraction is needed, move one well-tested responsibility at a time instead of performing a broad rewrite.

## AI-assisted development workflow

Future AI sessions should read `AGENTS.md` and the relevant focused docs first.

Before editing:

1. determine whether the change is normal-user control/UI or backend behavior;
2. inspect implementation and focused tests;
3. identify the behavior that must be preserved;
4. make the smallest coherent change;
5. run focused tests plus repository quality checks;
6. perform supervised live verification when real input/timing flow changes;
7. leave a descriptive commit message.

The intended goal is a reliable dedicated automation bot with a simple interface backed by proven specialized workflows.