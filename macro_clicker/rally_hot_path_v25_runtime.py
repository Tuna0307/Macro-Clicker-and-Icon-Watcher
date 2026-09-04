"""Verified all-busy tray dismissal for explicit three-team Rally.

A 2026-09-04 live v24 run proved that the legacy tray-only recovery trusted
``_click_point(...) == True`` as if that meant the bottom squad tray actually
closed.  The configured reference point (1218, 1045) is inside the tray region
(650..1280, 880..1080), so the click could succeed without dismissing anything.
The workflow was then cleared and Base recovery armed, leaving the macro stuck
on the world map with the tray still open.

v25 keeps the existing v10 transition grace/stable all-busy proof, but changes
only the final dismissal phase:

* click at a point outside the positively detected tray;
* keep the Rally workflow latched after that click;
* freshly verify that the tray anchor is absent on more than one poll;
* clear/release the workflow only after closure is confirmed;
* if the tray remains present, try an alternate outside-tray point and log the
  failure instead of claiming success.

The final Attack path is untouched and remains fail-closed.  Legacy two-team
behavior is untouched.
"""

from __future__ import annotations

import time

from . import rally_hot_path_runtime as _hot
from . import rally_hot_path_v7_runtime as _v7
from . import rally_hot_path_v9_runtime as _v9
from . import rally_hot_path_v10_runtime as _v10
from . import rally_hot_path_v17_runtime as _v17
from . import rally_matching as _rm
from .rally_team_policy import RALLY_TEAM_BUSY

BUILD_MARKER = "JOIN-HOT-RACE-v25 verified tray dismissal"

# These points are deliberately outside v7.TRAY_ANCHOR_REGION=(650,880,630,200).
# The old point (1218,1045) was inside that rectangle.
TRAY_DISMISS_REFERENCE_POINTS = (
    (600, 1045),   # immediately left of tray
    (1320, 1045),  # immediately right of tray
    (960, 820),    # above tray as a last alternate
)
TRAY_VERIFY_GRACE_SECONDS = 0.30
TRAY_ABSENCE_CONFIRM_SECONDS = 0.20
RISKY_MAP_RECOVERY_WINDOW_SECONDS = 3.0

_INSTALLED = False
_ORIGINAL_START = None
_ORIGINAL_RECOVER = None
_ORIGINAL_V10_DELEGATE = None


def _reset_dismiss_state(engine):
    engine._rally_v25_dismiss_pending = False
    engine._rally_v25_dismiss_attempts = 0
    engine._rally_v25_last_dismiss_click_at = 0.0
    engine._rally_v25_tray_absent_since = 0.0


def _scale_reference_point(window_rect, point):
    left, top, width, height = window_rect
    ref_width, ref_height = _rm.RALLY_FIXED_TEAM_REFERENCE_SIZE
    return (
        int(round(left + point[0] * width / ref_width)),
        int(round(top + point[1] * height / ref_height)),
    )


def _all_busy_tray(result):
    if not isinstance(result, dict):
        return False
    if not result.get("tray_valid") or result.get("formation_visible"):
        return False
    states = result.get("states", {})
    return all(states.get(team) == RALLY_TEAM_BUSY for team in (1, 2, 3))


def _arm_risky_map_recovery(engine, now):
    # Match v17's bounded Profile recovery semantics because any outside-tray
    # click can land on a player.  Base recovery is also armed for the same
    # bounded interval; both still require their own positive scenario template.
    previous_profile = bool(getattr(engine, "_rally_hot_profile_armed", False))
    engine._rally_hot_profile_armed = True
    engine._rally_v17_profile_recovery_owned = not previous_profile
    engine._rally_v17_profile_recovery_until = (
        now + _v17.PROFILE_RECOVERY_WINDOW_SECONDS
    )

    engine._rally_hot_base_armed = True
    engine._rally_hot_base_not_before = 0.0
    engine._rally_v9_base_arm_expires = now + RISKY_MAP_RECOVERY_WINDOW_SECONDS


def _attempt_verified_tray_dismiss(engine):
    """Send one outside-tray click but do not clear workflow state yet."""

    if not _hot._is_three_team(engine):
        return _ORIGINAL_V10_DELEGATE(engine)
    if not getattr(engine, "_rally_hot_entry_latched", False):
        return False
    if getattr(engine, "_pending_rally_level", None) is None:
        return False
    if getattr(engine, "_pending_rally_team_selected", None) is not None:
        return False

    result = _v7._capture_full_squad_tray(engine)
    if not _all_busy_tray(result):
        return False

    window_rect = result.get("window_rect")
    if not isinstance(window_rect, tuple) or len(window_rect) != 4:
        return False

    now = time.monotonic()
    attempt = int(getattr(engine, "_rally_v25_dismiss_attempts", 0))
    point_ref = TRAY_DISMISS_REFERENCE_POINTS[
        attempt % len(TRAY_DISMISS_REFERENCE_POINTS)
    ]
    point = _scale_reference_point(window_rect, point_ref)

    _arm_risky_map_recovery(engine, now)
    clicked = engine._click_point(point[0], point[1], "left")
    if clicked is False:
        engine.log(
            "  [rally-v25] all-busy tray outside-dismiss click failed "
            f"at {point} ref={point_ref}; workflow remains latched"
        )
        return False

    engine._rally_v25_dismiss_pending = True
    engine._rally_v25_dismiss_attempts = attempt + 1
    engine._rally_v25_last_dismiss_click_at = now
    engine._rally_v25_tray_absent_since = 0.0
    engine.log(
        "  [rally-v25] all-busy tray outside-dismiss attempt "
        f"{attempt + 1} clicked {point} ref={point_ref}; "
        "workflow remains latched pending fresh tray-closure proof"
    )

    # v10 interprets True as "recovered" and would log success/reset state.
    # Return False deliberately: the click itself is not proof of closure.
    return False


def _finalize_confirmed_tray_close(engine, now):
    _v7._clear_workflow_after_tray(engine)
    engine._rally_hot_entry_latched = False

    # Keep the bounded Base/Profile recovery windows armed because the outside
    # click may have selected a world-map entity while also closing the tray.
    engine._rally_hot_base_armed = True
    if float(getattr(engine, "_rally_v9_base_arm_expires", 0.0)) <= now:
        engine._rally_v9_base_arm_expires = now + RISKY_MAP_RECOVERY_WINDOW_SECONDS

    absence_since = float(getattr(engine, "_rally_v25_tray_absent_since", now))
    elapsed = max(0.0, now - absence_since)
    attempts = int(getattr(engine, "_rally_v25_dismiss_attempts", 0))
    engine.log(
        "  [rally-v25] tray closure confirmed across fresh captures "
        f"({elapsed:.2f}s, attempts={attempts}); workflow/latch released"
    )
    _reset_dismiss_state(engine)
    return True


def _recover_all_busy_tray(engine):
    """Wrap v10 recovery with positive post-click closure verification."""

    if not _hot._is_three_team(engine):
        return _ORIGINAL_RECOVER(engine)

    if not getattr(engine, "_rally_v25_dismiss_pending", False):
        return _ORIGINAL_RECOVER(engine)

    now = time.monotonic()
    result = _v7._capture_full_squad_tray(engine)

    if isinstance(result, dict) and result.get("formation_visible"):
        engine.log(
            "  [rally-v25] formation panel appeared while tray dismissal was "
            "pending; canceling tray recovery and leaving Attack polling in charge"
        )
        _reset_dismiss_state(engine)
        return False

    if _all_busy_tray(result):
        engine._rally_v25_tray_absent_since = 0.0
        elapsed = max(
            0.0,
            now - float(getattr(engine, "_rally_v25_last_dismiss_click_at", now)),
        )
        if elapsed < TRAY_VERIFY_GRACE_SECONDS:
            return False

        attempts = int(getattr(engine, "_rally_v25_dismiss_attempts", 0))
        engine.log(
            "  [rally-v25] tray still positively present "
            f"{elapsed:.2f}s after outside-dismiss attempt {attempts}; "
            "trying alternate outside point"
        )
        return _attempt_verified_tray_dismiss(engine)

    # A single negative tray capture is not enough: detector misses are possible.
    absent_since = float(getattr(engine, "_rally_v25_tray_absent_since", 0.0))
    if absent_since <= 0.0:
        engine._rally_v25_tray_absent_since = now
        world_map = _v9._condition_visible(engine, {"RallyIcon.png"})
        engine.log(
            "  [rally-v25] tray anchor absent on first fresh verification; "
            f"world-map RallyIcon={'YES' if world_map else 'NO'}; "
            "waiting for second absence proof"
        )
        return False

    if now - absent_since < TRAY_ABSENCE_CONFIRM_SECONDS:
        return False

    return _finalize_confirmed_tray_close(engine, now)


def install_rally_hot_path_v25_runtime():
    """Install verified tray dismissal after v24."""

    global _INSTALLED
    global _ORIGINAL_START
    global _ORIGINAL_RECOVER
    global _ORIGINAL_V10_DELEGATE
    if _INSTALLED:
        return

    from .engine import MacroEngine

    _ORIGINAL_START = MacroEngine.start
    _ORIGINAL_RECOVER = _v7._recover_all_busy_tray
    _ORIGINAL_V10_DELEGATE = _v10._ORIGINAL_V7_RECOVER

    def start(self):
        _reset_dismiss_state(self)
        result = _ORIGINAL_START(self)
        if _hot._is_three_team(self):
            self.log(f"[build] {BUILD_MARKER} loaded")
        return result

    MacroEngine.start = start

    # v10 owns grace/stability classification.  Replace only the final delegate
    # that used to trust a successful click as closure proof.
    _v10._ORIGINAL_V7_RECOVER = _attempt_verified_tray_dismiss

    # v7's installed cycle resolves this module-global recovery each cycle.
    # The wrapper handles post-click verification before delegating to v10.
    _v7._recover_all_busy_tray = _recover_all_busy_tray
    _INSTALLED = True
