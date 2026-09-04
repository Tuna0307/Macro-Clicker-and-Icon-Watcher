"""Guard exact all-busy Team state from weak derived 0/3 false returns.

The world-map 0/3 value has no dedicated repository template; it is inferred
when the stable "/3" suffix is visible and none of the explicit 1/3, 2/3, 3/3
templates match.  A v24 live trace showed this weak negative inference
persisting long enough to invalidate a freshly corroborated all-busy cache:

    exact T1=BUSY T2=BUSY T3=BUSY
    positive sidebar 3/3
    -> derived 0/3 for ~2 s
    -> cache invalidated
    -> Rally entered
    -> fresh tray still proved BUSY/BUSY/BUSY

v26 adds a narrow evidence hierarchy only for that contradiction.  While the
exact fixed-Team cache says all three BUSY, a derived 0/3 sample must survive an
extra guard horizon before it is allowed into the existing v12 stable-change
logic.  Explicit 1/3 or 2/3 observations still delegate immediately and can
invalidate stale identity normally.  A broad positive 3/3 observation cancels
the derived-zero candidate immediately.

Final Attack behavior and legacy two-team behavior are unchanged.
"""

from __future__ import annotations

import time

from . import rally_hot_path_runtime as _hot
from . import rally_hot_path_v7_runtime as _v7
from . import rally_hot_path_v9_runtime as _v9
from . import rally_hot_path_v12_runtime as _v12
from .rally_team_policy import RALLY_TEAM_BUSY

BUILD_MARKER = "JOIN-HOT-RACE-v26 all-busy derived-zero guard"

# v12 will still require its existing 2 s stable confirmation after this guard
# opens, giving a total of about 5 s before a pure 3/3 -> derived 0/3 transition
# may invalidate exact all-busy identity.
DERIVED_ZERO_PRE_CONFIRM_SECONDS = 3.0
ZERO_GUARD_LOG_INTERVAL_SECONDS = 1.0
BROAD_THREE_CHECK_INTERVAL_SECONDS = 0.75

_INSTALLED = False
_ORIGINAL_START = None
_ORIGINAL_OBSERVE = None


def _exact_all_busy(engine):
    if not _hot._is_three_team(engine):
        return False
    if not getattr(engine, "_rally_v9_team_cache_valid", False):
        return False
    states = getattr(engine, "_rally_v9_team_states", None)
    if not isinstance(states, dict):
        return False
    return all(states.get(team) == RALLY_TEAM_BUSY for team in (1, 2, 3))


def _reset_zero_guard(engine):
    engine._rally_v26_zero_candidate_since = 0.0
    engine._rally_v26_zero_candidate_samples = 0
    engine._rally_v26_last_zero_log = 0.0
    engine._rally_v26_last_broad_check = 0.0


def _clear_v12_zero_candidate(engine):
    if getattr(engine, "_rally_v12_pending_squad_count", None) == 0:
        _v12._clear_count_candidate(engine)


def _broad_three_of_three_rate_limited(engine, now):
    last = float(getattr(engine, "_rally_v26_last_broad_check", 0.0))
    if now - last < BROAD_THREE_CHECK_INTERVAL_SECONDS:
        return None
    engine._rally_v26_last_broad_check = now
    return bool(_v7._broad_three_of_three(engine))


def _log_guard(engine, message, now):
    last = float(getattr(engine, "_rally_v26_last_zero_log", 0.0))
    if now - last < ZERO_GUARD_LOG_INTERVAL_SECONDS:
        return
    engine._rally_v26_last_zero_log = now
    engine.log(f"  [rally-v26] {message}")


def _observe_squad_count(engine, count, now=None):
    if not _hot._is_three_team(engine):
        return _ORIGINAL_OBSERVE(engine, count, now=now)

    now = time.monotonic() if now is None else float(now)

    if count != 0 or not _exact_all_busy(engine):
        if count != 0:
            _reset_zero_guard(engine)
        return _ORIGINAL_OBSERVE(engine, count, now=now)

    # 0/3 is the only weak derived count.  Do not let it immediately contradict
    # stronger exact fixed-Team BUSY/BUSY/BUSY evidence.
    _clear_v12_zero_candidate(engine)

    broad_three = _broad_three_of_three_rate_limited(engine, now)
    if broad_three is True:
        _reset_zero_guard(engine)
        engine.log(
            "  [rally-v26] derived 0/3 contradicted by positive broad 3/3 "
            "while exact Teams are all BUSY; ignored and cache preserved"
        )
        return False

    since = float(getattr(engine, "_rally_v26_zero_candidate_since", 0.0))
    samples = int(getattr(engine, "_rally_v26_zero_candidate_samples", 0)) + 1
    engine._rally_v26_zero_candidate_samples = samples

    if since <= 0.0:
        engine._rally_v26_zero_candidate_since = now
        _log_guard(
            engine,
            "derived 0/3 conflicts with exact T1=BUSY T2=BUSY T3=BUSY; "
            "starting extra return-evidence guard before v12 confirmation",
            now,
        )
        return False

    elapsed = max(0.0, now - since)
    if elapsed < DERIVED_ZERO_PRE_CONFIRM_SECONDS:
        _log_guard(
            engine,
            f"holding derived 0/3 contradiction for {elapsed:.2f}s "
            f"(samples={samples}); exact all-busy cache preserved",
            now,
        )
        return False

    _log_guard(
        engine,
        f"derived 0/3 persisted {elapsed:.2f}s (samples={samples}); "
        "releasing it to normal v12 stable-change confirmation",
        now,
    )
    return _ORIGINAL_OBSERVE(engine, count, now=now)


def install_rally_hot_path_v26_runtime():
    """Install the all-busy derived-zero guard after v25."""

    global _INSTALLED
    global _ORIGINAL_START
    global _ORIGINAL_OBSERVE
    if _INSTALLED:
        return

    from .engine import MacroEngine

    _ORIGINAL_START = MacroEngine.start
    _ORIGINAL_OBSERVE = _v12._observe_squad_count

    def start(self):
        _reset_zero_guard(self)
        result = _ORIGINAL_START(self)
        if _hot._is_three_team(self):
            self.log(f"[build] {BUILD_MARKER} loaded")
        return result

    MacroEngine.start = start

    # v12's installed cycle resolves its module-global observer at runtime.
    # v24 remains underneath this wrapper, so any sample released by v26 still
    # receives the detailed before/after diagnostic trace.
    _v12._observe_squad_count = _observe_squad_count
    _v9._observe_squad_count = _observe_squad_count
    _INSTALLED = True
