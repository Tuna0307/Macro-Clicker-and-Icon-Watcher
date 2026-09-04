"""Recover player-profile popups opened by fixed-panel dismissal clicks.

A 2026-09-04 live run exposed a gap in the existing MisClick Profile gate.  In
three-team mode, a final Team check can prove T1/T2/T3 all BUSY.  The normal
abort path then dismisses the fixed formation panel with a map-adjacent click
above its validated anchor.  That click is intentionally outside the panel, so
it can land on a player/base underneath and open the player-profile popup.

The original hot path disarms MisClick Profile when ``select_rally_team`` starts.
Consequently the popup opened by the abort/dismiss click was never evaluated and
the Rally loop stalled indefinitely.

v17 treats the fixed-panel dismissal itself as a risky click.  In explicit
three-team mode it arms the already-existing MisClick Profile detector *before*
the click and keeps that detector available for a short bounded window.  No new
blind dismissal is introduced: recovery still requires the scenario's positive
``FriendStatus.png`` evidence and uses its existing click action.  A failed
panel-dismiss click restores the previous gate state, and the legacy two-team
path is unchanged.
"""

from __future__ import annotations

import time

from . import rally_hot_path_runtime as _hot

BUILD_MARKER = "JOIN-HOT-RACE-v17 fixed-panel profile recovery"
PROFILE_RECOVERY_WINDOW_SECONDS = 3.0

_INSTALLED = False
_ORIGINAL_START = None
_ORIGINAL_EVALUATE_STEP = None
_ORIGINAL_RUN_ACTION = None
_ORIGINAL_DISMISS_FIXED_PANEL = None


def _clear_owned_profile_window(engine):
    engine._rally_v17_profile_recovery_until = 0.0
    engine._rally_v17_profile_recovery_owned = False


def _dismiss_fixed_rally_team_panel(engine, result, button="left"):
    """Arm Profile recovery around the existing outside-panel dismissal click."""

    if not _hot._is_three_team(engine):
        return _ORIGINAL_DISMISS_FIXED_PANEL(engine, result, button)

    previous_armed = bool(getattr(engine, "_rally_hot_profile_armed", False))
    engine._rally_hot_profile_armed = True
    engine._rally_v17_profile_recovery_owned = not previous_armed
    engine._rally_v17_profile_recovery_until = (
        time.monotonic() + PROFILE_RECOVERY_WINDOW_SECONDS
    )

    clicked = _ORIGINAL_DISMISS_FIXED_PANEL(engine, result, button)
    if not clicked:
        engine._rally_hot_profile_armed = previous_armed
        _clear_owned_profile_window(engine)
        return clicked

    engine.log(
        "  [rally-v17] fixed-panel outside dismissal armed Profile recovery"
    )
    return clicked


def install_rally_hot_path_v17_runtime():
    """Install bounded Profile recovery around fixed-panel dismissals."""

    global _INSTALLED
    global _ORIGINAL_START
    global _ORIGINAL_EVALUATE_STEP
    global _ORIGINAL_RUN_ACTION
    global _ORIGINAL_DISMISS_FIXED_PANEL
    if _INSTALLED:
        return

    from .engine import MacroEngine

    _ORIGINAL_START = MacroEngine.start
    _ORIGINAL_EVALUATE_STEP = MacroEngine._evaluate_step
    _ORIGINAL_RUN_ACTION = MacroEngine._run_action
    _ORIGINAL_DISMISS_FIXED_PANEL = MacroEngine._dismiss_fixed_rally_team_panel

    def start(self):
        self._rally_v17_profile_recovery_until = 0.0
        self._rally_v17_profile_recovery_owned = False
        result = _ORIGINAL_START(self)
        if _hot._is_three_team(self):
            self.log(f"[build] {BUILD_MARKER} loaded")
        return result

    def evaluate_step(self, step, frame_cache=None):
        if _hot._is_three_team(self) and getattr(step, "name", None) == "MisClick Profile":
            owned = bool(
                getattr(self, "_rally_v17_profile_recovery_owned", False)
            )
            deadline = float(
                getattr(self, "_rally_v17_profile_recovery_until", 0.0)
            )
            if owned and deadline > 0.0 and time.monotonic() > deadline:
                self._rally_hot_profile_armed = False
                _clear_owned_profile_window(self)
        return _ORIGINAL_EVALUATE_STEP(self, step, frame_cache=frame_cache)

    def run_action(self, step, action, points, matches):
        result = _ORIGINAL_RUN_ACTION(self, step, action, points, matches)
        if (
            _hot._is_three_team(self)
            and getattr(step, "name", None) == "MisClick Profile"
            and getattr(action, "type", None) == "click"
            and result
        ):
            _clear_owned_profile_window(self)
        return result

    MacroEngine.start = start
    MacroEngine._evaluate_step = evaluate_step
    MacroEngine._run_action = run_action
    MacroEngine._dismiss_fixed_rally_team_panel = _dismiss_fixed_rally_team_panel
    _INSTALLED = True
