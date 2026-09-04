"""Expire stale expected squad-count increments before they can mask a return.

A 2026-09-04 live three-team run exposed a narrow ambiguity in the v12/v14
world-map count guard.  After Team 1 had been dispatched, an old expected count
increment remained pending while the Rally icon was not positively available for
normal count polling.  More than a minute later the sidebar changed 0/3 -> 1/3.
Because the value happened to equal the old expected increment, v12 treated it
as delayed proof of our dispatch and preserved the exact cache.  The cache still
said Team 1 BUSY, so valid Lv70 rows were rejected at the stale max60 ceiling.

The existing v12 policy already defines a 30-second horizon after which an
unobserved expected increment is stale.  v18 applies that same horizon to the
*arrival* branch too.  If an expected increment first appears after the stale
horizon, it is no longer allowed to prove our old dispatch.  Instead the expected
marker is cleared and the sample goes through v12's ordinary stable world-map
count-change confirmation.  A confirmed change invalidates only the stale exact
Team identity cache.  The next formation screen still performs the existing
fresh fixed Team 1/2/3 proof before any Attack input.

This does not infer which Team returned, does not make an UNKNOWN Team eligible,
does not weaken final dispatch validation, and leaves the two-team path unchanged.
"""

from __future__ import annotations

import time

from . import rally_hot_path_runtime as _hot
from . import rally_hot_path_v9_runtime as _v9
from . import rally_hot_path_v12_runtime as _v12
from . import rally_hot_path_v14_runtime as _v14

BUILD_MARKER = "JOIN-HOT-RACE-v18 stale expected-count expiry"

_INSTALLED = False
_ORIGINAL_START = None
_ORIGINAL_OBSERVE = None


def _observe_squad_count(engine, count, now=None):
    """Do not let an over-age expected increment masquerade as dispatch proof."""

    now = time.monotonic() if now is None else float(now)
    if not _hot._is_three_team(engine):
        return _ORIGINAL_OBSERVE(engine, count, now=now)
    if not isinstance(count, int) or count not in (0, 1, 2, 3):
        return _ORIGINAL_OBSERVE(engine, count, now=now)

    previous = getattr(engine, "_rally_v9_last_squad_count", None)
    expected = getattr(engine, "_rally_v9_expected_squad_count", None)
    since = float(getattr(engine, "_rally_v9_expected_count_since", 0.0))
    elapsed = max(0.0, now - since)

    if (
        getattr(engine, "_rally_v9_team_cache_valid", False)
        and isinstance(previous, int)
        and isinstance(expected, int)
        and count == expected
        and count > previous
        and elapsed >= _v12.EXPECTED_COUNT_STALE_SECONDS
    ):
        # Once the expected-dispatch token is this old, a matching world count
        # can no longer identify the change as *our* dispatch.  It may be a Team
        # return or another unrelated sidebar transition.  Drop only the stale
        # expectation, then let v12 debounce the count as ordinary change
        # evidence.  Exact Team identity remains valid until that change is
        # positively stable.
        engine._rally_v9_expected_squad_count = None
        engine._rally_v9_expected_count_since = 0.0
        engine._rally_v12_last_expected_lag_log = None
        engine._rally_v14_last_backtrack_log = None
        _v12._clear_count_candidate(engine)
        engine.log(
            "  [rally-v18] expected squad increment "
            f"{previous}/3 -> {count}/3 arrived after {elapsed:.1f}s; "
            "treating it as ordinary count-change evidence, not old dispatch proof"
        )

    return _ORIGINAL_OBSERVE(engine, count, now=now)


def install_rally_hot_path_v18_runtime():
    """Install stale expected-count expiry after v17."""

    global _INSTALLED
    global _ORIGINAL_START
    global _ORIGINAL_OBSERVE
    if _INSTALLED:
        return

    from .engine import MacroEngine

    _ORIGINAL_START = MacroEngine.start
    _ORIGINAL_OBSERVE = _v14._observe_squad_count

    def start(self):
        result = _ORIGINAL_START(self)
        if _hot._is_three_team(self):
            self.log(f"[build] {BUILD_MARKER} loaded")
        return result

    MacroEngine.start = start

    # v12._poll_squad_count resolves its module-global observer at runtime.
    # Keep the v9 alias aligned as well so direct callers use the same policy.
    _v12._observe_squad_count = _observe_squad_count
    _v9._observe_squad_count = _observe_squad_count
    _INSTALLED = True
