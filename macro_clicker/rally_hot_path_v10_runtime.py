"""Transition-stable all-busy tray recovery for explicit three-team Rally.

v7 introduced a useful recovery for the real tray-only state that appears when
all three squads are already out.  A 2026-09-03 live run exposed a transition
race: about 0.45 s after a valid Rally-row ``+`` click, the bottom tray had
rendered but the central formation panel and ZZ glyphs had not finished drawing.
v7 therefore saw ``AddSquad`` + no ``SquadAmount`` + no ZZ and incorrectly
classified that transient frame as BUSY/BUSY/BUSY.  It dismissed/cleared the
workflow just before the real formation screen finished opening, leaving the
user on a live formation screen with no Attack step enabled.

This v10 overlay does not add a blocking sleep and does not slow a normal Rally
join.  Normal Attack/formation detection keeps running immediately.  It only
prevents the *tray-only recovery* from making a destructive decision during the
formation transition, and requires BUSY/BUSY/BUSY to remain stable before the
existing v7 dismiss click is allowed.

Legacy two-team behavior is untouched.
"""

from __future__ import annotations

import time

from . import rally_hot_path_runtime as _hot
from . import rally_hot_path_v7_runtime as _v7
from .rally_team_policy import RALLY_TEAM_BUSY

BUILD_MARKER = "JOIN-HOT-RACE-v10 transition-stable tray recovery"

# This is a recovery-decision grace period, not a sleep.  During it the normal
# engine cycle continues, so Attack Confirm can fire as soon as the formation
# UI is positively recognized.
TRAY_TRANSITION_GRACE_SECONDS = 1.0
TRAY_BUSY_STABLE_SECONDS = 0.25

_INSTALLED = False
_ORIGINAL_START = None
_ORIGINAL_RUN_ACTION = None
_ORIGINAL_V7_RECOVER = None


def _reset_tray_confirmation(engine):
    engine._rally_v10_tray_busy_since = None


def _record_join_click(engine, now=None):
    now = time.monotonic() if now is None else float(now)
    engine._rally_v10_join_click_at = now
    _reset_tray_confirmation(engine)


def _candidate_is_all_busy(result):
    if not isinstance(result, dict):
        return False
    if not result.get("tray_valid") or result.get("formation_visible"):
        return False
    states = result.get("states", {})
    return all(states.get(team) == RALLY_TEAM_BUSY for team in (1, 2, 3))


def _guarded_recover_all_busy_tray(engine, now=None):
    """Allow v7 tray dismissal only after a stable post-Join BUSY tray."""

    if not _hot._is_three_team(engine):
        return _ORIGINAL_V7_RECOVER(engine)

    if (
        not getattr(engine, "_rally_hot_entry_latched", False)
        or getattr(engine, "_pending_rally_level", None) is None
        or getattr(engine, "_pending_rally_team_selected", None) is not None
    ):
        _reset_tray_confirmation(engine)
        return False

    now = time.monotonic() if now is None else float(now)
    joined_at = getattr(engine, "_rally_v10_join_click_at", None)
    if joined_at is not None:
        elapsed = max(0.0, now - float(joined_at))
        if elapsed < TRAY_TRANSITION_GRACE_SECONDS:
            # Do not call v7 yet.  The normal engine cycle still runs directly
            # after this returns False, so a real formation/Attack screen is
            # detected with no artificial wait.
            _reset_tray_confirmation(engine)
            return False

    result = _v7._capture_full_squad_tray(engine)
    if not _candidate_is_all_busy(result):
        _reset_tray_confirmation(engine)
        return False

    busy_since = getattr(engine, "_rally_v10_tray_busy_since", None)
    if busy_since is None:
        engine._rally_v10_tray_busy_since = now
        engine.log(
            "  [team3] all-busy tray candidate observed after formation grace; "
            "waiting for stable confirmation (normal Attack polling continues)"
        )
        return False

    if now - float(busy_since) < TRAY_BUSY_STABLE_SECONDS:
        return False

    # v7 performs its own fresh capture again immediately before the dismiss
    # click.  Therefore a transient candidate must pass: grace -> stable probe
    # -> final fresh v7 proof.  Any IDLE/UNKNOWN/formation evidence cancels it.
    recovered = bool(_ORIGINAL_V7_RECOVER(engine))
    if recovered:
        engine.log(
            "  [team3] stable all-busy tray confirmed after transition guard"
        )
    _reset_tray_confirmation(engine)
    return recovered


def install_rally_hot_path_v10_runtime():
    """Install transition-stable v7 tray recovery once."""

    global _INSTALLED
    global _ORIGINAL_START
    global _ORIGINAL_RUN_ACTION
    global _ORIGINAL_V7_RECOVER
    if _INSTALLED:
        return

    from .engine import MacroEngine

    _ORIGINAL_START = MacroEngine.start
    _ORIGINAL_RUN_ACTION = MacroEngine._run_action
    _ORIGINAL_V7_RECOVER = _v7._recover_all_busy_tray

    def start(self):
        self._rally_v10_join_click_at = None
        self._rally_v10_tray_busy_since = None
        result = _ORIGINAL_START(self)
        if _hot._is_three_team(self):
            self.log(f"[build] {BUILD_MARKER} loaded")
        return result

    def run_action(self, step, action, points, matches):
        result = _ORIGINAL_RUN_ACTION(self, step, action, points, matches)
        if not _hot._is_three_team(self):
            return result

        name = getattr(step, "name", None)
        action_type = getattr(action, "type", None)
        if (
            name == "Joining"
            and action_type == "click_matching_row"
            and result
            and getattr(self, "_pending_rally_level", None) is not None
        ):
            _record_join_click(self)
            return result

        # Any completed workflow exit invalidates the old transition timestamp.
        if not getattr(self, "_rally_hot_entry_latched", False):
            self._rally_v10_join_click_at = None
            _reset_tray_confirmation(self)
        return result

    MacroEngine.start = start
    MacroEngine._run_action = run_action
    # v7's installed cycle resolves this module-global function at runtime, so
    # replacing it here safely tightens only the tray-only recovery decision.
    _v7._recover_all_busy_tray = _guarded_recover_all_busy_tray
    _INSTALLED = True
