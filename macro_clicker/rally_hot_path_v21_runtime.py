"""Probe restrictive exact-Team caches sooner for high-level Rally rows.

A 2026-09-04 live run showed why v19's original ~30-second stale horizon can
still miss short-lived Lv70/Lv80 opportunities.  Team 1 was freshly dispatched
at 21:11:29, so the exact cache correctly became T1=BUSY with a max60 Rally-row
ceiling.  Lv80 appeared from 21:11:41 onward with a correctly paired Join ``+``,
but each attempt was rejected at max60 because the cache was only ~12-21 seconds
old.  The row disappeared before v19's 30-second refresh-probe age was reached.

The final Attack path is already fail-closed: widening the Rally-page ceiling
only permits opening the formation screen.  Attack still requires a fresh fixed
Team 1/2/3 read, a capable IDLE Team, the configured delay, and a fresh
Attack.png revalidation.

v21 therefore lets the existing v19 formation-refresh probe treat a restrictive
exact cache as probe-eligible after 10 seconds in explicit three-team mode.  The
existing 10-second probe retry cooldown remains unchanged, so this does not turn
high-level rows into rapid repeated ``+`` spam.  Legacy two-team behavior is
unchanged.
"""

from __future__ import annotations

from . import rally_hot_path_runtime as _hot
from . import rally_hot_path_v19_runtime as _v19
from . import rally_matching as _rm

BUILD_MARKER = "JOIN-HOT-RACE-v21 fast stale-cache formation probe"
FAST_PROBE_AGE_SECONDS = 10.0

_INSTALLED = False
_ORIGINAL_START = None
_ORIGINAL_AVAILABLE_LEVEL_CAP = None


def _available_rally_team_level_cap(engine, action):
    """Delegate to v19 using the shorter three-team refresh-probe age."""

    if not _hot._is_three_team(engine):
        return _ORIGINAL_AVAILABLE_LEVEL_CAP(engine, action)

    previous = _v19.STALE_CACHE_PROBE_AGE_SECONDS
    _v19.STALE_CACHE_PROBE_AGE_SECONDS = FAST_PROBE_AGE_SECONDS
    try:
        return _ORIGINAL_AVAILABLE_LEVEL_CAP(engine, action)
    finally:
        _v19.STALE_CACHE_PROBE_AGE_SECONDS = previous


def install_rally_hot_path_v21_runtime():
    """Install the shorter bounded three-team stale-cache probe age once."""

    global _INSTALLED
    global _ORIGINAL_START
    global _ORIGINAL_AVAILABLE_LEVEL_CAP
    if _INSTALLED:
        return

    from .engine import MacroEngine

    _ORIGINAL_START = MacroEngine.start
    _ORIGINAL_AVAILABLE_LEVEL_CAP = _rm.RallyMatchingMixin._available_rally_team_level_cap

    def start(self):
        result = _ORIGINAL_START(self)
        if _hot._is_three_team(self):
            self.log(f"[build] {BUILD_MARKER} loaded")
        return result

    def available_rally_team_level_cap(self, action):
        return _available_rally_team_level_cap(self, action)

    MacroEngine.start = start
    _rm.RallyMatchingMixin._available_rally_team_level_cap = (
        available_rally_team_level_cap
    )
    _INSTALLED = True
