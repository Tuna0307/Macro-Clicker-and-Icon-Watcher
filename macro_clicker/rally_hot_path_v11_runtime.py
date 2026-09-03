"""Keep the v9 entry-only watchdog out of the formation transition.

A 2026-09-03 live run proved a second, independent race after v10.  The macro
successfully reached the Rally page, OCR'd an eligible row, revalidated the last
slot ``+`` and clicked it.  While the game was transitioning from the Rally page
to the formation screen, the world-map Rally icon became visible underneath the
panel.  v9 still had its *entry* watchdog armed, interpreted that icon as proof
that the original Rally-icon click had failed, and cleared Joining/Attack state
just before the real formation screen finished drawing.

v11 makes the watchdog phase-correct.  Once Rally-page/row progress has been
proven, the entry-only watch is permanently disarmed for that workflow.  It does
not add sleeps, does not change the legacy two-team path, and does not weaken the
final fixed-slot/Attack validation.
"""

from __future__ import annotations

import time

from . import rally_hot_path_runtime as _hot
from . import rally_hot_path_v9_runtime as _v9

BUILD_MARKER = "JOIN-HOT-RACE-v11 phase-correct entry watchdog"

_INSTALLED = False
_ORIGINAL_START = None
_ORIGINAL_RUN_ACTION = None
_ORIGINAL_V9_WATCH = None


def _clear_entry_watch(engine, reason=None):
    """Disarm only the v9 world-map -> Rally entry watch."""

    was_armed = getattr(engine, "_rally_v9_expect_rally_since", None) is not None
    engine._rally_v9_expect_rally_since = None
    if was_armed and reason:
        engine.log(f"  [rally-v11] entry watchdog disarmed ({reason})")
    return was_armed


def _formation_transition_started(engine, now=None):
    """Return True once the workflow is beyond the entry-only phase.

    ``_pending_rally_level`` is carried only after a Rally row has been resolved.
    ``_rally_join_guard_until`` is armed by a successful row ``+`` click while
    the Attack/formation UI opens.  Either is enough to prove that a later
    world-map RallyIcon is not evidence about the *original* entry click.
    """

    now = time.monotonic() if now is None else float(now)
    if getattr(engine, "_pending_rally_level", None) is not None:
        return True
    return float(getattr(engine, "_rally_join_guard_until", 0.0)) > now


def _guarded_entry_watch(engine, now=None):
    """Delegate to v9 only while still genuinely waiting for the Rally page."""

    if not _hot._is_three_team(engine):
        return _ORIGINAL_V9_WATCH(engine, now=now)

    if _formation_transition_started(engine, now=now):
        _clear_entry_watch(engine, "Rally row/formation transition already proven")
        return False

    return _ORIGINAL_V9_WATCH(engine, now=now)


def install_rally_hot_path_v11_runtime():
    """Install the phase-correct v9 entry-watch guard once."""

    global _INSTALLED
    global _ORIGINAL_START
    global _ORIGINAL_RUN_ACTION
    global _ORIGINAL_V9_WATCH
    if _INSTALLED:
        return

    from .engine import MacroEngine

    _ORIGINAL_START = MacroEngine.start
    _ORIGINAL_RUN_ACTION = MacroEngine._run_action
    _ORIGINAL_V9_WATCH = _v9._watch_entry_progress

    def start(self):
        result = _ORIGINAL_START(self)
        if _hot._is_three_team(self):
            self.log(f"[build] {BUILD_MARKER} loaded")
        return result

    def run_action(self, step, action, points, matches):
        if not _hot._is_three_team(self):
            return _ORIGINAL_RUN_ACTION(self, step, action, points, matches)

        name = getattr(step, "name", None)
        action_type = getattr(action, "type", None)
        result = _ORIGINAL_RUN_ACTION(self, step, action, points, matches)

        if name == "Joining" and action_type == "click_matching_row":
            # Reaching this action already means the Joining conditions were
            # positively READY on the Rally page.  Even if the final last-slot
            # revalidation cancels a stale click, the original world-map entry
            # succeeded, so v9 must never use RallyIcon to unwind this workflow.
            _clear_entry_watch(self, "Joining/Rally page positively reached")

        return result

    MacroEngine.start = start
    MacroEngine._run_action = run_action

    # v9's installed cycle resolves this module-global function at runtime, just
    # like v10 tightens v7's tray recovery.  Replacing it here keeps the change
    # narrowly scoped to the entry watchdog without rewriting the scheduler.
    _v9._watch_entry_progress = _guarded_entry_watch
    _INSTALLED = True
