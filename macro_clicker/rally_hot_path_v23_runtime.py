"""Reconcile world-map squad counts with the authoritative fixed-Team cache.

A 2026-09-04 v22 live run showed that the world-map count expectation was still
computed as ``last_sidebar_count + 1`` after a confirmed dispatch.  That is only
safe when the sidebar is current.  In the live run it was often stale at 0/3, so
dispatching the third known Team still armed an expectation of 1/3 even though
the fresh fixed-slot cache now proved T1=BUSY T2=BUSY T3=BUSY.  A real 3/3
observation was therefore misclassified as a transient non-expected count and
the v22 hard gate could remain stuck for more than two minutes.

The same mismatch caused repeated cache invalidations in partial-busy states.
For example, a fresh cache with exactly T1=BUSY should be corroborated by a
later positive 1/3 sidebar read, not invalidated merely because an older sidebar
sample had been 0/3.

v23 makes the exact fixed-Team cache authoritative for *count cardinality* while
it remains valid:

* a confirmed dispatch expects the number of BUSY fixed Team slots, rather than
  ``old sidebar + 1``;
* a positive world-map count equal to that BUSY cardinality corroborates the
  cache and clears stale dispatch/count-change bookkeeping;
* a different stable world-map count still delegates to the existing v12/v14/v18
  return/change policy, so a Team return can invalidate stale identity normally;
* no-match Back refreshes receive a short one-second re-entry debounce to remove
  the most visible open/back/reopen thrash without weakening any Team or Attack
  checks; and
* v22 full-squad gate logging is throttled to five seconds to keep Activity
  readable while the hard gate is legitimately active.

Final Attack validation is unchanged and remains fail-closed.  The legacy
two-team path is untouched.
"""

from __future__ import annotations

import time

from . import rally_hot_path_runtime as _hot
from . import rally_hot_path_v9_runtime as _v9
from . import rally_hot_path_v12_runtime as _v12
from . import rally_hot_path_v13_runtime as _v13
from . import rally_hot_path_v14_runtime as _v14
from . import rally_hot_path_v22_runtime as _v22
from .rally_team_policy import RALLY_TEAM_BUSY, RALLY_TEAM_IDLE

BUILD_MARKER = "JOIN-HOT-RACE-v23 busy-count reconciliation"
NO_MATCH_REENTRY_COOLDOWN_SECONDS = 1.0
FULL_SQUAD_GATE_LOG_INTERVAL_SECONDS = 5.0

_INSTALLED = False
_ORIGINAL_START = None
_ORIGINAL_EVALUATE_STEP = None
_ORIGINAL_NO_MATCH_FALLBACK = None
_ORIGINAL_MARK_DISPATCHED_TEAM_BUSY = None
_ORIGINAL_OBSERVE_SQUAD_COUNT = None


def _exact_states(engine):
    if not _hot._is_three_team(engine):
        return None
    if not getattr(engine, "_rally_v9_team_cache_valid", False):
        return None
    states = getattr(engine, "_rally_v9_team_states", None)
    if not isinstance(states, dict):
        return None
    if any(
        states.get(team) not in {RALLY_TEAM_IDLE, RALLY_TEAM_BUSY}
        for team in (1, 2, 3)
    ):
        return None
    return states


def _exact_busy_count(engine):
    states = _exact_states(engine)
    if states is None:
        return None
    return sum(states[team] == RALLY_TEAM_BUSY for team in (1, 2, 3))


def _clear_count_bookkeeping(engine):
    engine._rally_v9_expected_squad_count = None
    engine._rally_v9_expected_count_since = 0.0
    engine._rally_v12_last_expected_lag_log = None
    engine._rally_v14_last_backtrack_log = None
    _v12._clear_count_candidate(engine)


def _mark_dispatched_team_busy(engine, team_number, now=None):
    """Keep v9's exact state update, then align its expectation to BUSY cardinality."""

    result = _ORIGINAL_MARK_DISPATCHED_TEAM_BUSY(engine, team_number, now=now)
    if not _hot._is_three_team(engine):
        return result

    busy_count = _exact_busy_count(engine)
    if busy_count is None:
        return result

    old_expected = getattr(engine, "_rally_v9_expected_squad_count", None)
    when = time.monotonic() if now is None else float(now)
    engine._rally_v9_expected_squad_count = busy_count
    engine._rally_v9_expected_count_since = when
    _v12._clear_count_candidate(engine)
    engine._rally_v12_last_expected_lag_log = None
    engine._rally_v14_last_backtrack_log = None

    if old_expected != busy_count:
        old_text = "none" if old_expected is None else str(old_expected)
        engine.log(
            "  [rally-v23] dispatch expectation aligned to exact BUSY count: "
            f"{old_text}/3 -> {busy_count}/3"
        )
    return result


def _observe_squad_count(engine, count, now=None):
    """Treat count == exact BUSY cardinality as corroboration, not invalidation."""

    if not _hot._is_three_team(engine):
        return _ORIGINAL_OBSERVE_SQUAD_COUNT(engine, count, now=now)
    if not isinstance(count, int) or count not in (0, 1, 2, 3):
        return _ORIGINAL_OBSERVE_SQUAD_COUNT(engine, count, now=now)

    busy_count = _exact_busy_count(engine)
    if busy_count is None or count != busy_count:
        return _ORIGINAL_OBSERVE_SQUAD_COUNT(engine, count, now=now)

    previous = getattr(engine, "_rally_v9_last_squad_count", None)
    expected = getattr(engine, "_rally_v9_expected_squad_count", None)
    had_candidate = getattr(engine, "_rally_v12_pending_squad_count", None) is not None

    engine._rally_v9_last_squad_count = count
    _clear_count_bookkeeping(engine)

    if previous != count or expected is not None or had_candidate:
        engine.log(
            "  [rally-v23] world-map squad count "
            f"{count}/3 corroborates exact fixed-Team BUSY count {busy_count}/3; "
            "cache preserved"
        )
    return False


def _run_no_match_fallback(engine, step, action, points):
    result = _ORIGINAL_NO_MATCH_FALLBACK(engine, step, action, points)
    if (
        result
        and _hot._is_three_team(engine)
        and _v13._uses_back_fallback(step, action)
        and not getattr(engine, "_retry_current_step", False)
    ):
        engine._rally_v23_entry_not_before = (
            time.monotonic() + NO_MATCH_REENTRY_COOLDOWN_SECONDS
        )
    return result


def _evaluate_step(engine, step, frame_cache=None):
    if (
        _hot._is_three_team(engine)
        and getattr(step, "name", None) == "Enter Rally after team probe"
        and time.monotonic()
        < float(getattr(engine, "_rally_v23_entry_not_before", 0.0))
    ):
        return False, {}, {}
    return _ORIGINAL_EVALUATE_STEP(engine, step, frame_cache=frame_cache)


def install_rally_hot_path_v23_runtime():
    """Install BUSY-count reconciliation and light no-match pacing after v22."""

    global _INSTALLED
    global _ORIGINAL_START
    global _ORIGINAL_EVALUATE_STEP
    global _ORIGINAL_NO_MATCH_FALLBACK
    global _ORIGINAL_MARK_DISPATCHED_TEAM_BUSY
    global _ORIGINAL_OBSERVE_SQUAD_COUNT
    if _INSTALLED:
        return

    from .engine import MacroEngine

    _ORIGINAL_START = MacroEngine.start
    _ORIGINAL_EVALUATE_STEP = MacroEngine._evaluate_step
    _ORIGINAL_NO_MATCH_FALLBACK = MacroEngine._run_no_match_fallback
    _ORIGINAL_MARK_DISPATCHED_TEAM_BUSY = _v9._mark_dispatched_team_busy
    _ORIGINAL_OBSERVE_SQUAD_COUNT = _v12._observe_squad_count

    def start(self):
        self._rally_v23_entry_not_before = 0.0
        result = _ORIGINAL_START(self)
        if _hot._is_three_team(self):
            self.log(f"[build] {BUILD_MARKER} loaded")
        return result

    MacroEngine.start = start
    MacroEngine._evaluate_step = _evaluate_step
    MacroEngine._run_no_match_fallback = _run_no_match_fallback

    # v9's already-installed dispatch wrapper resolves this module-global helper
    # at runtime.
    _v9._mark_dispatched_team_busy = _mark_dispatched_team_busy

    # v12's cycle resolves its module-global observer at runtime.  Keep the v9
    # alias aligned for direct callers/tests.
    _v12._observe_squad_count = _observe_squad_count
    _v9._observe_squad_count = _observe_squad_count

    # The v22 hard gate remains unchanged; only reduce repetitive Activity spam.
    _v22.GATE_LOG_INTERVAL_SECONDS = FULL_SQUAD_GATE_LOG_INTERVAL_SECONDS
    _INSTALLED = True
