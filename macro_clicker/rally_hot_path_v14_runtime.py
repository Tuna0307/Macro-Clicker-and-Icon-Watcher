"""Preserve exact Team availability across post-dispatch counter backtracking.

A 2026-09-04 live three-team run proved that a successful Team 3 dispatch could
be followed by a transient world-map squad-count observation that moved
backwards (1/3 -> 0/3) while the macro was still waiting for its own confirmed
dispatch to become visible as the expected 2/3 increment.  v12 treated that
backward count as an unrelated stable change and invalidated the exact fixed-Team
cache.  The Rally-page filter then fell back to the broad configured maximum and
clicked a Lv70 row + even though the still-correct fixed-Team state was
T1=BUSY, T2=IDLE, T3=BUSY and only the max60 Team 2 remained available.

v14 keeps the stronger exact fixed-Team evidence authoritative while an expected
post-dispatch count increment is unresolved.  Until v12's existing 30-second
expected-count stale horizon expires, any non-expected counter value is treated
as a transient observation only: it cannot advance a count-change candidate or
invalidate Team identity.  The expected increment can still arrive late and be
accepted normally.  After the existing stale horizon expires, v12's ordinary
stable-count invalidation policy resumes.

This is conservative: a real Team return during that short unresolved-dispatch
window may temporarily leave the cache too restrictive, but it cannot make an
incapable Team eligible or authorize an unsafe Rally + click.  Legacy two-team
behavior is untouched.
"""

from __future__ import annotations

import time

from . import rally_hot_path_runtime as _hot
from . import rally_hot_path_v9_runtime as _v9
from . import rally_hot_path_v12_runtime as _v12

BUILD_MARKER = "JOIN-HOT-RACE-v14 post-dispatch count backtrack guard"

_INSTALLED = False
_ORIGINAL_START = None
_ORIGINAL_OBSERVE = None


def _clear_pending_count_candidate(engine):
    engine._rally_v12_pending_squad_count = None
    engine._rally_v12_pending_squad_since = 0.0


def _observe_squad_count(engine, count, now=None):
    """Guard exact Team identity while our own dispatch count is unresolved."""

    now = time.monotonic() if now is None else float(now)
    if not _hot._is_three_team(engine):
        return _ORIGINAL_OBSERVE(engine, count, now=now)

    previous = getattr(engine, "_rally_v9_last_squad_count", None)
    expected = getattr(engine, "_rally_v9_expected_squad_count", None)
    since = float(getattr(engine, "_rally_v9_expected_count_since", 0.0))

    if (
        getattr(engine, "_rally_v9_team_cache_valid", False)
        and isinstance(previous, int)
        and isinstance(expected, int)
        and count != expected
        and max(0.0, now - since) < _v12.EXPECTED_COUNT_STALE_SECONDS
    ):
        # Do not let a transient/backward count erase the exact fixed-Team cache
        # while the count produced by our own positively confirmed dispatch is
        # still allowed to arrive late.  In particular, keep `previous` intact
        # so a later `count == expected` can still satisfy v12's normal
        # previous -> expected increment proof.
        _clear_pending_count_candidate(engine)
        log_key = (previous, count, expected)
        if getattr(engine, "_rally_v14_last_backtrack_log", None) != log_key:
            engine.log(
                "  [team-cache] transient squad count "
                f"{previous}/3 -> {count}/3 while confirmed dispatch expects "
                f"{expected}/3; preserving exact fixed-team cache"
            )
            engine._rally_v14_last_backtrack_log = log_key
        return False

    result = _ORIGINAL_OBSERVE(engine, count, now=now)
    if count == expected or expected is None:
        engine._rally_v14_last_backtrack_log = None
    return result


def install_rally_hot_path_v14_runtime():
    """Install the post-dispatch count backtrack guard after v13."""

    global _INSTALLED
    global _ORIGINAL_START
    global _ORIGINAL_OBSERVE
    if _INSTALLED:
        return

    from .engine import MacroEngine

    _ORIGINAL_START = MacroEngine.start
    _ORIGINAL_OBSERVE = _v12._observe_squad_count

    def start(self):
        self._rally_v14_last_backtrack_log = None
        result = _ORIGINAL_START(self)
        if _hot._is_three_team(self):
            self.log(f"[build] {BUILD_MARKER} loaded")
        return result

    MacroEngine.start = start

    # v12._poll_squad_count resolves its module-global _observe_squad_count at
    # runtime.  Replacing both aliases also keeps any direct v9 callers/tests
    # aligned with the same guard.
    _v12._observe_squad_count = _observe_squad_count
    _v9._observe_squad_count = _observe_squad_count
    _INSTALLED = True
