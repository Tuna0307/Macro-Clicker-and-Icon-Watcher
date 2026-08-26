from copy import deepcopy
from types import SimpleNamespace

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
    runtime.gather_marches_var = _Var(config.gather.march_count)
    runtime.replacement_order_var = _Var("3 → 2 → 1")
    runtime.development_enabled_var = _Var(config.positions.development_enabled)
    runtime.science_enabled_var = _Var(config.positions.science_enabled)
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


def test_collect_config_returns_pending_copy_without_mutating_live_config():
    runtime = _harness()
    original = deepcopy(runtime.config)
    runtime.rally_min_var = _Var(12)

    pending = runtime._collect_config()

    assert pending is not runtime.config
    assert pending.rally.min_level == 12
    assert runtime.config == original


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
    runtime.controller = SimpleNamespace(
        status=SimpleNamespace(last_message="Running Rally"),
        start=lambda: True,
    )

    runtime._start_bot(save_first=False)

    assert save_calls == []
    assert logs == ["Running Rally"]


def test_manual_start_still_saves_current_ui_first():
    runtime = _harness()
    save_calls = []
    runtime._save_from_ui = lambda: save_calls.append(True) or True
    runtime._append_log = lambda _message: None
    runtime.controller = SimpleNamespace(
        status=SimpleNamespace(last_message="Running Rally"),
        start=lambda: True,
    )

    runtime._start_bot()

    assert save_calls == [True]
