"""Let persistent derived 0/3 evidence escape the v26 all-busy guard.

A 2026-09-05 v28 live run exposed a continuity bug in the v26 derived-zero
guard.  After a confirmed third-Team dispatch the exact cache correctly became
T1=BUSY T2=BUSY T3=BUSY while the world-map sidebar still showed 2/3 and the
confirmed dispatch expected 3/3.  The weak derived 0/3 detector then stayed
positive for many seconds.

v26 intentionally keeps derived 0/3 out of v12 for an extra three seconds, but
its implementation also cleared v12's pending zero-count candidate on every
sample *after* that guard had opened.  Once v14's 30-second unresolved-dispatch
horizon finally expired, v12 repeatedly logged a new
``2/3 -> 0/3; require 2s stable confirmation`` candidate whose age was reset to
zero on every poll.  The exact all-busy cache could therefore never invalidate,
so v22 hard-blocked Rally indefinitely even while the visible sidebar was 0/3.

v29 keeps the same evidence hierarchy but fixes the hand-off:

* during the first three seconds of a conflicting derived 0/3, v12's candidate
  is still cleared exactly as v26 intended;
* after that guard opens, v12 owns its candidate continuously and v29 no longer
  resets the confirmation timer on every sample;
* if an unresolved confirmed-dispatch expectation has already reached v12's
  existing 30-second stale horizon, the already-persistent zero evidence is
  seeded into v12 with its original observation time so v12 may immediately
  recognize that the required two-second stability has long been satisfied;
* any positive broad 3/3 proof still cancels the zero candidate immediately.

This only invalidates stale identity.  It never infers which Team returned.
Rally-row filtering may become less restrictive after invalidation, but final
Attack still requires a fresh fixed Team 1/2/3 read and fresh Attack target.
Legacy two-team behavior is untouched.
"""

from __future__ import annotations

import time

from . import rally_hot_path_runtime as _hot
from . import rally_hot_path_v9_runtime as _v9
from . import rally_hot_path_v12_runtime as _v12
from . import rally_hot_path_v26_runtime as _v26

BUILD_MARKER = "JOIN-HOT-RACE-v29 persistent-zero confirmation continuity"

_INSTALLED = False
_ORIGINAL_START = None
_UNDERLYING_OBSERVE = None


def _reset_v29_zero_state(engine, *, clear_v12_candidate=False):
    _v26._reset_zero_guard(engine)
    engine._rally_v29_zero_released = False
    engine._rally_v29_last_release_log = 0.0
    if clear_v12_candidate:
        _v12._clear_count_candidate(engine)


def _expected_age(engine, now):
    expected = getattr(engine, "_rally_v9_expected_squad_count", None)
    if not isinstance(expected, int):
        return None
    since = float(getattr(engine, "_rally_v9_expected_count_since", 0.0))
    if since <= 0.0:
        return None
    return max(0.0, now - since)


def _log_release(engine, message, now):
    last = float(getattr(engine, "_rally_v29_last_release_log", 0.0))
    if now - last < 1.0:
        return
    engine._rally_v29_last_release_log = now
    engine.log(f"  [rally-v29] {message}")


def _seed_persistent_zero_candidate_if_stale_expected(engine, now, zero_since):
    """Reuse already-stable zero evidence after the dispatch expectation expires."""

    expected_age = _expected_age(engine, now)
    if expected_age is None or expected_age < _v12.EXPECTED_COUNT_STALE_SECONDS:
        return False

    engine._rally_v12_pending_squad_count = 0
    engine._rally_v12_pending_squad_since = float(zero_since)
    _log_release(
        engine,
        "derived 0/3 remained persistent through the "
        f"{expected_age:.1f}s unresolved-dispatch horizon; "
        "reusing its original observation time for v12 stable-change confirmation",
        now,
    )
    return True


def _observe_squad_count(engine, count, now=None):
    if not _hot._is_three_team(engine):
        return _UNDERLYING_OBSERVE(engine, count, now=now)

    now = time.monotonic() if now is None else float(now)

    if count != 0 or not _v26._exact_all_busy(engine):
        _reset_v29_zero_state(engine)
        return _UNDERLYING_OBSERVE(engine, count, now=now)

    broad_three = _v26._broad_three_of_three_rate_limited(engine, now)
    if broad_three is True:
        _reset_v29_zero_state(engine, clear_v12_candidate=True)
        engine.log(
            "  [rally-v29] derived 0/3 contradicted by positive broad 3/3 "
            "while exact Teams are all BUSY; zero candidate cleared and cache preserved"
        )
        return False

    since = float(getattr(engine, "_rally_v26_zero_candidate_since", 0.0))
    samples = int(getattr(engine, "_rally_v26_zero_candidate_samples", 0)) + 1
    engine._rally_v26_zero_candidate_samples = samples

    if since <= 0.0:
        _v12._clear_count_candidate(engine)
        engine._rally_v26_zero_candidate_since = now
        engine._rally_v29_zero_released = False
        _log_release(
            engine,
            "derived 0/3 conflicts with exact T1=BUSY T2=BUSY T3=BUSY; "
            "starting the existing 3s pre-confirm guard",
            now,
        )
        return False

    elapsed = max(0.0, now - since)
    if elapsed < _v26.DERIVED_ZERO_PRE_CONFIRM_SECONDS:
        _v12._clear_count_candidate(engine)
        _log_release(
            engine,
            f"holding derived 0/3 contradiction for {elapsed:.2f}s "
            f"(samples={samples}); exact all-busy cache preserved",
            now,
        )
        return False

    if not getattr(engine, "_rally_v29_zero_released", False):
        engine._rally_v29_zero_released = True
        _log_release(
            engine,
            f"3s derived-zero guard satisfied after {elapsed:.2f}s; "
            "v12 candidate timer now remains continuous across samples",
            now,
        )

    _seed_persistent_zero_candidate_if_stale_expected(engine, now, since)

    result = _UNDERLYING_OBSERVE(engine, count, now=now)
    if result or not _v26._exact_all_busy(engine):
        _reset_v29_zero_state(engine)
    return result


def install_rally_hot_path_v29_runtime():
    """Install persistent-zero hand-off continuity after v28."""

    global _INSTALLED
    global _ORIGINAL_START
    global _UNDERLYING_OBSERVE
    if _INSTALLED:
        return

    from .engine import MacroEngine

    _ORIGINAL_START = MacroEngine.start
    _UNDERLYING_OBSERVE = _v26._ORIGINAL_OBSERVE

    def start(self):
        self._rally_v29_zero_released = False
        self._rally_v29_last_release_log = 0.0
        result = _ORIGINAL_START(self)
        if _hot._is_three_team(self):
            self.log(f"[build] {BUILD_MARKER} loaded")
        return result

    MacroEngine.start = start
    _v12._observe_squad_count = _observe_squad_count
    _v9._observe_squad_count = _observe_squad_count
    _INSTALLED = True
