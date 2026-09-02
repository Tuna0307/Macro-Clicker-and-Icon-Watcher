# Testing guide

This project has several kinds of tests. They answer different questions and should not be treated as interchangeable.

## 1. Python/unit regression tests

Primary command on the supported Windows development environment:

```powershell
python -m pytest -q
```

These tests protect logic such as:

- row association and target choice;
- rally team selection;
- OCR decision behavior;
- scenario/model validation;
- target-window and click safety;
- stop/kill-switch behavior;
- detection scaling and matching helpers;
- diagnostics and persistence behavior.

A passing unit suite means the encoded behavior still matches the regression tests. It does **not** prove that every live screen/template will be recognized correctly.

## 2. Scenario/template validation

```powershell
python -m tools.validate_scenarios
```

This is a blocking CI check because broken JSON references, missing templates, malformed regions, or invalid persisted fields can make a saved workflow unusable even when Python tests pass.

## 3. Static checks

### Ruff lint

```powershell
python -m ruff check .
```

This remains a blocking CI check for straightforward correctness problems such as undefined names and invalid imports.

### Ruff formatting

```powershell
python -m ruff format --check .
```

Formatting is informational in CI. A formatting difference does not imply the macro behavior is wrong.

### mypy

```powershell
python -m mypy macro_clicker tools
```

mypy is informational in CI. It is useful for identifying inconsistent type annotations, but a type-annotation warning is not by itself evidence of a runtime automation failure.

## 4. Headless/local test limitations

The application is Windows desktop automation. Some tests import or depend on:

- `keyboard`;
- `mss`;
- `pyautogui`;
- Windows window APIs;
- an active graphical display.

A Linux/headless analysis environment may be able to run pure detection/OCR/model tests but may not be able to import the full automation stack without a display or Windows-specific dependencies.

Do not change production code merely to make desktop-input modules importable in an unrelated headless environment unless there is a real portability requirement.

The Windows GitHub Actions runner is the canonical full automated environment for the repository.

## 5. Screenshot/fixture tests

Screenshots are useful when testing **perception**, not just decision logic.

Provide or preserve a screenshot/fixture when a real failure looks like:

- a valid icon was not detected;
- an unrelated icon produced a false positive;
- an OCR level was read incorrectly;
- the wrong row/slot was paired because of screen geometry;
- scaling or aspect-ratio changes affected matching;
- team availability recognition disagreed with what was visible.

A good image-regression fixture should record:

1. the source screenshot/crop;
2. the template(s) being tested;
3. expected match/no-match result;
4. expected OCR value when applicable;
5. reference resolution or target-window size when relevant;
6. a short note describing the real failure it reproduces.

Do not collect screenshots just to increase test count. Add them when they represent a real visual condition the detector must continue to handle.

## 6. Live verification

Some behavior cannot be proven completely by unit tests because the external UI is live and timing-sensitive.

After a meaningful change to rally/input timing, perform a short supervised run and verify at least:

- the correct target window is used;
- when `require_target_foreground` is disabled, a click can be dispatched to a
  visible target on a secondary monitor while another application is foreground,
  without weakening target-window bounds or fresh-geometry checks;
- negative absolute desktop coordinates remain valid for monitors positioned
  left of or above the primary monitor;
- joins select the intended row;
- OCR filters the expected levels;
- Team 1 / Team 3 selection behaves as configured;
- recovery does not back out after a successful transition;
- kill switch/stop remains responsive;
- passive Icon Alerts still notify without triggering macro actions.

For high-risk timing changes, preserve diagnostic evidence during the supervised run.

## CI policy

Current blocking checks:

```text
pytest                       BLOCKING
Ruff lint                    BLOCKING
scenario/template validator  BLOCKING
```

Current advisory checks:

```text
Ruff formatting              INFORMATIONAL
mypy                         INFORMATIONAL
```

This is intentional. CI should catch likely runtime/configuration regressions without turning the repository red solely because AI-generated code has a formatting or annotation difference.

## When a screenshot should be sent to an AI assistant

A screenshot is usually **not needed** for:

- adding a pure feature flag;
- changing scenario sequencing already covered by tests;
- fixing a type annotation;
- documentation changes;
- refactoring a pure helper without changing its output.

A screenshot is strongly useful for:

- missed detections;
- false detections;
- OCR mistakes;
- wrong-row/target pairing;
- resolution-specific behavior;
- a UI state that the current templates do not distinguish correctly.

When possible, include the original unannotated screenshot rather than only a cropped or marked-up version, because surrounding geometry can matter to the automation.
