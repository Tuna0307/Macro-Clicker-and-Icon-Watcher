"""Prioritize profile-popup recovery before any new Rally entry.

A 2026-09-05 live v30 run proved that v17 correctly armed the existing
FriendStatus-based MisClick Profile recovery around a fixed formation-panel
outside click, but the next Rally-entry step could fire first. The base hot-path
entry action explicitly disarms ``_rally_hot_profile_armed`` before clicking
Rally, so the v17 recovery window was lost and the player profile popup remained
on screen.

v31 gives the already-armed profile recovery window priority over new Rally
entry. While v17 owns an active profile-recovery window, the normal
``Enter Rally after team probe`` step is blocked and the existing
``MisClick Profile`` step gets repeated chances to prove FriendStatus and close
the popup. If no popup exists, the unchanged v17 timeout expires and normal
Rally entry resumes.

No blind popup click is added. Legacy two-team behavior and final Attack safety
are unchanged.
"""

from __future__ import annotations

import time

from . import rally_hot_path_runtime as _hot
from . import rally_hot_path_v17_runtime as _v17

BUILD_MARKER = "JOIN-HOT-RACE-v31 profile-recovery entry priority"
PROFILE_TRACE_INTERVAL_SECONDS = 0.75

_INSTALLED = False
_ORIGINAL_START = None
_ORIGINAL_EVALUATE_STEP = None
_ORIGINAL_RUN_ACTION = None


def _profile_recovery_pending(engine, now=None):
    if not _hot._is_three_team(engine):
        return False
    if not bool(getattr(engine, "_rally_v17_profile_recovery_owned", False)):
        return False
    if not bool(getattr(engine, "_rally_hot_profile_armed", False)):
        return False
    deadline = float(getattr(engine, "_rally_v17_profile_recovery_until", 0.0))
    if deadline <= 0.0:
        return False
    when = time.monotonic() if now is None else float(now)
    return when <= deadline


def _log_profile_trace(engine, message, now):
    last = float(getattr(engine, "_rally_v31_last_profile_trace", 0.0))
    if now - last < PROFILE_TRACE_INTERVAL_SECONDS:
        return
    engine._rally_v31_last_profile_trace = now
    engine.log(f"  [rally-v31] {message}")


def _evaluate_step(engine, step, frame_cache=None):
    if not _hot._is_three_team(engine):
        return _ORIGINAL_EVALUATE_STEP(engine, step, frame_cache=frame_cache)

    name = getattr(step, "name", None)
    now = time.monotonic()

    if name == "Enter Rally after team probe" and _profile_recovery_pending(
        engine, now=now
    ):
        deadline = float(
            getattr(engine, "_rally_v17_profile_recovery_until", now)
        )
        _log_profile_trace(
            engine,
            "profile recovery owns input priority; new Rally entry blocked for "
            f"another {max(0.0, deadline - now):.2f}s",
            now,
        )
        return False, {}, {}

    result = _ORIGINAL_EVALUATE_STEP(engine, step, frame_cache=frame_cache)

    if name == "MisClick Profile" and _profile_recovery_pending(engine, now=now):
        deadline = float(
            getattr(engine, "_rally_v17_profile_recovery_until", now)
        )
        _log_profile_trace(
            engine,
            "profile recovery probe "
            f"{'READY' if bool(result[0]) else 'blocked'}; "
            f"window_rem={max(0.0, deadline - now):.2f}s",
            now,
        )
    return result


def _run_action(engine, step, action, points, matches):
    if not _hot._is_three_team(engine):
        return _ORIGINAL_RUN_ACTION(engine, step, action, points, matches)

    name = getattr(step, "name", None)
    action_type = getattr(action, "type", None)
    now = time.monotonic()

    # Defensive second gate. The engine is single-threaded, but do not let a
    # stale READY result disarm v17 if the recovery window still owns input.
    if (
        name == "Enter Rally after team probe"
        and action_type == "click"
        and _profile_recovery_pending(engine, now=now)
    ):
        _log_profile_trace(
            engine,
            "stale Rally-entry action suppressed because profile recovery still "
            "owns input",
            now,
        )
        return False

    return _ORIGINAL_RUN_ACTION(engine, step, action, points, matches)


def install_rally_hot_path_v31_runtime():
    """Install profile-recovery priority after v30."""

    global _INSTALLED
    global _ORIGINAL_START
    global _ORIGINAL_EVALUATE_STEP
    global _ORIGINAL_RUN_ACTION
    if _INSTALLED:
        return

    from .engine import MacroEngine

    _ORIGINAL_START = MacroEngine.start
    _ORIGINAL_EVALUATE_STEP = MacroEngine._evaluate_step
    _ORIGINAL_RUN_ACTION = MacroEngine._run_action

    def start(self):
        self._rally_v31_last_profile_trace = 0.0
        result = _ORIGINAL_START(self)
        if _hot._is_three_team(self):
            self.log(f"[build] {BUILD_MARKER} loaded")
        return result

    MacroEngine.start = start
    MacroEngine._evaluate_step = _evaluate_step
    MacroEngine._run_action = _run_action
    _INSTALLED = True
