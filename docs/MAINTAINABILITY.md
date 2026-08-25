# Maintainability policy

This document records the maintenance strategy for the current specialized visual-automation and screen-monitoring application.

The goal is **reliability first**. Working behavior should not be rewritten simply because a file is large, an abstraction is specialized, or a cleaner theoretical design is possible.

## What maintainability means here

Maintainability does **not** mean returning the project to a generic macro framework.

It means:

- future AI/development sessions can quickly understand why existing behavior exists;
- a new feature can be added without accidentally destabilizing mature workflows;
- uncertain image/OCR/window states continue to fail closed;
- important runtime behavior has regression coverage;
- large modules are split only when there is a concrete reason and a safe boundary.

## Current maintenance tiers

### Tier 1 — protect / leave alone by default

These areas are mature, timing-sensitive, or already heavily regression-tested. Change them only for a specific bug, performance problem, or feature requirement.

- Rally row association and `click_matching_row` behavior.
- Atomic row/template/OCR snapshots.
- Level OCR arbitration and unreadable-level retry behavior.
- Team 1 / Team 3 eligibility, availability, preference, and fallback behavior.
- Rally join transition guard and recovery/back-out sequencing.
- Foreground-window validation and live window-geometry checks before input.
- Monitor-bounds and out-of-window click rejection.
- Stop/kill-switch handling during waits, capture, matching, OCR, and actions.
- Existing scenario state transitions implemented with `set_step`.

Large or unusual code in this tier is not automatically technical debt. If it encodes a previously observed runtime failure, preserving it is more important than making it look simpler.

### Tier 2 — safe cleanup

These are appropriate for small, behavior-preserving maintenance commits:

- type annotations;
- comments/docstrings that explain non-obvious invariants;
- dead diagnostic variables that are proven unused;
- duplicated pure helper functions;
- extracting a pure calculation from a large function while keeping inputs/outputs identical;
- moving documentation or developer-only utilities;
- improving test names/fixtures without changing runtime policy;
- making CI feedback less noisy without weakening runtime tests.

Each cleanup should be its own coherent commit when possible.

### Tier 3 — refactor only when development pressure justifies it

The following modules are large, but should not be split merely to reduce line count:

- `macro_clicker/alert_watcher.py`
- `macro_clicker/engine.py`
- `macro_clicker/app.py`
- `macro_clicker/editors.py`
- `macro_clicker/detection_core.py`
- `macro_clicker/models.py`
- `macro_clicker/rally_matching.py`

A refactor becomes worthwhile when one of these concrete triggers occurs:

1. a new feature repeatedly needs to modify the same large function;
2. two features need the same logic and duplication is appearing;
3. tests cannot isolate a responsibility without constructing a large unrelated object;
4. a bug repeatedly comes from state being owned in the wrong layer;
5. performance profiling shows a clear hotspot that needs a different execution path;
6. merge/edit conflicts become common because unrelated work touches the same function.

Do not refactor only because a function is long.

## Known hotspots and preferred future boundaries

### `engine.py`

Current responsibilities include polling, condition evaluation, action dispatch, safe input, lifecycle/stop handling, and some specialized transition policy.

Preferred future extraction order **only if needed**:

1. diagnostic/log formatting helpers;
2. pure action-selection calculations;
3. feature-specific controllers for genuinely new automation systems;
4. lifecycle/polling separation only after tests cover the boundary.

Do not start with rally behavior or input safety.

### `rally_matching.py`

This module already acts as a useful specialized boundary. Keep rally row/OCR/team decision logic here unless a specific sub-responsibility becomes independently useful.

Potential extraction candidates only when justified:

- pure row-assignment helpers;
- OCR-decision formatting/evidence serialization;
- team-availability interpretation.

Preserve the atomic snapshot contract.

### `alert_watcher.py`

This is the largest module and combines watcher runtime, template management, UI/controller behavior, and sound/popup policy.

If future alert work becomes difficult, the safest extraction order is:

1. pure template/manifest operations;
2. watcher scan-loop policy;
3. UI construction;
4. sound/popup delivery.

Do not mix passive alert policy with active macro actions unless that is an intentional new feature.

### `models.py`

The broad `Action` model supports several specialized action types. It is verbose, but changing its JSON shape risks scenario compatibility.

Do not replace it with a class hierarchy just for cleanliness. Consider a migration only if adding new actions makes validation/serialization meaningfully difficult, and preserve backward-compatible loading.

### `editors.py` and `app.py`

Large UI functions are lower risk than the runtime engine. Refactor UI only when adding new controls becomes difficult or duplicated state handling appears.

## New feature rule

New automation does not need to be generic for unrelated applications.

For a new active automation feature:

1. identify its visual states;
2. reuse `detection_core.py` primitives;
3. keep feature decision policy out of passive alert code;
4. define retry/recovery behavior before the happy path is considered complete;
5. add focused tests for target selection and unsafe states;
6. avoid modifying mature rally behavior unless there is a direct dependency.

For a new passive alert feature:

1. reuse shared matching where possible;
2. keep confirmation/cooldown/sound/popup policy in the alert subsystem;
3. remain passive unless the feature is explicitly intended to trigger automation.

## Commit policy for AI-assisted development

Every non-trivial commit should explain:

- **what changed**;
- **why it changed**;
- whether runtime behavior is intended to change;
- which tests/checks protect the change;
- any important invariant future AI should preserve.

Prefer several small commits over one mixed refactor/feature/configuration commit.

## Current recommendation

Do not perform a large cleanup of the working rally system.

The preferred maintenance sequence is:

1. keep documentation/current architecture accurate;
2. keep blocking CI focused on runtime correctness;
3. add regression tests when a real failure is observed;
4. make small type/comment/helper cleanups opportunistically;
5. introduce new feature boundaries as new automation systems are added;
6. refactor a hotspot only after a concrete trigger appears.

This strategy gives future development room without trading away the reliability of the automation that already works.
