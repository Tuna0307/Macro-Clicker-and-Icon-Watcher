# PC Macro Builder

A desktop tool, similar in spirit to Smart-AutoClicker, for building
game macros: drag-select icons on screen, define Steps with
Conditions (image present/absent) and Actions (click, key, wait,
enable/disable other steps), then run the scenario.

## Setup

Using a project virtual environment is recommended so OCR and image-processing
packages do not conflict with packages installed for other programs:

```powershell
py -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m macro_clicker
```

The dependency ranges are bounded to compatible major versions. On Windows,
`Run PC Macro Builder.bat` starts the GUI without a console and automatically
uses `.venv` when it exists. Startup failures
are shown in a dialog and recorded under
`%LOCALAPPDATA%\Macro Clicker and Icon Watcher\logs`. Set
`MACRO_CLICKER_DATA_DIR` to use a different runtime-log location.

For development checks:

```powershell
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\python -m ruff check .
.\.venv\Scripts\python -m ruff format --check .
.\.venv\Scripts\python -m mypy macro_clicker tools
.\.venv\Scripts\python -m tools.validate_scenarios
```

## Interface preferences

The application uses a bright CustomTkinter/ttk hybrid interface.
Open **Scenario settings** to configure the scenario Start/Stop keys and the
global interface sound and animation preferences. Interface preferences are
stored in the per-user data folder and apply to both Macro Builder and Icon
Alerts. In Icon Alerts, select an item and toggle **Detect this icon** (or press
Space) to choose exactly which saved icons are scanned.

## Project layout

```text
macro_clicker/   Application, detection, OCR, diagnostics, and UI code
tools/           Developer validation utilities
tests/           Automated tests
templates/       Macro image assets and level digit references
scenarios/       Saved macro scenarios
alerts/          Icon Alert settings and templates
docs/            Architecture and maintenance notes
launcher.pyw     Windows GUI entry point used by the launch scripts
```

Generated caches and runtime logs do not belong in the repository. Runtime
logs and diagnostic captures are written to the per-user data directory shown
below. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for module ownership and
safe extension points.

On Windows, run your terminal as Administrator if your game runs
elevated -- otherwise clicks/keys sent by `pyautogui`/`keyboard` won't
reach it.

## Window targeting

If you move the game window around, fill in **Target window title
contains** with part of the game window title before creating or editing
conditions. For example, if the title bar says `My Game - Profile 1`,
enter `My Game`.

When a target window is set:

- Conditions without a picked region search inside the target window
  instead of the whole monitor.
- New picked regions are saved relative to the target window, so they
  follow the game if you drag it somewhere else.
- New picked regions also store proportional coordinates, so if the
  target window is resized, the region is recalculated against the new
  window size.
- If the window cannot be found while running, the step is skipped
  instead of clicking in the wrong place.

Leave the field blank to use the old full-screen / absolute-region
behavior.

## Shared detection foundation

Macro Builder and Icon Alerts use the same `macro_clicker/detection_core.py` implementation
for DPI-aware BGR screen captures, monitor selection, window/monitor-relative
regions, template scaling, colored-text masking, rotations, low-variance
safety, bounded variant preparation, coarse search, and match scoring.
Workflow-specific behavior remains separate: Macro Builder turns matches into
actions, while Icon Alerts applies confirmation, cooldown, sound, and popup
policies.

When a template has a reference window/monitor size, the matcher inserts the
exact current width and height scales before its fallback scales. For example,
moving a full-screen game from 1920x1080 to 2560x1440 adds an exact 1.333333x
candidate. A change of aspect ratio also gets an exact independent x/y variant
instead of being forced into one approximate scalar. New screen captures save
their reference size. Legacy Macro conditions safely try the known historical
window sizes in the scenario without rewriting its JSON; explicit new template
metadata takes priority.

The matching implementation is shared, while each workflow keeps its safe
legacy fallback range: Icon Alerts retains its broad 0.50x-1.50x search and
Macro Builder retains its faster 0.80x-1.20x fallback. Known monitor/window
resolution changes are not limited by those ranges because their exact scale
is inserted automatically.

When a target window is configured, both workflows follow that window to the
physical monitor it currently occupies. When no target window is used, newly
picked regions are saved relative to the selected monitor, so changing from a
1920x1080 monitor to a 2560x1440 monitor moves and scales the region. Existing
legacy `screen` regions remain absolute to avoid silently changing saved
behavior.

Macro matches also carry their exact x/y scale into detected-target offsets,
matching-row tolerance, and level OCR regions.
Legacy fixed `x`/`y` click actions remain absolute screen coordinates; use a
detected condition target for portable cross-monitor actions.

## Matching a row

For list-style game screens where the correct click depends on the
same row, use the **click_matching_row** action.

Example:

```
Conditions:
[0] Mob.png
[1] Join.png

Action:
click_matching_row
  Row reference condition #: 0
  Click condition #: 1
```

This means: find all `Mob.png` matches, find all `Join.png` matches,
then click the first join button whose vertical row lines up with one
of the `Mob.png` matches. The step scans top to bottom, so it will
prefer the first valid rally row.

For auto rally where each row has multiple open plus slots and you want
to click the last available slot in every valid row:

```
Action:
click_matching_row
  Row reference condition #: 0
  Click condition #: 1
  Rows: all
  Target choice: rightmost
```

This clicks the rightmost matching target in each valid row, top to
bottom.

For level-filtered rows, the mob anchors, join targets, and all six OCR crop
offsets come from one atomic screenshot. The snapshot is expanded to include
the level area even when the mob search region itself is tightly cropped. If
the level is unreadable, or the selected row changes during the configured
pre-click delay, later actions in that step are aborted and the step retries on
the next poll without consuming its cooldown.

## Concepts

- **Scenario** -- a named, saved set of Steps (`scenarios/*.json`).
  Switch between them from the dropdown at the top.
- **Step** -- one row in the list. Has Conditions and Actions. Checked
  every poll cycle, top to bottom. If its conditions aren't on screen
  right now, it's simply skipped that cycle -- no error, no blocking.
- **Condition** -- a template image to look for (and an optional
  "Negate" toggle so the condition succeeds when the image is
  *absent* instead). Multiple conditions on one step are combined
  with AND or OR. Use "Capture from screen..." to drag-select the
  icon directly instead of cropping screenshots by hand.
- **Action** -- `click`, `click_matching_row`, `key`, `wait`, or `set_step`
  (enable/disable another step). Actions run top to bottom once a step's
  conditions are met.

## Included scenarios

The repository includes one-team and two-team rally scenarios. They use
`set_step` actions to move between rally detection, joining, confirmation, and
safe back-out states. Their template paths are project-relative, so the folder
can be moved to another computer without rewriting the scenario JSON.

Scenario JSON files are editable runtime configuration. Personal level limits,
delays, and similar tuning can remain as uncommitted local changes; commit them
only when they are intended to become the project defaults.

## Detection types

Alert templates and Macro Builder conditions use the same detection types:

- **Text / colored text** isolates the foreground text color so translucent or
  changing backgrounds do not become part of the match. Text does not use
  rotation. Near-exact matches alert immediately; other passing matches are
  confirmed against a second chat-region capture after 100 ms.
- **Static picture** searches the configured scales without rotated variants.
- **Animated/rotating picture** searches the configured scales at 0, ±5, and
  ±8 degrees for icons that visibly tilt or wobble.

The Icon Alerts **Grayscale pictures** option applies to its picture templates;
Macro Builder stores the same choice per condition. New templates and old Macro
  conditions default to **Static picture**. Older alert manifests without a
detection type retain the previous animated/rotating behavior.

## Tips

- Start `confidence` around 0.85; raise it if you get false
  positives, lower it if a real icon isn't detected.
- Use "Pick region..." on a condition to restrict matching to a small
  area (e.g. just your hotbar). If a target window is set first, that
  region follows the window when it moves and rescales when the window
  size changes.
- Use `click_matching_row` when one condition identifies the row and
  another condition is the button to click in that same row.
- Each scenario can configure its own start key (default F8) and stop key
  under **Scenario settings**. The two keys must be different.
- The kill-switch key (default F12) is required before a scenario starts and
  is checked between captures, matches, and every action, even while the game
  has focus.

## Diagnostic evidence

Macro Builder scenarios collect bounded diagnostic evidence by default. The
setting can be disabled in **Scenario settings**. Icon Alerts does not collect
screenshots. Events are stored under:

```text
%LOCALAPPDATA%\Macro Clicker and Icon Watcher\logs\diagnostics
```

The collector uses a selective policy so a long-running macro does not save a
full screenshot for every normal check:

- Unread OCR, provisional OCR conflicts, accepted OCR below 95%, and row
  changes keep full evidence in `critical`.
- Reference/target template near misses are retained in `samples` at most once
  every five minutes.
- A repeated `no eligible row` result is retained in `samples` at most once
  every ten minutes.
- Strong successful reads (95% or higher) are sampled at most once every 30
  minutes.
- Visually similar screenshots are deduplicated for five minutes.

Full events are split into `critical` and `samples` directories. Each contains
`metadata.json`, a JPEG annotated context screenshot, and lossless PNG OCR
crops. Rally records include row and target matches, template scores, OCR
text/confidence, all crop offsets, level limits, and the
final decision.

Every rally decision is also appended without screenshots to the rotating
`decisions.jsonl` log. It is limited to 5 MB plus three backups. Screenshot
retention keeps up to 175 critical events and 25 routine success samples, with
the existing overall limits of 200 events, seven days, and 500 MB. Automatic
labels are only diagnostic classifications; determining whether an event is
truly a false positive or false negative still requires reviewing the evidence.

