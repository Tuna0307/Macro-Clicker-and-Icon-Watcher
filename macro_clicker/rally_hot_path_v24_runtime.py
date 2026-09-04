"""Deep diagnostic tracing for the explicit three-team Rally runtime.

This overlay is intentionally diagnostic-only.  It changes no Rally eligibility,
Team selection, cooldown, row matching, world-count reconciliation, or final
Attack behavior.  The live three-team workflow has accumulated enough stateful
safety layers that sparse event logs can make a correct-but-clunky path look like
a different bug.  v24 therefore records the state machine around the places
where evidence changes or input is sent.

The extra logging is temporary by design.  Once long-run behavior is stable, the
trace can be reduced without touching the underlying safety logic.
"""

from __future__ import annotations

import time

from . import rally_hot_path_runtime as _hot
from . import rally_hot_path_v9_runtime as _v9
from . import rally_hot_path_v12_runtime as _v12
from . import rally_hot_path_v22_runtime as _v22
from . import rally_matching as _rm
from .rally_team_policy import RALLY_TEAM_BUSY, RALLY_TEAM_IDLE

BUILD_MARKER = "JOIN-HOT-RACE-v24 deep diagnostic state trace"
STATE_HEARTBEAT_SECONDS = 2.0
ENTRY_TRACE_INTERVAL_SECONDS = 1.0

_TRACE_STEPS = {
    "Enter Rally after team probe",
    "Joining",
    "Attack Confirm",
    "Back if wrong mob",
    "Back if no slot",
    "MisClick Base",
    "MisClick Profile",
}

_INSTALLED = False
_ORIGINAL_START = None
_ORIGINAL_CYCLE = None
_ORIGINAL_EVALUATE_STEP = None
_ORIGINAL_RUN_ACTION = None
_ORIGINAL_NO_MATCH_FALLBACK = None
_ORIGINAL_CAPTURE_FIXED_STATUS = None
_ORIGINAL_AVAILABLE_LEVEL_CAP = None
_ORIGINAL_OBSERVE_SQUAD_COUNT = None
_ORIGINAL_MARK_DISPATCHED_TEAM_BUSY = None


def _safe_len(value):
    try:
        return len(value)
    except Exception:
        return 0


def _fmt_optional(value, suffix=""):
    if value is None:
        return "none"
    return f"{value}{suffix}"


def _fmt_age(started, now):
    try:
        started = float(started)
    except (TypeError, ValueError):
        return "none"
    if started <= 0.0:
        return "none"
    return f"{max(0.0, now - started):.2f}s"


def _fmt_remaining(deadline, now):
    try:
        deadline = float(deadline)
    except (TypeError, ValueError):
        return "0.00s"
    return f"{max(0.0, deadline - now):.2f}s"


def _exact_states(engine):
    if not getattr(engine, "_rally_v9_team_cache_valid", False):
        return None
    states = getattr(engine, "_rally_v9_team_states", None)
    if not isinstance(states, dict):
        return None
    if any(
        states.get(team) not in {RALLY_TEAM_IDLE, RALLY_TEAM_BUSY}
        for team in (1, 2, 3)
    ):
        return None
    return states


def _busy_count(states):
    if states is None:
        return None
    return sum(states.get(team) == RALLY_TEAM_BUSY for team in (1, 2, 3))


def _selected_team(engine):
    selected = getattr(engine, "_pending_rally_team_selected", None)
    if isinstance(selected, dict):
        return selected.get("team")
    return selected


def _state_key(engine):
    states = _exact_states(engine)
    state_tuple = (
        None
        if states is None
        else tuple(states.get(team) for team in (1, 2, 3))
    )
    return (
        bool(getattr(engine, "_rally_hot_entry_latched", False)),
        getattr(engine, "_pending_rally_level", None),
        _selected_team(engine),
        bool(getattr(engine, "_rally_v9_team_cache_valid", False)),
        state_tuple,
        getattr(engine, "_rally_v9_last_squad_count", None),
        getattr(engine, "_rally_v9_expected_squad_count", None),
        getattr(engine, "_rally_v12_pending_squad_count", None),
        bool(getattr(engine, "_rally_hot_base_armed", False)),
        bool(getattr(engine, "_rally_hot_profile_armed", False)),
        bool(getattr(engine, "_abort_current_step", False)),
        bool(getattr(engine, "_retry_current_step", False)),
        bool(getattr(engine, "_cleanup_after_abort", False)),
        float(getattr(engine, "_rally_join_guard_until", 0.0)),
        float(getattr(engine, "_rally_v19_probe_until", 0.0)),
        float(getattr(engine, "_rally_v22_full_squad_hold_until", 0.0)),
        float(getattr(engine, "_rally_v23_entry_not_before", 0.0)),
    )


def _format_state(engine, now=None):
    now = time.monotonic() if now is None else float(now)
    states = _exact_states(engine)
    cache_valid = bool(getattr(engine, "_rally_v9_team_cache_valid", False))
    if states is None:
        teams_text = "T1=? T2=? T3=? busy=?"
    else:
        teams_text = (
            f"T1={states[1]} T2={states[2]} T3={states[3]} "
            f"busy={_busy_count(states)}"
        )

    expected = getattr(engine, "_rally_v9_expected_squad_count", None)
    candidate = getattr(engine, "_rally_v12_pending_squad_count", None)
    expect_rally_since = getattr(engine, "_rally_v9_expect_rally_since", None)

    return (
        f"latch={int(bool(getattr(engine, '_rally_hot_entry_latched', False)))} "
        f"pending_level={_fmt_optional(getattr(engine, '_pending_rally_level', None))} "
        f"pending_team={_fmt_optional(_selected_team(engine))} | "
        f"cache={'VALID' if cache_valid else 'INVALID'} "
        f"cache_age={_fmt_age(getattr(engine, '_rally_v9_team_cache_captured_at', 0.0), now)} "
        f"{teams_text} | "
        f"sidebar={_fmt_optional(getattr(engine, '_rally_v9_last_squad_count', None), '/3')} "
        f"expected={_fmt_optional(expected, '/3')} "
        f"expected_age={_fmt_age(getattr(engine, '_rally_v9_expected_count_since', 0.0), now) if expected is not None else 'none'} "
        f"candidate={_fmt_optional(candidate, '/3')} "
        f"candidate_age={_fmt_age(getattr(engine, '_rally_v12_pending_squad_since', 0.0), now) if candidate is not None else 'none'} | "
        f"expect_rally_age={_fmt_age(expect_rally_since, now)} "
        f"join_guard_rem={_fmt_remaining(getattr(engine, '_rally_join_guard_until', 0.0), now)} "
        f"probe_rem={_fmt_remaining(getattr(engine, '_rally_v19_probe_until', 0.0), now)} "
        f"full_hold_rem={_fmt_remaining(getattr(engine, '_rally_v22_full_squad_hold_until', 0.0), now)} "
        f"reentry_rem={_fmt_remaining(getattr(engine, '_rally_v23_entry_not_before', 0.0), now)} | "
        f"base_arm={int(bool(getattr(engine, '_rally_hot_base_armed', False)))} "
        f"profile_arm={int(bool(getattr(engine, '_rally_hot_profile_armed', False)))} "
        f"abort={int(bool(getattr(engine, '_abort_current_step', False)))} "
        f"retry={int(bool(getattr(engine, '_retry_current_step', False)))} "
        f"cleanup={int(bool(getattr(engine, '_cleanup_after_abort', False)))}"
    )


def _trace_state(engine, event, *, force=False, now=None):
    if not _hot._is_three_team(engine):
        return False
    now = time.monotonic() if now is None else float(now)
    key = _state_key(engine)
    last_key = getattr(engine, "_rally_v24_last_state_key", None)
    last_heartbeat = float(getattr(engine, "_rally_v24_last_heartbeat", 0.0))
    changed = key != last_key
    heartbeat_due = now - last_heartbeat >= STATE_HEARTBEAT_SECONDS
    if not force and not changed and not heartbeat_due:
        return False

    engine._rally_v24_last_state_key = key
    engine._rally_v24_last_heartbeat = now
    marker = "change" if changed else "heartbeat"
    engine.log(
        f"  [rally-v24][state:{marker}:{event}] " + _format_state(engine, now=now)
    )
    return True


def _trace_entry_evaluation(engine, ready, now=None):
    now = time.monotonic() if now is None else float(now)
    reason = _v22._hard_full_squad_reason(engine, now=now)
    cooldown = max(
        0.0,
        float(getattr(engine, "_rally_v23_entry_not_before", 0.0)) - now,
    )
    key = (
        bool(ready),
        reason,
        round(cooldown, 1),
        bool(getattr(engine, "_rally_hot_entry_latched", False)),
        getattr(engine, "_rally_v9_last_squad_count", None),
        _busy_count(_exact_states(engine)),
    )
    last_key = getattr(engine, "_rally_v24_last_entry_key", None)
    last_log = float(getattr(engine, "_rally_v24_last_entry_log", 0.0))
    if key == last_key and now - last_log < ENTRY_TRACE_INTERVAL_SECONDS:
        return
    engine._rally_v24_last_entry_key = key
    engine._rally_v24_last_entry_log = now
    engine.log(
        "  [rally-v24][entry] "
        f"ready={int(bool(ready))} "
        f"hard_reason={reason or 'none'} "
        f"cooldown_rem={cooldown:.2f}s | "
        + _format_state(engine, now=now)
    )


def _score_text(value):
    if value is None:
        return "none"
    try:
        return f"{float(value):.3f}"
    except (TypeError, ValueError):
        return str(value)


def _formation_text(result):
    if not isinstance(result, dict):
        return f"raw_type={type(result).__name__}"
    states = result.get("states") or {}
    scores = result.get("idle_scores") or {}
    return (
        f"screen_valid={int(bool(result.get('screen_valid')))} "
        f"error={result.get('error') or 'none'} "
        + " ".join(
            f"T{team}={states.get(team, '?')} idle_score={_score_text(scores.get(team))}"
            for team in (1, 2, 3)
        )
    )


def _observe_squad_count(engine, count, now=None):
    if not _hot._is_three_team(engine):
        return _ORIGINAL_OBSERVE_SQUAD_COUNT(engine, count, now=now)
    now = time.monotonic() if now is None else float(now)
    before_sidebar = getattr(engine, "_rally_v9_last_squad_count", None)
    before_expected = getattr(engine, "_rally_v9_expected_squad_count", None)
    before_candidate = getattr(engine, "_rally_v12_pending_squad_count", None)
    before_cache = bool(getattr(engine, "_rally_v9_team_cache_valid", False))
    before_busy = _busy_count(_exact_states(engine))

    result = _ORIGINAL_OBSERVE_SQUAD_COUNT(engine, count, now=now)

    engine.log(
        "  [rally-v24][count] "
        f"observed={count}/3 before(sidebar={_fmt_optional(before_sidebar, '/3')},"
        f"expected={_fmt_optional(before_expected, '/3')},"
        f"candidate={_fmt_optional(before_candidate, '/3')},"
        f"cache={int(before_cache)},busy={_fmt_optional(before_busy)}) -> "
        f"after(sidebar={_fmt_optional(getattr(engine, '_rally_v9_last_squad_count', None), '/3')},"
        f"expected={_fmt_optional(getattr(engine, '_rally_v9_expected_squad_count', None), '/3')},"
        f"candidate={_fmt_optional(getattr(engine, '_rally_v12_pending_squad_count', None), '/3')},"
        f"cache={int(bool(getattr(engine, '_rally_v9_team_cache_valid', False)))},"
        f"busy={_fmt_optional(_busy_count(_exact_states(engine)))}) "
        f"changed={int(bool(result))}"
    )
    _trace_state(engine, "count", force=True, now=now)
    return result


def _mark_dispatched_team_busy(engine, team_number, now=None):
    if not _hot._is_three_team(engine):
        return _ORIGINAL_MARK_DISPATCHED_TEAM_BUSY(engine, team_number, now=now)
    now = time.monotonic() if now is None else float(now)
    engine.log(
        "  [rally-v24][dispatch-cache:before] "
        f"team=T{team_number} | " + _format_state(engine, now=now)
    )
    result = _ORIGINAL_MARK_DISPATCHED_TEAM_BUSY(engine, team_number, now=now)
    engine.log(
        "  [rally-v24][dispatch-cache:after] "
        f"team=T{team_number} | " + _format_state(engine, now=now)
    )
    _trace_state(engine, "dispatch-cache", force=True, now=now)
    return result


def install_rally_hot_path_v24_runtime():
    """Install diagnostic-only tracing after v23."""

    global _INSTALLED
    global _ORIGINAL_START
    global _ORIGINAL_CYCLE
    global _ORIGINAL_EVALUATE_STEP
    global _ORIGINAL_RUN_ACTION
    global _ORIGINAL_NO_MATCH_FALLBACK
    global _ORIGINAL_CAPTURE_FIXED_STATUS
    global _ORIGINAL_AVAILABLE_LEVEL_CAP
    global _ORIGINAL_OBSERVE_SQUAD_COUNT
    global _ORIGINAL_MARK_DISPATCHED_TEAM_BUSY
    if _INSTALLED:
        return

    from .engine import MacroEngine

    _ORIGINAL_START = MacroEngine.start
    _ORIGINAL_CYCLE = MacroEngine._cycle
    _ORIGINAL_EVALUATE_STEP = MacroEngine._evaluate_step
    _ORIGINAL_RUN_ACTION = MacroEngine._run_action
    _ORIGINAL_NO_MATCH_FALLBACK = MacroEngine._run_no_match_fallback
    _ORIGINAL_CAPTURE_FIXED_STATUS = _rm.RallyMatchingMixin._capture_fixed_rally_team_status
    _ORIGINAL_AVAILABLE_LEVEL_CAP = _rm.RallyMatchingMixin._available_rally_team_level_cap
    _ORIGINAL_OBSERVE_SQUAD_COUNT = _v12._observe_squad_count
    _ORIGINAL_MARK_DISPATCHED_TEAM_BUSY = _v9._mark_dispatched_team_busy

    def start(self):
        self._rally_v24_last_state_key = None
        self._rally_v24_last_heartbeat = 0.0
        self._rally_v24_last_entry_key = None
        self._rally_v24_last_entry_log = 0.0
        result = _ORIGINAL_START(self)
        if _hot._is_three_team(self):
            self.log(f"[build] {BUILD_MARKER} loaded")
            _trace_state(self, "start", force=True)
        return result

    def cycle(self):
        if _hot._is_three_team(self):
            _trace_state(self, "cycle-before")
        result = _ORIGINAL_CYCLE(self)
        if _hot._is_three_team(self):
            _trace_state(self, "cycle-after")
        return result

    def evaluate_step(self, step, frame_cache=None):
        result = _ORIGINAL_EVALUATE_STEP(self, step, frame_cache=frame_cache)
        if (
            _hot._is_three_team(self)
            and getattr(step, "name", None) == "Enter Rally after team probe"
        ):
            _trace_entry_evaluation(self, bool(result[0]))
        return result

    def run_action(self, step, action, points, matches):
        name = getattr(step, "name", None)
        action_type = getattr(action, "type", None)
        traced = _hot._is_three_team(self) and name in _TRACE_STEPS
        if traced:
            self.log(
                "  [rally-v24][action:before] "
                f"step={name!r} type={action_type!r} "
                f"points={_safe_len(points)} matches={_safe_len(matches)} | "
                + _format_state(self)
            )
        result = _ORIGINAL_RUN_ACTION(self, step, action, points, matches)
        if traced:
            self.log(
                "  [rally-v24][action:after] "
                f"step={name!r} type={action_type!r} result={bool(result)} | "
                + _format_state(self)
            )
            _trace_state(self, f"action:{name}", force=True)
        return result

    def run_no_match_fallback(self, step, action, points):
        traced = _hot._is_three_team(self) and getattr(step, "name", None) == "Joining"
        if traced:
            self.log(
                "  [rally-v24][no-match:before] "
                f"action={getattr(action, 'type', None)!r} points={_safe_len(points)} | "
                + _format_state(self)
            )
        result = _ORIGINAL_NO_MATCH_FALLBACK(self, step, action, points)
        if traced:
            self.log(
                "  [rally-v24][no-match:after] "
                f"result={bool(result)} | " + _format_state(self)
            )
            _trace_state(self, "no-match", force=True)
        return result

    def capture_fixed_rally_team_status(self):
        result = _ORIGINAL_CAPTURE_FIXED_STATUS(self)
        if _hot._is_three_team(self):
            self.log("  [rally-v24][formation] " + _formation_text(result))
            _trace_state(self, "formation", force=True)
        return result

    def available_rally_team_level_cap(self, action):
        result = _ORIGINAL_AVAILABLE_LEVEL_CAP(self, action)
        if (
            _hot._is_three_team(self)
            and getattr(action, "type", None) == "click_matching_row"
        ):
            cap_text = "none" if result is None else str(result)
            self.log(
                "  [rally-v24][row-cap] "
                f"result={cap_text} | " + _format_state(self)
            )
        return result

    MacroEngine.start = start
    MacroEngine._cycle = cycle
    MacroEngine._evaluate_step = evaluate_step
    MacroEngine._run_action = run_action
    MacroEngine._run_no_match_fallback = run_no_match_fallback
    _rm.RallyMatchingMixin._capture_fixed_rally_team_status = capture_fixed_rally_team_status
    _rm.RallyMatchingMixin._available_rally_team_level_cap = available_rally_team_level_cap

    # v12's polling loop resolves this module-global observer at runtime.
    _v12._observe_squad_count = _observe_squad_count
    _v9._observe_squad_count = _observe_squad_count

    # v9's dispatch wrapper resolves this helper at runtime.
    _v9._mark_dispatched_team_busy = _mark_dispatched_team_busy
    _INSTALLED = True
