"""Hard-stop Rally probing while all three squads are positively out.

A 2026-09-04 live run exposed two related gaps after v21:

* the exact fixed-Team cache could be T1=BUSY T2=BUSY T3=BUSY (cached
  level ceiling ``None``), but v19/v21 still treated that old all-busy cache as
  eligible for a stale formation-refresh probe and clicked a Rally row ``+``;
* v7 could positively detect world-map ``3/3`` and suppress one Rally-entry
  evaluation, then a transient miss on the next scan allowed the ordinary entry
  step to fire a fraction of a second later.

v22 makes positive full-squad evidence a hard gate.  An exact all-busy Team
cache, a stable world-map 3/3 count, or a short hold after positive broad 3/3
evidence blocks both Rally entry and Rally-row ``+`` probing.

The final Attack path is unchanged and remains fail-closed.  The legacy
two-team path is untouched.
"""

from __future__ import annotations

import time

from . import rally_hot_path_runtime as _hot
from . import rally_hot_path_v7_runtime as _v7
from . import rally_hot_path_v19_runtime as _v19
from . import rally_matching as _rm
from .rally_team_policy import RALLY_TEAM_BUSY

BUILD_MARKER = "JOIN-HOT-RACE-v22 full-squad hard gate"
BROAD_FULL_SQUAD_HOLD_SECONDS = 2.0
GATE_LOG_INTERVAL_SECONDS = 1.0

_INSTALLED = False
_ORIGINAL_START = None
_ORIGINAL_EVALUATE_STEP = None
_ORIGINAL_AVAILABLE_LEVEL_CAP = None
_ORIGINAL_BROAD_THREE_OF_THREE = None


def _exact_all_busy(engine):
    if not _hot._is_three_team(engine):
        return False
    if not getattr(engine, "_rally_v9_team_cache_valid", False):
        return False
    states = getattr(engine, "_rally_v9_team_states", None)
    if not isinstance(states, dict):
        return False
    return all(states.get(team) == RALLY_TEAM_BUSY for team in (1, 2, 3))


def _hard_full_squad_reason(engine, now=None):
    """Return positive evidence that Rally entry/row ``+`` must be blocked."""

    if not _hot._is_three_team(engine):
        return None
    if _exact_all_busy(engine):
        return "exact fixed Team cache is T1=BUSY T2=BUSY T3=BUSY"
    if getattr(engine, "_rally_v9_last_squad_count", None) == 3:
        return "stable world-map squad count is 3/3"

    now = time.monotonic() if now is None else float(now)
    hold_until = float(getattr(engine, "_rally_v22_full_squad_hold_until", 0.0))
    if hold_until > now:
        return "recent positive broad 3/3 evidence"
    return None


def _log_gate(engine, message, now=None):
    now = time.monotonic() if now is None else float(now)
    last = float(getattr(engine, "_rally_v22_last_gate_log", 0.0))
    if now - last < GATE_LOG_INTERVAL_SECONDS:
        return
    engine._rally_v22_last_gate_log = now
    engine.log(f"  [rally-v22] {message}")


def _sticky_broad_three_of_three(engine):
    """Hold a positive v7 broad 3/3 hit across brief detector misses."""

    matched = bool(_ORIGINAL_BROAD_THREE_OF_THREE(engine))
    if matched and _hot._is_three_team(engine):
        now = time.monotonic()
        engine._rally_v22_full_squad_hold_until = max(
            float(getattr(engine, "_rally_v22_full_squad_hold_until", 0.0)),
            now + BROAD_FULL_SQUAD_HOLD_SECONDS,
        )
    return matched


def _evaluate_step(engine, step, frame_cache=None):
    if not _hot._is_three_team(engine):
        return _ORIGINAL_EVALUATE_STEP(engine, step, frame_cache=frame_cache)

    if getattr(step, "name", None) == "Enter Rally after team probe":
        reason = _hard_full_squad_reason(engine)
        if reason is not None:
            _v19._clear_refresh_probe(engine)
            _log_gate(
                engine,
                f"{reason}; Rally entry hard-blocked until return evidence",
            )
            return False, {}, {}

    return _ORIGINAL_EVALUATE_STEP(engine, step, frame_cache=frame_cache)


def _available_rally_team_level_cap(engine, action):
    """Never widen a Rally row while positive evidence says all squads are out."""

    if not _hot._is_three_team(engine):
        return _ORIGINAL_AVAILABLE_LEVEL_CAP(engine, action)
    if getattr(action, "type", None) != "click_matching_row":
        return _ORIGINAL_AVAILABLE_LEVEL_CAP(engine, action)

    reason = _hard_full_squad_reason(engine)
    if reason is None:
        return _ORIGINAL_AVAILABLE_LEVEL_CAP(engine, action)

    _v19._clear_refresh_probe(engine)
    _log_gate(
        engine,
        f"{reason}; stale-cache formation probe blocked, no Rally + allowed",
    )
    # ``None`` is the existing no-capable-IDLE-team ceiling.
    return None


def install_rally_hot_path_v22_runtime():
    """Install the full-squad hard gate after v21."""

    global _INSTALLED
    global _ORIGINAL_START
    global _ORIGINAL_EVALUATE_STEP
    global _ORIGINAL_AVAILABLE_LEVEL_CAP
    global _ORIGINAL_BROAD_THREE_OF_THREE
    if _INSTALLED:
        return

    from .engine import MacroEngine

    _ORIGINAL_START = MacroEngine.start
    _ORIGINAL_EVALUATE_STEP = MacroEngine._evaluate_step
    _ORIGINAL_AVAILABLE_LEVEL_CAP = _rm.RallyMatchingMixin._available_rally_team_level_cap
    _ORIGINAL_BROAD_THREE_OF_THREE = _v7._broad_three_of_three

    def start(self):
        self._rally_v22_full_squad_hold_until = 0.0
        self._rally_v22_last_gate_log = 0.0
        result = _ORIGINAL_START(self)
        if _hot._is_three_team(self):
            self.log(f"[build] {BUILD_MARKER} loaded")
        return result

    MacroEngine.start = start
    MacroEngine._evaluate_step = _evaluate_step
    _rm.RallyMatchingMixin._available_rally_team_level_cap = (
        _available_rally_team_level_cap
    )

    # v7 resolves this module-global helper at runtime.  Remembering a positive
    # hit for two seconds prevents a one-frame detector miss from undoing a
    # just-proven world-map 3/3 gate.
    _v7._broad_three_of_three = _sticky_broad_three_of_three
    _INSTALLED = True
