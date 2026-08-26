# Auto Gather workflow

`scenarios/Gather Gold.json` is the active resource-gathering workflow.

The visual flow stays scenario-driven, while the small amount of state that
must survive between repeated dispatches lives in
`macro_clicker/resource_gathering.py`.

## Why there is a controller

Do not encode the successful-dispatch count and replacement-march pointer by
creating separate scenario steps for every combination (for example S1/P3,
S2/P3, S2/P2). That approach works but duplicates the same detection and
action logic many times.

`GatherController` owns only:

- `successful_dispatches`;
- the current replacement index for the configured order;
- whether the dispatch currently being verified used a replacement march.

Screen detection, waits, target-window safety, clicking, retries, and step
transitions remain owned by `MacroEngine` and the scenario.

## Current policy

- Target: 3 verified gathering dispatches.
- Resource search starts at level 12 and falls back to 11, then 10.
- When a free march is available, the game auto-selects an available march;
  Auto Gather does not manually click a team before Dispatch on this path.
- A successful free-march dispatch does **not** consume the replacement pointer.
- When all marches are occupied, replacement priority is `3 -> 2 -> 1`.
- The no-free-march branch must select that replacement march **before** the
  normal Dispatch step is allowed to send it.
- The pointer advances only after that replacement dispatch is verified as
  successful.
- If the resource is taken before dispatch completes, click Cancel, retry the
  same logical dispatch, and keep the same replacement pointer.
- Stop only after three verified successes.

## Dispatch-panel detection

`templates/GatherDispatchButton.jpg` is intentionally a tight crop of the
static `出征` label on the blue Dispatch button. Keep the crop free of the
mouse cursor and the changing travel-time text.

A previous template accidentally included the cursor and part of the timer.
That made matching hover around the configured threshold: the first free march
could dispatch, while a later free march could stall on the open dispatch
panel even though the button was visibly present. If this template is ever
recaptured, use only stable button pixels.

## `gather_control` actions

The scenario uses one specialized action type instead of duplicated step
families:

- `select_replacement` — chooses the current replacement march relative to the
  detected no-free-march anchor and marks that dispatch as a replacement.
- `cancel_retry` — clears the pending replacement flag without advancing the
  pointer or success count.
- `record_success` — increments the success count, advances the pointer only
  when the verified dispatch used a replacement, and stops at the configured
  target count.

The controller is reset whenever `MacroEngine.start()` begins a run.

## Safety

The replacement click still goes through `MacroEngine._click_point`, so the
existing foreground-window, target-window geometry, monitor-boundary,
kill-switch, and fail-safe behavior remains in force.

Do not move gathering clicks to direct `pyautogui` calls.

## Regression coverage

Relevant tests:

- `tests/test_resource_gathering.py`
- `tests/test_auto_gather_scenario.py`

Keep these invariants protected when changing the workflow, especially the
free-march pointer behavior and taken-resource retry behavior.
