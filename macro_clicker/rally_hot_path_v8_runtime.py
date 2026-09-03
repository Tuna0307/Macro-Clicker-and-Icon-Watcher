"""Mob2-paced final-dispatch overlay for explicit three-team Rally mode.

The v6/v7 hot path remains responsible for fast opportunity scanning, Rally-entry
latching, 3/3 suppression, stale Join cancellation, and all-busy tray recovery.
This overlay changes only two UI-transition boundaries observed in the 2026-09-03
live recordings:

* the world-map Rally click gets the same short 0.3 s settle used by the mature
  two-team flow instead of skipping the configured wait entirely; and
* after a three-team selector clicks a fixed Team card, the configured random
  dispatch wait is honored once and ``Attack.png`` is freshly revalidated by
  itself before the final click.  The whole Attack Confirm condition set is not
  re-required after the Team-card click because that click legitimately changes
  formation UI pixels and was causing the generic action-loop revalidation to
  cancel action #3.

No fixed Attack coordinate is used.  If fresh Attack cannot be proven, no Attack
click is sent.  The legacy two-team path is untouched.
"""

from __future__ import annotations

import time

from . import rally_hot_path_runtime as _hot

ENTRY_SETTLE_SECONDS = 0.3
FRESH_ATTACK_RETRY_WINDOW_SECONDS = 0.35
FRESH_ATTACK_RETRY_INTERVAL_SECONDS = 0.05
BUILD_MARKER = "JOIN-HOT-RACE-v8 mob2-paced dispatch"

_INSTALLED = False
_ORIGINAL_START = None
_ORIGINAL_RUN_ACTION = None


def _attack_followups(step, selector_action):
    """Return the configured post-selector wait and final Attack click actions."""

    seen_selector = False
    wait_action = None
    click_action = None
    for candidate in getattr(step, "actions", ()):
        if candidate is selector_action:
            seen_selector = True
            continue
        if not seen_selector:
            continue
        if wait_action is None and getattr(candidate, "type", None) == "wait":
            wait_action = candidate
            continue
        if (
            getattr(candidate, "type", None) == "click"
            and getattr(candidate, "on_condition_index", None) is not None
        ):
            click_action = candidate
            break
    return wait_action, click_action


def _fresh_attack_context(engine, step, click_action):
    """Freshly prove only the Attack target after Team selection.

    Team selection can legitimately change the second Attack Confirm condition
    (formation/SquadAmount pixels).  Requiring the whole original step again is
    therefore too strict.  The final input remains fail-closed by freshly
    matching the exact condition used by the configured Attack click.
    """

    condition_index = getattr(click_action, "on_condition_index", None)
    conditions = getattr(step, "conditions", ())
    if (
        not isinstance(condition_index, int)
        or condition_index < 0
        or condition_index >= len(conditions)
    ):
        return None

    condition = conditions[condition_index]
    deadline = time.monotonic() + FRESH_ATTACK_RETRY_WINDOW_SECONDS
    while True:
        if engine._stop_requested():
            return None
        engine._window_rect_lookup_cache = {}
        ok, fresh_matches = engine._evaluate_condition(
            condition_index,
            condition,
            {},
            collect_all=False,
        )
        if ok and fresh_matches:
            return {
                "condition_index": condition_index,
                "points": {condition_index: fresh_matches[0]["center"]},
                "matches": {condition_index: fresh_matches},
            }

        remaining = deadline - time.monotonic()
        if remaining <= 0.0:
            return None
        if engine._sleep_until_stop(
            min(FRESH_ATTACK_RETRY_INTERVAL_SECONDS, remaining)
        ):
            return None


def _selection_key(engine):
    selected = getattr(engine, "_pending_rally_team_selected", None)
    if not isinstance(selected, dict):
        return None
    level = selected.get("level")
    team = selected.get("team")
    if level is None or team is None:
        return None
    return level, team


def _clear_dispatch_wait(engine):
    engine._rally_v8_dispatch_wait_key = None


def _complete_three_team_dispatch(engine, step, selector_action, points, matches):
    """Honor the configured random wait, then fresh-check and click Attack."""

    selection_key = _selection_key(engine)
    if selection_key is None:
        return False

    wait_action, click_action = _attack_followups(step, selector_action)
    if click_action is None:
        engine.log("  [team3] final Attack action is not configured; no dispatch")
        engine._retry_current_step = True
        return False

    if getattr(engine, "_rally_v8_dispatch_wait_key", None) != selection_key:
        if wait_action is not None:
            # Call the pre-hot-path engine action directly.  This preserves the
            # scenario's configured random 1.0-1.5 s wait without letting the
            # v6 entry-wait suppression intercept it.
            _hot._ORIGINAL_RUN_ACTION(engine, step, wait_action, points, matches)
            if engine._stop_requested():
                return False
        engine._rally_v8_dispatch_wait_key = selection_key

    fresh = _fresh_attack_context(engine, step, click_action)
    if fresh is None:
        engine.log(
            "  [team3] fresh Attack target not proven after Team selection; "
            "no Attack click sent"
        )
        engine._retry_current_step = True
        return False

    condition_index = fresh["condition_index"]
    fresh_match = fresh["matches"][condition_index][0]
    score = fresh_match.get("confidence")
    score_text = "n/a" if score is None else f"{float(score):.3f}"
    engine.log(
        "  [team3] fresh Attack revalidated after Team selection "
        f"score={score_text}"
    )

    clicked = _hot._ORIGINAL_RUN_ACTION(
        engine,
        step,
        click_action,
        fresh["points"],
        fresh["matches"],
    )
    if not clicked:
        engine.log("  [team3] fresh Attack click was blocked; dispatch not committed")
        engine._retry_current_step = True
        return False

    engine.log("  [team3] dispatch committed through fresh Attack target")
    engine._rally_hot_entry_latched = False
    engine._rally_hot_profile_armed = False
    engine._rally_hot_base_armed = False
    engine._rally_join_guard_until = 0.0
    _clear_dispatch_wait(engine)

    # The configured actions after selector/wait/Attack are only state cleanup.
    # Mark this step complete here so the generic action loop does not re-run the
    # stale full-condition check before the old action #3.  Its existing abort
    # cleanup path will still execute the later set_step actions.
    engine._abort_current_step = True
    engine._cleanup_after_abort = True
    engine._retry_current_step = False
    return True


def install_rally_hot_path_v8_runtime():
    """Install mob2-paced transition and fresh final-attack handling once."""

    global _INSTALLED
    global _ORIGINAL_START
    global _ORIGINAL_RUN_ACTION
    if _INSTALLED:
        return

    from .engine import MacroEngine

    _ORIGINAL_START = MacroEngine.start
    _ORIGINAL_RUN_ACTION = MacroEngine._run_action

    def start(self):
        self._rally_v8_dispatch_wait_key = None
        result = _ORIGINAL_START(self)
        if _hot._is_three_team(self):
            self.log(f"[build] {BUILD_MARKER} loaded")
        return result

    def run_action(self, step, action, points, matches):
        if not _hot._is_three_team(self):
            return _ORIGINAL_RUN_ACTION(self, step, action, points, matches)

        name = getattr(step, "name", None)
        action_type = getattr(action, "type", None)

        # v6 deliberately removed this wait.  The user's working two-team
        # recording shows the 0.3 s settle is the desired pacing, so restore
        # exactly that small transition delay while keeping 30-50 ms scanning
        # everywhere else.
        if name == "Enter Rally after team probe" and action_type == "wait":
            if self._sleep_until_stop(ENTRY_SETTLE_SECONDS):
                return False
            self.log(
                f"  wait {ENTRY_SETTLE_SECONDS:g}s "
                "(mob2-paced Rally entry settle)"
            )
            return True

        result = _ORIGINAL_RUN_ACTION(self, step, action, points, matches)

        if name == "Attack Confirm" and action_type == "select_rally_team":
            if getattr(self, "_abort_current_step", False):
                _clear_dispatch_wait(self)
                return result
            if _selection_key(self) is None:
                return result
            # Whether this selector was just clicked or resumed from a previous
            # poll, finish the dispatch through a fresh Attack-only check.
            return _complete_three_team_dispatch(
                self,
                step,
                action,
                points,
                matches,
            )

        if name in {"Back if wrong mob", "Back if no slot", "MisClick Base"}:
            if action_type == "click" and result:
                _clear_dispatch_wait(self)
        return result

    MacroEngine.start = start
    MacroEngine._run_action = run_action
    _INSTALLED = True
