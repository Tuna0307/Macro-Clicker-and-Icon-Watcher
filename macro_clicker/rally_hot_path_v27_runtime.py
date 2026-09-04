"""Prioritize the time-critical Joining scan after Rally entry.

A 2026-09-04 v26 live run showed that successful Rally-row ``+`` clicks were
consistently taking about 2.3-2.5 seconds after the world-map Rally-icon click.
The actual Joining scan, OCR, level decision, and final ``+`` micro-revalidation
needed only about 0.36-0.54 seconds.  The large unlogged gap occurred after the
intentional 0.3-second Rally-entry settle and before Joining started.

The explicit three-team scenario still contains the older
``Probe fixed three-team status`` world-map step immediately ahead of Joining.
That probe is useful while genuinely on the world map, but once
``_rally_hot_entry_latched`` is true the macro has already committed to a Rally
workflow.  Evaluating the world-map probe at that point only consumes the
time-critical row-join window and may inspect transition pixels that no longer
belong to its intended phase.

v27 therefore bypasses only that world-map probe while a three-team Rally
workflow is latched.  The probe remains unchanged whenever the workflow is not
latched.  Joining itself is unchanged: GoldMob matching, same-row ``+`` pairing,
Team-cache level ceiling, OCR, stale-probe policy, and final ``+``
micro-revalidation all still run.  Final formation Team proof and Attack
revalidation are completely unchanged.  Legacy two-team behavior is untouched.
"""

from __future__ import annotations

import time

from . import rally_hot_path_runtime as _hot

BUILD_MARKER = "JOIN-HOT-RACE-v27 latched world-probe bypass"
PROBE_STEP_NAME = "Probe fixed three-team status"
JOINING_STEP_NAME = "Joining"
ENTRY_STEP_NAME = "Enter Rally after team probe"

_INSTALLED = False
_ORIGINAL_START = None
_ORIGINAL_EVALUATE_STEP = None
_ORIGINAL_RUN_ACTION = None


def _evaluate_step(engine, step, frame_cache=None):
    if not _hot._is_three_team(engine):
        return _ORIGINAL_EVALUATE_STEP(engine, step, frame_cache=frame_cache)

    name = getattr(step, "name", None)
    latched = bool(getattr(engine, "_rally_hot_entry_latched", False))

    if not latched:
        engine._rally_v27_probe_skip_logged = False

    if name == PROBE_STEP_NAME and latched:
        if not getattr(engine, "_rally_v27_probe_skip_logged", False):
            engine.log(
                "  [rally-v27] active Rally workflow latched; "
                "skipping world-map fixed-team probe so Joining gets the "
                "time-critical scan window"
            )
            engine._rally_v27_probe_skip_logged = True
        return False, {}, {}

    if (
        name == JOINING_STEP_NAME
        and latched
        and not getattr(engine, "_rally_v27_first_join_scan_logged", False)
    ):
        entered_at = getattr(engine, "_rally_v27_entry_click_at", None)
        if entered_at is not None:
            elapsed = max(0.0, time.monotonic() - float(entered_at))
            engine.log(
                "  [rally-v27] first Joining scan begins "
                f"{elapsed:.3f}s after Rally-icon click"
            )
        engine._rally_v27_first_join_scan_logged = True

    return _ORIGINAL_EVALUATE_STEP(engine, step, frame_cache=frame_cache)


def _run_action(engine, step, action, points, matches):
    result = _ORIGINAL_RUN_ACTION(engine, step, action, points, matches)

    if not _hot._is_three_team(engine):
        return result

    if (
        getattr(step, "name", None) == ENTRY_STEP_NAME
        and getattr(action, "type", None) == "click"
        and result
    ):
        engine._rally_v27_entry_click_at = time.monotonic()
        engine._rally_v27_first_join_scan_logged = False
        engine._rally_v27_probe_skip_logged = False

    return result


def install_rally_hot_path_v27_runtime():
    """Install the latched world-map probe bypass after v26."""

    global _INSTALLED
    global _ORIGINAL_START
    global _ORIGINAL_EVALUATE_STEP
    global _ORIGINAL_RUN_ACTION
    if _INSTALLED:
        return

    from .engine import MacroEngine

    _ORIGINAL_START = MacroEngine.start
    _ORIGINAL_EVALUATE_STEP = MacroEngine._evaluate_step
    _ORIGINAL_RUN_ACTION = MacroEngine._run_action

    def start(self):
        self._rally_v27_entry_click_at = None
        self._rally_v27_first_join_scan_logged = False
        self._rally_v27_probe_skip_logged = False
        result = _ORIGINAL_START(self)
        if _hot._is_three_team(self):
            self.log(f"[build] {BUILD_MARKER} loaded")
        return result

    MacroEngine.start = start
    MacroEngine._evaluate_step = _evaluate_step
    MacroEngine._run_action = _run_action
    _INSTALLED = True
