from types import SimpleNamespace

from macro_clicker.bot.config import BotConfig
from macro_clicker.bot.controller import (
    FEATURE_DEVELOPMENT,
    FEATURE_GATHER,
    FEATURE_RALLY,
    BotController,
)
from macro_clicker.bot.status import build_dashboard_snapshot
from macro_clicker.resource_gathering import GatherController


def _controller(config):
    return BotController(lambda: config, lambda _feature: True, lambda: True)


def test_dashboard_snapshot_shows_next_queued_task():
    config = BotConfig()
    config.positions.development_enabled = True
    config.gather.enabled = True
    config.rally.enabled = True
    controller = _controller(config)

    assert controller.start() is True
    snapshot = build_dashboard_snapshot(config=config, controller=controller)

    assert snapshot.current_task == "Development Position"
    assert snapshot.next_task == "Auto Gather"
    assert snapshot.overall == "● Running — Development Position"


def test_dashboard_snapshot_reports_live_gather_progress_and_pointer():
    config = BotConfig()
    config.gather.enabled = True
    config.gather.march_count = 3
    controller = _controller(config)
    controller.status.active_feature = FEATURE_GATHER
    controller.status.running = True

    gather = GatherController()
    gather.record_success(target_count=3, replacement_order=[3, 2, 1])
    engine = SimpleNamespace(
        _gather_controller=gather,
        _last_fired={"Gather - Search unavailable": 12.0},
    )

    snapshot = build_dashboard_snapshot(
        config=config,
        controller=controller,
        engine=engine,
    )

    assert "1/3 successful" in snapshot.gather
    assert "lowering level and searching again" in snapshot.gather
    assert "March 3" in snapshot.gather


def test_dashboard_snapshot_reports_selected_rally_team_and_level():
    config = BotConfig()
    controller = _controller(config)
    controller.status.active_feature = FEATURE_RALLY
    controller.status.running = True
    engine = SimpleNamespace(
        _pending_rally_team_selected={"level": 58, "team": 3},
        _pending_rally_level=58,
        _last_fired={"Joining": 5.0},
    )

    snapshot = build_dashboard_snapshot(
        config=config,
        controller=controller,
        engine=engine,
    )

    assert snapshot.rally == "Running — Lv58 with Team 3"


def test_dashboard_snapshot_keeps_alerts_parallel_to_active_automation():
    config = BotConfig()
    config.alerts.enabled = True
    controller = _controller(config)
    controller.status.active_feature = FEATURE_DEVELOPMENT
    controller.status.running = True

    snapshot = build_dashboard_snapshot(
        config=config,
        controller=controller,
        alerts_running=True,
    )

    assert snapshot.overall == "● Running — Development Position + Alerts"
    assert snapshot.alerts.startswith("Watching —")


def test_dashboard_snapshot_describes_saved_schedule_when_idle():
    config = BotConfig()
    config.rally.enabled = False
    config.schedule.enabled = True
    config.schedule.start_time = "06:30"
    config.schedule.stop_time = "22:45"
    config.schedule.days = ["Mon", "Wed", "Fri"]
    controller = _controller(config)

    snapshot = build_dashboard_snapshot(config=config, controller=controller)

    assert snapshot.overall == "● Stopped"
    assert snapshot.schedule == "Active — 06:30–22:45 — Mon Wed Fri"
