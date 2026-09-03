"""Release the three-team Rally entry latch after a successful no-match Back.

The v12 level filter correctly rejects Rally rows above the ceiling of the
remaining known-IDLE teams and uses ``click_matching_row``'s existing no-match
fallback to click ``BackButton.png``.  A 2026-09-04 live run exposed one missing
piece: that internal fallback does not execute the named ``Back if wrong mob``
or ``Back if no slot`` actions, so v7's normal entry-latch release hook never
runs.  The Rally page closes, but the still-latched world-map entry step then
refuses to click the visible Rally icon again.

v13 is intentionally narrow.  It wraps only the existing no-match fallback and,
after a positively successful BackButton fallback from the three-team ``Joining``
step, releases the same entry latch that the normal Back recovery actions release.
It does not change OCR, level ceilings, Team identity, selector priority,
dispatch timing, or the legacy two-team path.
"""

from __future__ import annotations

import os

from . import rally_hot_path_runtime as _hot

BUILD_MARKER = "JOIN-HOT-RACE-v13 no-match Back latch release"

_INSTALLED = False
_ORIGINAL_START = None
_ORIGINAL_NO_MATCH_FALLBACK = None


def _uses_back_fallback(step, action):
    if getattr(step, "name", None) != "Joining":
        return False
    if getattr(action, "type", None) != "click_matching_row":
        return False
    index = getattr(action, "no_match_condition_index", None)
    conditions = getattr(step, "conditions", ()) or ()
    if not isinstance(index, int) or index < 0 or index >= len(conditions):
        return False
    template_path = getattr(conditions[index], "template_path", "") or ""
    return os.path.basename(template_path).casefold() == "backbutton.png"


def _run_no_match_fallback(engine, step, action, points):
    """Delegate the fallback, then mirror normal Back recovery latch release."""

    was_latched = bool(getattr(engine, "_rally_hot_entry_latched", False))
    result = _ORIGINAL_NO_MATCH_FALLBACK(engine, step, action, points)

    if (
        result
        and was_latched
        and _hot._is_three_team(engine)
        and _uses_back_fallback(step, action)
        and not getattr(engine, "_retry_current_step", False)
    ):
        engine._rally_hot_entry_latched = False
        engine.log(
            "  [rally-v13] no-match Back completed; Rally entry latch released "
            "for world-map refresh"
        )
    return result


def install_rally_hot_path_v13_runtime():
    """Install no-match Back latch release after v12."""

    global _INSTALLED
    global _ORIGINAL_START
    global _ORIGINAL_NO_MATCH_FALLBACK
    if _INSTALLED:
        return

    from .engine import MacroEngine

    _ORIGINAL_START = MacroEngine.start
    _ORIGINAL_NO_MATCH_FALLBACK = MacroEngine._run_no_match_fallback

    def start(self):
        result = _ORIGINAL_START(self)
        if _hot._is_three_team(self):
            self.log(f"[build] {BUILD_MARKER} loaded")
        return result

    MacroEngine.start = start
    MacroEngine._run_no_match_fallback = _run_no_match_fallback
    _INSTALLED = True
