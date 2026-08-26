from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from macro_clicker.bot.config import BotConfig, BotConfigError
from macro_clicker.bot.ui_runtime import BotRuntimeMixin


class _Var:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value


class _RuntimeHarness(BotRuntimeMixin):
    pass


def _harness():
    config = BotConfig()
    runtime = _RuntimeHarness()
    runtime.config = config
    runtime.target_window_var = _Var(config.target_window_title)
    runtime.rally_enabled_var = _Var(config.rally.enabled)
    runtime.rally_min_var = _Var(config.rally.min_level)
    runtime.rally_max_var = _Var(config.rally.max_level)
    runtime.team1_max_var = _Var(config.rally.team1_max_level)
    runtime.team3_max_var = _Var(config.rally.team3_max_level)
    runtime.join_delay_var = _Var(config.rally.join_delay)
    runtime.gather_enabled_var = _Var(config.gather.enabled)
    runtime.resource_var = _Var(config.gather.resource)
    runtime.gather_start_level_var = _Var(config.gather.start_level)
    runtime.gather_team_vars = {
        team: _Var(team in config.gather.teams_enabled) for team in (1, 2, 3)
    }
    runtime.development_enabled_var = _Var(config.positions.development_enabled)
    runtime.science_enabled_var = _Var(config.positions.science_enabled)
    runtime.positions_retry_var = _Var(config.positions.retry_automatically)
    runtime.alerts_enabled_var = _Var(config.alerts.enabled)
    runtime.digs_enabled_var = _Var(config.alerts.digs_enabled)
    runtime.secret_task_enabled_var = _Var(config.alerts.secret_task_enabled)
    runtime.alert_sound_var = _Var(config.alerts.sound_enabled)
    runtime.alert_volume_var = _Var(config.alerts.volume_percent)
    runtime.schedule_enabled_var = _Var(config.schedule.enabled)
    runtime.schedule_start_var = _Var(config.schedule.start_time)
    runtime.schedule_stop_var = _Var(config.schedule.stop_time)
    runtime.day_vars = {
        day: _Var(day in config.schedule.days)
        for day in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
    }
    return runtime


def _successful_controller():
    return SimpleNamespace(
        status=SimpleNamespace(last_message="Running Rally"),
        enabled_features=lambda: ["rally"],
        start=lambda: True,
    )


def test_collect_config_returns_pending_copy_without_mutating_live_config():
    runtime = _harness()
    original = deepcopy(runtime.config)
    runtime.rally_min_var = _Var(12)

    pending = runtime._collect_config()

    assert pending is not runtime.config
    assert pending.rally.min_level == 12
    assert runtime.config == original


def test_collect_config_reads_position_retry_choice():
    runtime = _harness()
    runtime.positions_retry_var = _Var(False)

    pending = runtime._collect_config()

    assert pending.positions.retry_automatically is False
    assert runtime.config.positions.retry_automatically is True


def test_collect_config_reads_enabled_gather_teams():
    runtime = _harness()
    runtime.gather_team_vars = {1: _Var(True), 2: _Var(False), 3: _Var(True)}

    pending = runtime._collect_config()

    assert pending.gather.teams_enabled == [1, 3]
    assert runtime.config.gather.teams_enabled == [1, 2, 3]


def test_collect_config_requires_at_least_one_gather_team():
    runtime = _harness()
    runtime.gather_team_vars = {1: _Var(False), 2: _Var(False), 3: _Var(False)}

    with pytest.raises(BotConfigError, match="at least one gathering team"):
        runtime._collect_config()


def test_invalid_ui_value_does_not_partially_mutate_live_config():
    runtime = _harness()
    original = deepcopy(runtime.config)
    runtime.rally_min_var = _Var(80)
    runtime.rally_max_var = _Var(70)

    with pytest.raises(BotConfigError):
        runtime._collect_config()

    assert runtime.config == original


def test_scheduled_start_can_use_saved_config_without_resaving_ui():
    runtime = _harness()
    save_calls = []
    logs = []
    runtime._save_from_ui = lambda: save_calls.append(True) or True
    runtime._append_log = logs.append
    runtime.controller = _successful_controller()
    runtime.config.gather.enabled = False
    runtime.config.alerts.enabled = False

    runtime._start_bot(save_first=False)

    assert save_calls == []
    assert logs == ["Running Rally"]


def test_manual_start_still_saves_current_ui_first():
    runtime = _harness()
    save_calls = []
    runtime._save_from_ui = lambda: save_calls.append(True) or True
    runtime._append_log = lambda _message: None
    runtime.controller = _successful_controller()
    runtime.config.gather.enabled = False
    runtime.config.alerts.enabled = False

    runtime._start_bot()

    assert save_calls == [True]


def test_requested_automation_failure_is_not_hidden_by_running_alerts():
    runtime = _harness()
    runtime.config.alerts.enabled = True
    runtime.config.gather.enabled = False
    logs = []
    runtime._append_log = logs.append
    runtime._start_alerts = lambda save_first=False: True
    runtime.controller = SimpleNamespace(
        status=SimpleNamespace(
            last_message="Bot cycle stopped: could not start Rally"
        ),
        enabled_features=lambda: ["rally"],
        start=lambda: False,
    )

    with patch("macro_clicker.bot.ui_runtime.messagebox.showwarning") as warning:
        runtime._start_bot(save_first=False)

    assert logs == [
        "Bot cycle stopped: could not start Rally",
        "Passive alerts are still running.",
    ]
    warning.assert_called_once_with(
        "Bot automation did not start",
        "Bot cycle stopped: could not start Rally",
        parent=runtime,
    )
