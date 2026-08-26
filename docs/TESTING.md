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
- diagnostics and persistence behavior;
- BotConfig validation/persistence;
- Bot feature-adapter translation;
- BotController task serialization/queueing;
- the declared normal-user Bot page structure.

A passing unit suite means the encoded behavior still matches the regression tests. It does **not** prove that every live screen/template will be recognized correctly or that every Tk layout looks correct on the user's real Windows desktop.

## 2. Scenario/template validation

```powershell
python -m tools.validate_scenarios
```

This is a blocking CI check because broken JSON references, missing templates, malformed regions, or invalid persisted fields can make a saved workflow unusable even when Python tests pass.

Normal BotConfig settings are applied to deep-copied runtime scenarios. Adapter tests should additionally protect the mapping from user-facing settings to the known backend actions/fields without mutating the project-owned source scenario.

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

Formatting is informational in CI. A formatting difference does not imply the automation behavior is wrong.

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

A Linux/headless analysis environment may be able to run pure detection/OCR/model/BotConfig/controller tests but may not be able to import or execute the full automation stack without a display or Windows-specific dependencies.

### Bot UI tests

Do **not** construct a live `tk.Tk()` desktop window in the GitHub Actions test suite merely to prove that tabs exist. A hosted Windows Actions runner is not an interactive user desktop and live Tk construction can stall the suite.

CI should test the Bot UI's **structural contract** headlessly where possible, for example:

- declared page names;
- BotApp inheritance/integration hooks;
- config/adapters/controller behavior;
- parsing/validation of user-facing values.

The actual rendered Bot shell, hidden/revealed Advanced tools, sizing, keyboard focus, and visual usability should be checked during supervised Windows testing on the user's machine.

Do not change production code merely to make desktop-input modules work in an unrelated headless environment unless there is a real portability requirement.

The Windows GitHub Actions runner is the canonical **automated** Windows environment, but it is not a substitute for an interactive game/application desktop.

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

After a meaningful Rally/input timing change, verify at least:

- the correct target window is used;
- joins select the intended row;
- OCR filters the expected levels;
- Team 1 / Team 3 selection behaves as configured;
- recovery does not back out after a successful transition;
- kill switch/stop remains responsive.

After a meaningful Auto Gather change, verify:

- configured starting level is applied;
- search keeps lowering/retrying until found;
- free marches are dispatched without unnecessary team clicks;
- busy-march replacement follows the configured order;
- resource-taken Cancel/retry preserves the same logical dispatch;
- configured successful-march count stops correctly.

After Bot UI/control-layer changes, verify on the real Windows desktop:

- the application opens directly to the Bot interface;
- Advanced and Alert Setup are hidden during normal use but can be revealed;
- Rally settings visibly affect the intended runtime behavior;
- Gather settings visibly affect the intended runtime behavior;
- Start Bot serializes enabled finite tasks before continuous Rally;
- Stop Bot cancels the active task and pending queue;
- passive alerts can remain active beside one clicking automation;
- schedule Start/Stop behaves at the configured local times;
- the Logs page receives runtime messages.

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

Documentation-only changes are ignored by CI.

CI also uses per-branch concurrency with `cancel-in-progress: true`: when several AI-assisted commits arrive quickly, an older Windows run is superseded by the newest run instead of leaving a long queue of obsolete checks.

This is intentional. CI should catch likely runtime/configuration regressions without turning the repository red solely because generated code has a formatting or annotation difference.

## When a screenshot should be sent to an AI assistant

A screenshot is usually **not needed** for:

- adding a pure feature flag;
- changing BotConfig/controller logic already covered by tests;
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
- a UI state that the current templates do not distinguish correctly;
- unexpected rendered Bot UI layout or a control that is confusing on the real desktop.

When possible, include the original unannotated screenshot rather than only a cropped or marked-up version, because surrounding geometry can matter to the automation.