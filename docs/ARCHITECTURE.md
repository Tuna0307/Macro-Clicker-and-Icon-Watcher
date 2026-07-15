# Architecture and maintenance guide

The source code lives in the `macro_clicker` package. Project-owned data stays
outside that package so reorganizing Python modules cannot silently relocate or
rewrite user assets.

## Module ownership

| Module | Responsibility |
| --- | --- |
| `app.py` | Main Macro Builder window, scenario editing, application lifecycle, and background step previews |
| `editors.py` | Condition, action, and step dialogs plus region-preview helpers |
| `alert_watcher.py` | Icon Alert controller, template management, watcher thread, and sound policy |
| `alert_settings.py` | Icon Alert settings model, validation, loading, and persistence |
| `alert_ui.py` | Screen/region pickers and alert popup presentation |
| `engine.py` | Scenario polling, condition evaluation, action dispatch, and safe input control |
| `rally_matching.py` | Atomic rally snapshots, same-row selection, level OCR decisions, and rally evidence |
| `detection_core.py` | Shared capture, scaling, template preparation, and match scoring |
| `models.py` | Scenario dataclasses, JSON conversion, validation, and scenario persistence |
| `level_ocr.py` | OCR engine lifecycle, preprocessing, text extraction, and confidence handling |
| `diagnostics.py` | Asynchronous bounded evidence and rotating decision metadata |
| `atomic_io.py` | Crash-safe JSON and PNG replacement |
| `project_paths.py` | Stable paths to project-owned scenarios, templates, and alert files |
| `runtime_paths.py` | Writable per-user logs, diagnostics, locks, and runtime state |
| `window_locator.py` | Window discovery and coordinate conversion |
| `capture_tool.py` | Interactive template and region capture |
| `ui_components.py` | Shared Tk styles and reusable controls |
| `app_helpers.py` | Scenario/step duplication and reference remapping |
| `log_maintenance.py` | Log rotation and age/count cleanup |

The `tools` package contains utilities that are useful during development but
are not imported by normal application startup.

## Data boundaries

- `templates/` and `scenarios/` are Macro Builder data.
- `alerts/settings.json` and `alerts/templates/` are Icon Alert data.
- These paths are centralized in `project_paths.py`. Runtime modules should not
  derive asset paths from their own `__file__` location.
- Writable logs and screenshots belong under the per-user directory exposed by
  `runtime_paths.py`, never in a repository `logs/` folder.
- Loading a scenario or manifest must not rewrite it. Persistence happens only
  in an explicit user save operation.

## Safe extension rules

1. Put reusable screen-matching behavior in `detection_core.py`; keep workflow
   policy in `engine.py`, `rally_matching.py`, or `alert_watcher.py`.
2. Add persisted fields through model parsing, validation, serialization, UI,
   and tests together. Preserve defaults for older JSON files.
3. Keep screenshots and encoding off the timing-critical macro loop whenever
   possible. Diagnostic writes use background workers.
4. Use `project_paths.py` for bundled data and `runtime_paths.py` for writable
   state.
5. Run pytest, Ruff, and mypy before changing stored templates or scenario
   formats.

## Complexity hotspots

The following areas remain intentionally behavior-compatible, but should be
the next candidates for small, test-backed extractions:

- `alert_watcher.py`: template manager loading, watcher loop, and main UI
  construction.
- `engine.py`: generic action dispatch and polling lifecycle.
- `rally_matching.py`: OCR arbitration and diagnostic serialization.
- `detection_core.py`: multiscale candidate generation and matching pipeline.
- `models.py`: scenario validation rules.

Avoid rewriting several of these at once. Extract one cohesive responsibility,
retain its public inputs/outputs, and run its focused tests before continuing.
