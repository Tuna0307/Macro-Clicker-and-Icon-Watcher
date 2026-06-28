# PC Macro Builder

A desktop tool, similar in spirit to Smart-AutoClicker, for building
game macros: drag-select icons on screen, define Steps with
Conditions (image present/absent) and Actions (click, key, wait,
enable/disable other steps), then run the scenario.

## Setup

```
pip install -r requirements.txt
python app.py
```

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
- **Action** -- `click`, `key`, `wait`, or `set_step` (enable/disable
  another step). Actions run top to bottom once a step's conditions
  are met.

## Building a sequence (step 1, then step 2, skip if not there)

This is what `set_step` actions are for. Load
`scenarios/example_loot_loop.json` to see it in action:

1. `step1_open_chest` starts enabled. When the chest image is found,
   it clicks it, disables itself, and enables `step2_collect_loot`.
2. `step2_collect_loot` starts **disabled** -- it's skipped entirely
   until step 1 turns it on. Once active, if the loot button isn't on
   screen yet, its condition just fails and it's re-checked next
   cycle. When it fires, it disables itself and re-enables step 1,
   looping back.
3. `dialogue_skip` stays enabled throughout and can interrupt at any
   time, independent of the chest/loot sequence.

Before running it for real, replace the three placeholder images in
`templates/` with actual captures from your game (use "Capture from
screen..." inside the condition editor).

## Tips

- Start `confidence` around 0.85; raise it if you get false
  positives, lower it if a real icon isn't detected.
- Use "Pick region..." on a condition to restrict matching to a small
  area (e.g. just your hotbar). If a target window is set first, that
  region follows the window when it moves and rescales when the window
  size changes.
- Use `click_matching_row` when one condition identifies the row and
  another condition is the button to click in that same row.
- The kill-switch key (default F12) stops the scenario instantly from
  anywhere, even while the game has focus.
