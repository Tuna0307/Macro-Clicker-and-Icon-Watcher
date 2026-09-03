# Activity Log — Clear View

The Macro Builder Activity pane now exposes a **Clear log** button beside the existing **Collapse** control.

## Behavior

`Clear log` is intentionally non-destructive:

- it immediately clears the visible Activity text;
- it drains messages that were already queued for the Activity view, so old lines do not immediately reappear;
- it resets the Activity line counter used for the 1000-line UI retention limit;
- it does **not** stop or restart the running macro;
- it does **not** reset scenario or Rally runtime state;
- it does **not** delete or truncate the persistent `pc_macro_builder.log` file.

A private `---- activity view cleared ----` marker is appended to the persistent disk log so debugging history still records where the operator started a fresh visual observation.

New runtime messages continue appearing normally after the view has been cleared.

## Installation

The control is installed for both supported desktop launch paths:

- `launcher.pyw` (used by `Run PC Macro Builder.bat` / `.vbs`);
- `python -m macro_clicker`.

The implementation lives in `macro_clicker/activity_clear_runtime.py` and deliberately does not change Rally detection, dispatch, safety, or timing behavior.
