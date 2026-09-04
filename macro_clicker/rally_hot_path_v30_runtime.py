"""Keep all-busy return detection alive when RallyIcon is temporarily hidden.

A 2026-09-05 v29 live run showed that the macro could be logically stuck for
more than a minute after all three Teams had been dispatched.  The exact fixed
Team cache correctly held T1=BUSY T2=BUSY T3=BUSY, but v12's normal world-map
count poll requires RallyIcon.png to be positively visible before it reads the
tiny squad-count ROI.  When that icon was hidden by a world-map UI state, no
fresh squad-count samples reached v29/v12 for about 113 seconds, so the all-busy
cache had no chance to observe return evidence.

v30 adds a no-input fallback only while an exact all-busy cache is valid and no
Rally/formation transition is active.  If normal RallyIcon-gated polling has
been silent for a short period, it reads the same tiny squad-count ROI directly
and feeds the result into the already-installed v29/v12 confirmation policy.
It never infers Team identity and never authorizes Attack.  Derived 0/3 remains
subject to v29's extra guard; explicit 1/3, 2/3 and 3/3 keep their existing
confirmation/corroboration semantics.

Legacy two-team behavior is untouched.
"""

from __future__ import annotations

import time

from . import rally_hot_path_runtime as _hot
from . import rally_hot_path_v9_runtime as _v9
from . import rally_hot_path_v12_runtime as _v12
from . import rally_hot_path_v26_runtime as _v26

BUILD_MARKER = "JOIN-HOT-RACE-v30 all-busy count fallback watcher"
NO_SAMPLE_FALLBACK_SECONDS = 2.0
FALLBACK_POLL_SECONDS = 0.25
FALLBACK_LOG_INTERVAL_SECONDS = 1.0

_INSTALLED = False
_ORIGINAL_START = None
_ORIGINAL_POLL_SQUAD_COUNT = None
_ORIGINAL_READ_WORLD_SQUAD_COUNT = None


def _record_count_sample(engine, count, now=None):
    if not _hot._is_three_team(engine):
        return
    if not isinstance(count, int) or count not in (0, 1, 2, 3):
        return
    when = time.monotonic() if now is None else float(now)
    engine._rally_v30_last_count_sample_at = when
    engine._rally_v30_last_sample_value = count


def _read_world_squad_count(engine):
    """Record successful normal samples without changing their meaning."""

    count = _ORIGINAL_READ_WORLD_SQUAD_COUNT(engine)
    _record_count_sample(engine, count)
    return count


def _fallback_allowed(engine, now):
    if not _hot._is_three_team(engine):
        return False
    if not _v26._exact_all_busy(engine):
        return False
    if getattr(engine, "_rally_hot_entry_latched", False):
        return False
    if float(getattr(engine, "_rally_join_guard_until", 0.0)) > now:
        return False
    return True


def _log_fallback(engine, message, now):
    last = float(getattr(engine, "_rally_v30_last_fallback_log", 0.0))
    if now - last < FALLBACK_LOG_INTERVAL_SECONDS:
        return
    engine._rally_v30_last_fallback_log = now
    engine.log(f"  [rally-v30] {message}")


def _poll_squad_count(engine, now=None):
    now = time.monotonic() if now is None else float(now)

    # Preserve the normal v12 path first.  Its call into _v9._read_world_squad_count
    # now records successful RallyIcon-gated samples through the wrapper above.
    result = _ORIGINAL_POLL_SQUAD_COUNT(engine, now=now)
    if not _fallback_allowed(engine, now):
        return result

    last_sample = float(getattr(engine, "_rally_v30_last_count_sample_at", 0.0))
    if last_sample <= 0.0:
        engine._rally_v30_last_count_sample_at = now
        return result

    silence = max(0.0, now - last_sample)
    if silence < NO_SAMPLE_FALLBACK_SECONDS:
        return result

    last_fallback = float(getattr(engine, "_rally_v30_last_fallback_poll", 0.0))
    if now - last_fallback < FALLBACK_POLL_SECONDS:
        return result
    engine._rally_v30_last_fallback_poll = now

    # Deliberately bypass only v12's RallyIcon precondition.  The underlying
    # reader still captures the same tiny counter ROI and applies the same
    # explicit 1/2/3 templates / derived-0 policy.  No input is sent.
    count = _ORIGINAL_READ_WORLD_SQUAD_COUNT(engine)
    if not isinstance(count, int) or count not in (0, 1, 2, 3):
        _log_fallback(
            engine,
            f"normal count polling silent for {silence:.2f}s; direct tiny-ROI "
            "fallback could not prove 0/3..3/3 (no input sent)",
            now,
        )
        return result

    _record_count_sample(engine, count, now=now)
    suffix = "; 0/3 remains derived evidence under v29 guard" if count == 0 else ""
    _log_fallback(
        engine,
        f"normal count polling silent for {silence:.2f}s; direct tiny-ROI "
        f"fallback observed {count}/3 (no input sent){suffix}",
        now,
    )

    observed_change = _v12._observe_squad_count(engine, count, now=now)
    return bool(result or observed_change)


def install_rally_hot_path_v30_runtime():
    """Install the all-busy count fallback watcher after v29."""

    global _INSTALLED
    global _ORIGINAL_START
    global _ORIGINAL_POLL_SQUAD_COUNT
    global _ORIGINAL_READ_WORLD_SQUAD_COUNT
    if _INSTALLED:
        return

    from .engine import MacroEngine

    _ORIGINAL_START = MacroEngine.start
    _ORIGINAL_POLL_SQUAD_COUNT = _v9._poll_squad_count
    _ORIGINAL_READ_WORLD_SQUAD_COUNT = _v9._read_world_squad_count

    def start(self):
        now = time.monotonic()
        self._rally_v30_last_count_sample_at = now
        self._rally_v30_last_sample_value = None
        self._rally_v30_last_fallback_poll = 0.0
        self._rally_v30_last_fallback_log = 0.0
        result = _ORIGINAL_START(self)
        if _hot._is_three_team(self):
            self.log(f"[build] {BUILD_MARKER} loaded")
        return result

    MacroEngine.start = start
    _v9._read_world_squad_count = _read_world_squad_count
    _v9._poll_squad_count = _poll_squad_count
    _INSTALLED = True
