"""Deadloop recovery and exact-team availability cache for three-team Rally.

This v9 overlay is intentionally narrow and installs after the existing v6/v7/v8
three-team hot-path layers.  It adds two live-safety/performance behaviors:

* after a successful world-map Rally-icon click, positively watch for Rally-page
  progress.  If the Rally page never appears, clear only the transient Rally
  workflow/latch and resume scanning without blind clicks; and
* remember exact Team 1/2/3 fixed-slot states after a validated formation-screen
  capture.  A successful dispatch marks the selected team BUSY immediately, so
  Rally-row OCR can reject levels that no remaining known-IDLE team can handle
  before clicking another row ``+``.

The left world-map squad counter is used only as a change signal.  It never
identifies Team 1/2/3.  A positively observed count change invalidates cached
identity because a team may have returned.  The one expected count increment
caused by our own confirmed dispatch is allowed without throwing away the cache.

Legacy two-team behavior is untouched.
"""

from __future__ import annotations

import os
import time

from . import rally_hot_path_runtime as _hot
from . import rally_matching as _rm
from .rally_team_policy import (
    RALLY_TEAM_BUSY,
    RALLY_TEAM_IDLE,
    available_rally_team_level_cap,
    effective_rally_team_priority,
)

BUILD_MARKER = "JOIN-HOT-RACE-v9 deadloop+team-cache"
EXPECT_RALLY_TIMEOUT_SECONDS = 2.5
RALLY_ICON_FAST_RECOVERY_GRACE_SECONDS = 0.45
BASE_RECOVERY_ARM_SECONDS = 1.0
SQUAD_COUNT_POLL_SECONDS = 0.15
EXPECTED_COUNT_SETTLE_SECONDS = 0.75

SQUAD_COUNT_REFERENCE_REGION = _hot.FULL_SQUAD_REFERENCE_REGION
SQUAD_COUNT_CONFIDENCE = 0.90
SQUAD_COUNT_TEMPLATES = {
    1: "templates/1_3Squad.png",
    2: "templates/2_3Squad.png",
    3: "templates/FullSquad3_3.png",
}
# ``0/3`` does not have a dedicated repository template.  The right-hand
# ``/3`` portion is stable across 0/3..3/3, so v9 derives a suffix template from
# 1/3 at runtime.  It only interprets "none of 1/2/3" as zero after that suffix
# is positively proven in the same tiny counter ROI.
SQUAD_COUNT_SUFFIX_START_RATIO = 0.34
SQUAD_COUNT_SUFFIX_CONFIDENCE = 0.90

_INSTALLED = False
_ORIGINAL_START = None
_ORIGINAL_RUN_ACTION = None
_ORIGINAL_CYCLE = None
_ORIGINAL_CAPTURE_FIXED_STATUS = None
_ORIGINAL_AVAILABLE_LEVEL_CAP = None


def _positive_condition(engine, basenames):
    wanted = {name.casefold() for name in basenames}
    scenario = getattr(engine, "scenario", None)
    if scenario is None:
        return None
    for step in getattr(scenario, "steps", ()):
        for index, condition in enumerate(getattr(step, "conditions", ())):
            basename = os.path.basename(
                getattr(condition, "template_path", "") or ""
            ).casefold()
            if basename in wanted and not getattr(condition, "negate", False):
                return index, condition
    return None


def _condition_visible(engine, basenames):
    """Freshly prove one known template condition without sending input."""

    found = _positive_condition(engine, basenames)
    if found is None:
        return False
    index, condition = found
    try:
        engine._window_rect_lookup_cache = {}
        ok, matches = engine._evaluate_condition(
            index,
            condition,
            {},
            collect_all=False,
        )
        return bool(ok and matches)
    except Exception:
        return False


def _clear_expect_rally(engine):
    engine._rally_v9_expect_rally_since = None


def _clear_stalled_workflow(engine, reason, *, world_map_proven):
    """Drop stale Rally state without clicking an unrecognized screen."""

    engine._pending_rally_level = None
    engine._pending_rally_team_selected = None
    engine._pending_rally_team_availability = None
    engine._rally_join_guard_until = 0.0
    engine._rally_hot_entry_latched = False
    engine._rally_hot_profile_armed = False
    engine._rally_v8_dispatch_wait_key = None
    _clear_expect_rally(engine)

    reset = getattr(engine, "_reset_three_team_rally_state", None)
    if callable(reset):
        reset(f"v9 deadloop recovery: {reason}")

    for step_name in (
        "Joining",
        "Attack Confirm",
        "Back if wrong mob",
        "Back if no slot",
    ):
        _hot._set_step_enabled(engine, step_name, False)
    # The entry step stays enabled in the scenario, so releasing the latch is
    # enough to resume normal world-map scanning on the next hot poll.

    if world_map_proven:
        engine._rally_hot_base_armed = False
        engine._rally_v9_base_arm_expires = 0.0
    else:
        # A failed Rally click can occasionally expose a recognized Base popup.
        # Keep the existing full-screen MisClick Base safety armed briefly so
        # that *its own positive template* may dismiss it.  v9 never blind-clicks.
        engine._rally_hot_base_armed = True
        engine._rally_hot_base_not_before = 0.0
        engine._rally_v9_base_arm_expires = (
            time.monotonic() + BASE_RECOVERY_ARM_SECONDS
        )

    engine.log(
        "  [rally-v9] stale Rally entry recovered "
        f"({reason}); workflow/latch cleared, no blind click sent"
    )


def _watch_entry_progress(engine, now=None):
    """Recover a Rally-icon click that never reaches the Rally page."""

    if not getattr(engine, "_rally_hot_entry_latched", False):
        _clear_expect_rally(engine)
        return False
    started = getattr(engine, "_rally_v9_expect_rally_since", None)
    if started is None:
        return False

    now = time.monotonic() if now is None else float(now)
    elapsed = max(0.0, now - float(started))

    # RallyPage is sufficient proof that the entry transition succeeded.  A
    # visible GoldMob also counts as forward progress if it wins the race first.
    if _condition_visible(engine, {"RallyPage.png", "GoldMob.png"}):
        _clear_expect_rally(engine)
        return False

    if (
        elapsed >= RALLY_ICON_FAST_RECOVERY_GRACE_SECONDS
        and _condition_visible(engine, {"RallyIcon.png"})
    ):
        _clear_stalled_workflow(
            engine,
            f"Rally icon visible again after {elapsed:.2f}s",
            world_map_proven=True,
        )
        return True

    if elapsed >= EXPECT_RALLY_TIMEOUT_SECONDS:
        _clear_stalled_workflow(
            engine,
            f"no Rally page progress for {elapsed:.2f}s",
            world_map_proven=False,
        )
        return True
    return False


def _selector_limits(engine):
    selector = _hot._three_team_selector(engine)
    if selector is None:
        return None
    priority = effective_rally_team_priority(selector.team_priority)
    limits = {
        team_number: getattr(selector, f"team{team_number}_max_level")
        for team_number in priority
    }
    return selector, priority, limits


def _invalidate_team_cache(engine, reason):
    had_cache = bool(getattr(engine, "_rally_v9_team_cache_valid", False))
    engine._rally_v9_team_cache_valid = False
    engine._rally_v9_team_states = None
    engine._rally_v9_team_cache_captured_at = 0.0
    engine._rally_v9_last_cached_cap_log = object()
    if had_cache:
        engine.log(f"  [team-cache] invalidated ({reason})")


def _cache_exact_fixed_states(engine, result):
    if not _hot._is_three_team(engine) or not isinstance(result, dict):
        return
    states = result.get("states", {})
    if not result.get("screen_valid") or any(
        states.get(team_number) not in {RALLY_TEAM_IDLE, RALLY_TEAM_BUSY}
        for team_number in (1, 2, 3)
    ):
        return

    new_states = {team_number: states[team_number] for team_number in (1, 2, 3)}
    previous = getattr(engine, "_rally_v9_team_states", None)
    engine._rally_v9_team_states = new_states
    engine._rally_v9_team_cache_valid = True
    engine._rally_v9_team_cache_captured_at = time.monotonic()
    engine._rally_v9_last_cached_cap_log = object()
    if previous != new_states:
        engine.log(
            "  [team-cache] exact fixed slots cached: "
            + " ".join(
                f"T{team}={new_states[team]}" for team in (1, 2, 3)
            )
        )


def _cached_level_cap(engine):
    if not getattr(engine, "_rally_v9_team_cache_valid", False):
        return _rm._TEAM_LEVEL_CAP_UNSET
    states = getattr(engine, "_rally_v9_team_states", None)
    resolved = _selector_limits(engine)
    if not isinstance(states, dict) or resolved is None:
        return _rm._TEAM_LEVEL_CAP_UNSET
    _selector, priority, limits = resolved
    if any(
        states.get(team_number) not in {RALLY_TEAM_IDLE, RALLY_TEAM_BUSY}
        for team_number in priority
    ):
        return _rm._TEAM_LEVEL_CAP_UNSET
    return available_rally_team_level_cap(states, limits, list(priority))


def _mark_dispatched_team_busy(engine, team_number, now=None):
    if not getattr(engine, "_rally_v9_team_cache_valid", False):
        return
    states = getattr(engine, "_rally_v9_team_states", None)
    if not isinstance(states, dict) or team_number not in (1, 2, 3):
        return

    states = dict(states)
    states[team_number] = RALLY_TEAM_BUSY
    engine._rally_v9_team_states = states
    engine._rally_v9_team_cache_captured_at = time.monotonic()
    engine._rally_v9_last_cached_cap_log = object()

    current_count = getattr(engine, "_rally_v9_last_squad_count", None)
    if isinstance(current_count, int) and 0 <= current_count < 3:
        engine._rally_v9_expected_squad_count = current_count + 1
        engine._rally_v9_expected_count_since = (
            time.monotonic() if now is None else float(now)
        )
    else:
        engine._rally_v9_expected_squad_count = None
        engine._rally_v9_expected_count_since = 0.0

    engine.log(
        f"  [team-cache] confirmed dispatch => T{team_number}=BUSY; "
        "remaining known-idle level ceiling recalculated"
    )


def _count_template_present(engine, frame, template, window_rect, confidence):
    matches = engine._find_template_matches_in_frame(
        frame,
        template,
        confidence,
        collect_all=False,
        allow_coarse=False,
        use_grayscale=True,
        reference_size=_hot.FULL_SQUAD_REFERENCE_SIZE,
        current_size=(window_rect[2], window_rect[3]),
        early_exit_score=confidence,
    )
    return bool(matches)


def _read_world_squad_count(engine):
    """Return a positively proven 0..3 world-map squad count, else ``None``."""

    window_rect = engine._get_target_window_rect()
    if not window_rect:
        return None
    try:
        region = _hot._scale_reference_region(window_rect, SQUAD_COUNT_REFERENCE_REGION)
        frame, _off_x, _off_y = engine._grab(region)

        loaded = {}
        # Prefer the more restrictive complete-count templates first.
        for count in (3, 2, 1):
            template = engine._load_template(SQUAD_COUNT_TEMPLATES[count])
            loaded[count] = template
            if _count_template_present(
                engine,
                frame,
                template,
                window_rect,
                SQUAD_COUNT_CONFIDENCE,
            ):
                return count

        one_template = loaded[1]
        width = int(one_template.shape[1])
        start = min(width - 1, max(1, round(width * SQUAD_COUNT_SUFFIX_START_RATIO)))
        suffix = one_template[:, start:]
        if suffix.size and _count_template_present(
            engine,
            frame,
            suffix,
            window_rect,
            SQUAD_COUNT_SUFFIX_CONFIDENCE,
        ):
            return 0
    except Exception:
        return None
    return None


def _observe_squad_count(engine, count, now=None):
    """Use count changes only to invalidate identity; never infer team identity."""

    if not isinstance(count, int) or count not in (0, 1, 2, 3):
        return False
    now = time.monotonic() if now is None else float(now)
    previous = getattr(engine, "_rally_v9_last_squad_count", None)
    expected = getattr(engine, "_rally_v9_expected_squad_count", None)

    if previous is None:
        engine._rally_v9_last_squad_count = count
        return False

    if count == previous:
        if expected is not None:
            since = float(getattr(engine, "_rally_v9_expected_count_since", 0.0))
            if now - since >= EXPECTED_COUNT_SETTLE_SECONDS:
                _invalidate_team_cache(
                    engine,
                    f"dispatch expected squad count {expected}, still observed {count}",
                )
                engine._rally_v9_expected_squad_count = None
                engine._rally_v9_expected_count_since = 0.0
                return True
        return False

    if expected is not None and count == expected and count > previous:
        engine._rally_v9_last_squad_count = count
        engine._rally_v9_expected_squad_count = None
        engine._rally_v9_expected_count_since = 0.0
        engine.log(
            f"  [team-cache] squad count {previous}/3 -> {count}/3 matches "
            "our confirmed dispatch; exact-team cache preserved"
        )
        return False

    engine._rally_v9_last_squad_count = count
    engine._rally_v9_expected_squad_count = None
    engine._rally_v9_expected_count_since = 0.0
    _invalidate_team_cache(
        engine,
        f"world-map squad count changed {previous}/3 -> {count}/3",
    )
    return True


def _poll_squad_count(engine, now=None):
    now = time.monotonic() if now is None else float(now)
    last_poll = float(getattr(engine, "_rally_v9_last_count_poll", 0.0))
    if now - last_poll < SQUAD_COUNT_POLL_SECONDS:
        return False
    engine._rally_v9_last_count_poll = now

    count = _read_world_squad_count(engine)
    if count is None:
        return False
    return _observe_squad_count(engine, count, now=now)


def _expire_temporary_base_arm(engine, now=None):
    expires = float(getattr(engine, "_rally_v9_base_arm_expires", 0.0))
    if expires <= 0.0:
        return
    now = time.monotonic() if now is None else float(now)
    if now < expires or getattr(engine, "_rally_hot_entry_latched", False):
        return
    engine._rally_v9_base_arm_expires = 0.0
    engine._rally_hot_base_armed = False


def install_rally_hot_path_v9_runtime():
    """Install deadloop recovery and exact-team cache once."""

    global _INSTALLED
    global _ORIGINAL_START
    global _ORIGINAL_RUN_ACTION
    global _ORIGINAL_CYCLE
    global _ORIGINAL_CAPTURE_FIXED_STATUS
    global _ORIGINAL_AVAILABLE_LEVEL_CAP
    if _INSTALLED:
        return

    from .engine import MacroEngine

    _ORIGINAL_START = MacroEngine.start
    _ORIGINAL_RUN_ACTION = MacroEngine._run_action
    _ORIGINAL_CYCLE = MacroEngine._cycle
    _ORIGINAL_CAPTURE_FIXED_STATUS = _rm.RallyMatchingMixin._capture_fixed_rally_team_status
    _ORIGINAL_AVAILABLE_LEVEL_CAP = _rm.RallyMatchingMixin._available_rally_team_level_cap

    def start(self):
        self._rally_v9_expect_rally_since = None
        self._rally_v9_base_arm_expires = 0.0
        self._rally_v9_team_cache_valid = False
        self._rally_v9_team_states = None
        self._rally_v9_team_cache_captured_at = 0.0
        self._rally_v9_last_cached_cap_log = object()
        self._rally_v9_last_squad_count = None
        self._rally_v9_expected_squad_count = None
        self._rally_v9_expected_count_since = 0.0
        self._rally_v9_last_count_poll = 0.0
        result = _ORIGINAL_START(self)
        if _hot._is_three_team(self):
            self.log(f"[build] {BUILD_MARKER} loaded")
        return result

    def run_action(self, step, action, points, matches):
        if not _hot._is_three_team(self):
            return _ORIGINAL_RUN_ACTION(self, step, action, points, matches)

        name = getattr(step, "name", None)
        action_type = getattr(action, "type", None)
        result = _ORIGINAL_RUN_ACTION(self, step, action, points, matches)

        if name == "Enter Rally after team probe" and action_type == "click":
            if result:
                self._rally_v9_expect_rally_since = time.monotonic()
            return result

        if name in {"Back if wrong mob", "Back if no slot", "MisClick Base"}:
            if action_type == "click" and result:
                _clear_expect_rally(self)
            return result

        if name == "Attack Confirm" and action_type == "select_rally_team":
            selected = getattr(self, "_pending_rally_team_selected", None)
            committed = (
                bool(result)
                and isinstance(selected, dict)
                and selected.get("team") in (1, 2, 3)
                and getattr(self, "_abort_current_step", False)
                and getattr(self, "_cleanup_after_abort", False)
                and not getattr(self, "_retry_current_step", False)
                and not getattr(self, "_rally_hot_entry_latched", False)
            )
            if committed:
                _mark_dispatched_team_busy(self, int(selected["team"]))
                _clear_expect_rally(self)
            return result

        return result

    def cycle(self):
        if _hot._is_three_team(self):
            now = time.monotonic()
            if _watch_entry_progress(self, now=now):
                return True
            _expire_temporary_base_arm(self, now=now)
            # Count polling is tiny and independent of team identity.  Only a
            # positively recognized 0/3..3/3 count can change cache validity.
            _poll_squad_count(self, now=now)
        return _ORIGINAL_CYCLE(self)

    def capture_fixed_rally_team_status(self):
        result = _ORIGINAL_CAPTURE_FIXED_STATUS(self)
        if _hot._is_three_team(self):
            _cache_exact_fixed_states(self, result)
        return result

    def available_rally_team_level_cap(self, action):
        if (
            _hot._is_three_team(self)
            and getattr(action, "type", None) == "click_matching_row"
        ):
            cached = _cached_level_cap(self)
            if cached is not _rm._TEAM_LEVEL_CAP_UNSET:
                previous = getattr(self, "_rally_v9_last_cached_cap_log", object())
                if cached != previous:
                    text = "none" if cached is None else str(cached)
                    self.log(
                        "  [team-cache] using known fixed-team availability; "
                        f"Rally-row ceiling={text}"
                    )
                    self._rally_v9_last_cached_cap_log = cached
                return cached
        return _ORIGINAL_AVAILABLE_LEVEL_CAP(self, action)

    MacroEngine.start = start
    MacroEngine._run_action = run_action
    MacroEngine._cycle = cycle
    _rm.RallyMatchingMixin._capture_fixed_rally_team_status = (
        capture_fixed_rally_team_status
    )
    _rm.RallyMatchingMixin._available_rally_team_level_cap = (
        available_rally_team_level_cap
    )
    _INSTALLED = True
