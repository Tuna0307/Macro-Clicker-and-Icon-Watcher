"""Refresh stale exact-Team cache through a bounded formation-screen probe.

A 2026-09-04 live three-team run proved that v18 still depended too heavily on
sidebar-count evidence.  Team 1 had been dispatched and the exact cache therefore
restricted Rally rows to the max60 Teams 2/3.  More than two minutes later the
bot repeatedly saw and OCR-read a Lv70 GoldMob row, but kept rejecting it at the
stale max60 ceiling.  When a later Lv30 row finally opened the formation screen,
the fresh fixed-slot capture proved T1=IDLE T2=IDLE T3=IDLE.  The Lv70 rows had
therefore been skipped only because the exact cache had aged without a usable
sidebar change to invalidate it.

v19 adds one narrow escape hatch.  When an exact three-team cache is at least as
old as v12's existing 30-second stale horizon *and* its cached level ceiling is
narrower than the configured selector ceiling, the Rally-row prefilter may use
the configured ceiling for one short refresh-probe window.  That can click a
valid GoldMob row ``+`` only to reach the existing formation screen, where Team
1/2/3 are freshly and fail-closed re-read before any Attack input.

A successful fresh fixed-slot capture immediately ends the probe and refreshes
the cache age.  If the formation screen never becomes valid, a cooldown prevents
rapid repeated stale-cache probes.  This never infers which Team returned, never
makes UNKNOWN eligible for final dispatch, and leaves the two-team path
unchanged.
"""

from __future__ import annotations

import time

from . import rally_hot_path_runtime as _hot
from . import rally_hot_path_v9_runtime as _v9
from . import rally_hot_path_v12_runtime as _v12
from . import rally_matching as _rm

BUILD_MARKER = "JOIN-HOT-RACE-v19 stale-cache formation refresh probe"
STALE_CACHE_PROBE_AGE_SECONDS = _v12.EXPECTED_COUNT_STALE_SECONDS
PROBE_WINDOW_SECONDS = 4.0
PROBE_RETRY_COOLDOWN_SECONDS = 10.0

_INSTALLED = False
_ORIGINAL_START = None
_ORIGINAL_AVAILABLE_LEVEL_CAP = None
_ORIGINAL_CAPTURE_FIXED_STATUS = None


def _clear_refresh_probe(engine):
    engine._rally_v19_probe_until = 0.0
    engine._rally_v19_probe_cache_captured_at = 0.0


def _configured_selector_ceiling(engine):
    """Return the broad editable selector ceiling without using cached BUSY state."""

    resolved = _v9._selector_limits(engine)
    if resolved is None:
        return _rm._TEAM_LEVEL_CAP_UNSET
    _selector, priority, limits = resolved
    finite = []
    for team_number in priority:
        value = limits.get(team_number)
        if value is None:
            return _rm._TEAM_LEVEL_CAP_UNBOUNDED
        try:
            finite.append(int(value))
        except (TypeError, ValueError, OverflowError):
            return _rm._TEAM_LEVEL_CAP_UNSET
    return max(finite) if finite else _rm._TEAM_LEVEL_CAP_UNSET


def _is_broader_cap(broad, cached):
    if broad is _rm._TEAM_LEVEL_CAP_UNSET:
        return False
    if broad == _rm._TEAM_LEVEL_CAP_UNBOUNDED:
        return cached != _rm._TEAM_LEVEL_CAP_UNBOUNDED
    if cached is None:
        return True
    if cached in {_rm._TEAM_LEVEL_CAP_UNSET, _rm._TEAM_LEVEL_CAP_UNBOUNDED}:
        return False
    try:
        return int(broad) > int(cached)
    except (TypeError, ValueError, OverflowError):
        return False


def _cap_text(value):
    if value is None:
        return "none"
    if value is _rm._TEAM_LEVEL_CAP_UNSET:
        return "unset"
    if value == _rm._TEAM_LEVEL_CAP_UNBOUNDED:
        return "unbounded"
    return str(value)


def _available_rally_team_level_cap(engine, action):
    cached = _ORIGINAL_AVAILABLE_LEVEL_CAP(engine, action)
    if not _hot._is_three_team(engine):
        return cached
    if getattr(action, "type", None) != "click_matching_row":
        return cached
    if not getattr(engine, "_rally_v9_team_cache_valid", False):
        _clear_refresh_probe(engine)
        return cached

    captured_at = float(getattr(engine, "_rally_v9_team_cache_captured_at", 0.0))
    if captured_at <= 0.0:
        return cached

    broad = _configured_selector_ceiling(engine)
    if not _is_broader_cap(broad, cached):
        _clear_refresh_probe(engine)
        return cached

    now = time.monotonic()
    age = max(0.0, now - captured_at)
    if age < STALE_CACHE_PROBE_AGE_SECONDS:
        _clear_refresh_probe(engine)
        return cached

    probe_source = float(
        getattr(engine, "_rally_v19_probe_cache_captured_at", 0.0)
    )
    probe_until = float(getattr(engine, "_rally_v19_probe_until", 0.0))

    # Keep the same widened ceiling throughout the one Joining/revalidation
    # attempt.  If a fresh fixed-slot capture has changed the cache timestamp,
    # this probe is no longer allowed to carry forward.
    if probe_source == captured_at and now <= probe_until:
        return broad
    if probe_source != captured_at:
        _clear_refresh_probe(engine)

    last_probe = float(getattr(engine, "_rally_v19_last_probe_at", 0.0))
    if last_probe > 0.0 and now - last_probe < PROBE_RETRY_COOLDOWN_SECONDS:
        return cached

    engine._rally_v19_last_probe_at = now
    engine._rally_v19_probe_cache_captured_at = captured_at
    engine._rally_v19_probe_until = now + PROBE_WINDOW_SECONDS
    engine.log(
        "  [rally-v19] exact Team cache is stale "
        f"({age:.1f}s; cached ceiling={_cap_text(cached)}, "
        f"configured ceiling={_cap_text(broad)}); allowing one formation refresh probe"
    )
    return broad


def install_rally_hot_path_v19_runtime():
    """Install stale-cache formation refresh probing after v18."""

    global _INSTALLED
    global _ORIGINAL_START
    global _ORIGINAL_AVAILABLE_LEVEL_CAP
    global _ORIGINAL_CAPTURE_FIXED_STATUS
    if _INSTALLED:
        return

    from .engine import MacroEngine

    _ORIGINAL_START = MacroEngine.start
    _ORIGINAL_AVAILABLE_LEVEL_CAP = _rm.RallyMatchingMixin._available_rally_team_level_cap
    _ORIGINAL_CAPTURE_FIXED_STATUS = _rm.RallyMatchingMixin._capture_fixed_rally_team_status

    def start(self):
        self._rally_v19_probe_until = 0.0
        self._rally_v19_probe_cache_captured_at = 0.0
        self._rally_v19_last_probe_at = 0.0
        result = _ORIGINAL_START(self)
        if _hot._is_three_team(self):
            self.log(f"[build] {BUILD_MARKER} loaded")
        return result

    def available_rally_team_level_cap(self, action):
        return _available_rally_team_level_cap(self, action)

    def capture_fixed_rally_team_status(self):
        before = float(getattr(self, "_rally_v9_team_cache_captured_at", 0.0))
        result = _ORIGINAL_CAPTURE_FIXED_STATUS(self)
        after = float(getattr(self, "_rally_v9_team_cache_captured_at", 0.0))
        if _hot._is_three_team(self) and after > 0.0 and after != before:
            _clear_refresh_probe(self)
        return result

    MacroEngine.start = start
    _rm.RallyMatchingMixin._available_rally_team_level_cap = (
        available_rally_team_level_cap
    )
    _rm.RallyMatchingMixin._capture_fixed_rally_team_status = (
        capture_fixed_rally_team_status
    )
    _INSTALLED = True
