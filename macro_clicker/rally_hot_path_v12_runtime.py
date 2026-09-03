"""Keep exact three-team availability authoritative across world-count lag.

A 2026-09-03 live run proved that the v9 world-map squad-count staleness signal
could discard a correct fixed-slot cache too aggressively.  After Team 1 was
confirmed dispatched, the sidebar remained at 0/3 long enough for v9 to
invalidate the cache.  The Rally-page level filter then fell back to the broad
configured maximum, accepted Lv70, and clicked ``+`` even though the later fresh
fixed-slot detector still proved Team 1 BUSY and Teams 2/3 incapable.

v12 keeps the existing mob_2-style no-match recovery (Back -> reopen Rally) and
fixes the evidence ordering that decides whether a row is eligible:

* a confirmed dispatch remains authoritative while its expected count increment
  is merely delayed; missing the increment for the old 0.75 s settle window no
  longer invalidates exact identity;
* the expected increment may arrive later and still preserves the cache;
* unrelated count changes must persist on the proven world map before they may
  invalidate exact identity; and
* derived 0/3 receives a longer confirmation window because the repository has
  no dedicated 0/3 template and v9 infers it from the stable ``/3`` suffix.

This adds no sleep to Rally joining, does not change fixed Team identity, and
leaves the two-team scenario untouched.
"""

from __future__ import annotations

import time

from . import rally_hot_path_v9_runtime as _v9

BUILD_MARKER = "JOIN-HOT-RACE-v12 stable squad-count cache guard"
EXPECTED_COUNT_STALE_SECONDS = 30.0
COUNT_CHANGE_CONFIRM_SECONDS = 0.45
ZERO_COUNT_CHANGE_CONFIRM_SECONDS = 2.0

_INSTALLED = False
_ORIGINAL_START = None
_ORIGINAL_CACHE_EXACT = None


def _clear_count_candidate(engine):
    engine._rally_v12_pending_squad_count = None
    engine._rally_v12_pending_squad_since = 0.0


def _count_confirmation_seconds(count):
    return (
        ZERO_COUNT_CHANGE_CONFIRM_SECONDS
        if count == 0
        else COUNT_CHANGE_CONFIRM_SECONDS
    )


def _observe_squad_count(engine, count, now=None):
    """Debounce world-count staleness without weakening exact fixed-slot state."""

    if not isinstance(count, int) or count not in (0, 1, 2, 3):
        return False
    now = time.monotonic() if now is None else float(now)
    previous = getattr(engine, "_rally_v9_last_squad_count", None)
    expected = getattr(engine, "_rally_v9_expected_squad_count", None)

    if previous is None:
        engine._rally_v9_last_squad_count = count
        _clear_count_candidate(engine)
        return False

    if count == previous:
        _clear_count_candidate(engine)
        if expected is not None:
            since = float(getattr(engine, "_rally_v9_expected_count_since", 0.0))
            elapsed = max(0.0, now - since)
            if elapsed >= EXPECTED_COUNT_STALE_SECONDS:
                _v9._invalidate_team_cache(
                    engine,
                    "confirmed dispatch count never became observable for "
                    f"{elapsed:.1f}s (expected {expected}/3, observed {count}/3)",
                )
                engine._rally_v9_expected_squad_count = None
                engine._rally_v9_expected_count_since = 0.0
                return True
            if elapsed >= _v9.EXPECTED_COUNT_SETTLE_SECONDS:
                last_log = getattr(engine, "_rally_v12_last_expected_lag_log", None)
                log_key = (previous, expected)
                if last_log != log_key:
                    engine.log(
                        "  [team-cache] sidebar count still "
                        f"{count}/3 while confirmed dispatch expects {expected}/3; "
                        "preserving exact fixed-team cache"
                    )
                    engine._rally_v12_last_expected_lag_log = log_key
        return False

    if expected is not None and count == expected and count > previous:
        engine._rally_v9_last_squad_count = count
        engine._rally_v9_expected_squad_count = None
        engine._rally_v9_expected_count_since = 0.0
        engine._rally_v12_last_expected_lag_log = None
        _clear_count_candidate(engine)
        engine.log(
            f"  [team-cache] squad count {previous}/3 -> {count}/3 matches "
            "our confirmed dispatch; exact-team cache preserved"
        )
        return False

    candidate = getattr(engine, "_rally_v12_pending_squad_count", None)
    candidate_since = float(
        getattr(engine, "_rally_v12_pending_squad_since", 0.0)
    )
    required = _count_confirmation_seconds(count)
    if candidate != count:
        engine._rally_v12_pending_squad_count = count
        engine._rally_v12_pending_squad_since = now
        engine.log(
            f"  [team-cache] candidate world-map squad change {previous}/3 -> "
            f"{count}/3; require {required:g}s stable confirmation"
        )
        return False

    if now - candidate_since < required:
        return False

    engine._rally_v9_last_squad_count = count
    engine._rally_v9_expected_squad_count = None
    engine._rally_v9_expected_count_since = 0.0
    engine._rally_v12_last_expected_lag_log = None
    _clear_count_candidate(engine)
    _v9._invalidate_team_cache(
        engine,
        f"stable world-map squad count changed {previous}/3 -> {count}/3",
    )
    return True


def _poll_squad_count(engine, now=None):
    """Poll only outside an active Rally/formation transition."""

    now = time.monotonic() if now is None else float(now)
    if getattr(engine, "_rally_hot_entry_latched", False):
        return False
    if float(getattr(engine, "_rally_join_guard_until", 0.0)) > now:
        return False

    last_poll = float(getattr(engine, "_rally_v9_last_count_poll", 0.0))
    if now - last_poll < _v9.SQUAD_COUNT_POLL_SECONDS:
        return False
    engine._rally_v9_last_count_poll = now

    # RallyIcon is the cheap positive proof that the tiny sidebar ROI is being
    # interpreted as a world-map count rather than during Rally/formation UI.
    if not _v9._condition_visible(engine, {"RallyIcon.png"}):
        return False

    count = _v9._read_world_squad_count(engine)
    if count is None:
        return False
    return _observe_squad_count(engine, count, now=now)


def _cache_exact_fixed_states(engine, result):
    """A new authoritative fixed-slot capture cancels any pending count guess."""

    result_value = _ORIGINAL_CACHE_EXACT(engine, result)
    if getattr(engine, "_rally_v9_team_cache_valid", False):
        _clear_count_candidate(engine)
    return result_value


def install_rally_hot_path_v12_runtime():
    """Install stable world-count invalidation after v11."""

    global _INSTALLED
    global _ORIGINAL_START
    global _ORIGINAL_CACHE_EXACT
    if _INSTALLED:
        return

    from .engine import MacroEngine

    _ORIGINAL_START = MacroEngine.start
    _ORIGINAL_CACHE_EXACT = _v9._cache_exact_fixed_states

    def start(self):
        self._rally_v12_pending_squad_count = None
        self._rally_v12_pending_squad_since = 0.0
        self._rally_v12_last_expected_lag_log = None
        result = _ORIGINAL_START(self)
        if getattr(self, "_rally_hot_path_three_team", False):
            self.log(f"[build] {BUILD_MARKER} loaded")
        return result

    MacroEngine.start = start

    # v9's already-installed cycle and fixed-status wrapper resolve these
    # module globals at runtime, so replacing them here narrowly changes the
    # staleness evidence policy without rewriting the scheduler or selector.
    _v9._observe_squad_count = _observe_squad_count
    _v9._poll_squad_count = _poll_squad_count
    _v9._cache_exact_fixed_states = _cache_exact_fixed_states
    _INSTALLED = True
