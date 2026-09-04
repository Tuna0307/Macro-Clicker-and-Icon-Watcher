"""Clear stale formation-transition guards after final three-team aborts.

A 2026-09-04 long live run exposed a workflow-generation race.  After a Rally
row opened the formation screen, the normal 2.5-second
``_rally_join_guard_until`` transition guard was still active when the final
fixed Team check correctly found no capable idle Team and aborted without
dispatch.  The next world-map Rally entry could begin before that old deadline
expired.  v11 then mistook the previous workflow's guard for proof that the new
entry had already reached the formation transition and disarmed the v9 entry
watchdog.  If that fresh Rally click did not open the Rally page, the latch
could remain stuck indefinitely.

v20 clears only the previous formation-transition guard when the explicit
three-team final-abort path completes.  The fixed Team proof, Attack safety,
Profile recovery, Rally-page filtering, and legacy two-team path are unchanged.
"""

from __future__ import annotations

from . import rally_hot_path_runtime as _hot

BUILD_MARKER = "JOIN-HOT-RACE-v20 abort transition-guard reset"

_INSTALLED = False
_ORIGINAL_START = None
_ORIGINAL_ABORT_THREE_TEAM_DISPATCH = None


def _abort_three_team_dispatch(engine, action, result, reason):
    """Run the existing abort, then retire the completed workflow's join guard."""

    outcome = _ORIGINAL_ABORT_THREE_TEAM_DISPATCH(engine, action, result, reason)
    if not _hot._is_three_team(engine):
        return outcome

    previous_guard = float(getattr(engine, "_rally_join_guard_until", 0.0))
    engine._rally_join_guard_until = 0.0
    if previous_guard > 0.0:
        engine.log(
            "  [rally-v20] final abort cleared prior formation-transition guard "
            "before next Rally entry"
        )
    return outcome


def install_rally_hot_path_v20_runtime():
    """Install the final-abort transition-guard reset once."""

    global _INSTALLED
    global _ORIGINAL_START
    global _ORIGINAL_ABORT_THREE_TEAM_DISPATCH
    if _INSTALLED:
        return

    from .engine import MacroEngine

    _ORIGINAL_START = MacroEngine.start
    _ORIGINAL_ABORT_THREE_TEAM_DISPATCH = MacroEngine._abort_three_team_dispatch

    def start(self):
        result = _ORIGINAL_START(self)
        if _hot._is_three_team(self):
            self.log(f"[build] {BUILD_MARKER} loaded")
        return result

    MacroEngine.start = start
    MacroEngine._abort_three_team_dispatch = _abort_three_team_dispatch
    _INSTALLED = True
