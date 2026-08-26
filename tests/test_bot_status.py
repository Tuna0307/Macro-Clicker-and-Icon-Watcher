from types import SimpleNamespace

from macro_clicker.bot.config import BotConfig
from macro_clicker.bot.continuous_gather import ContinuousGatherService
from macro_clicker.bot.controller import (
    FEATURE_DEVELOPMENT,
    FEATURE_RALLY,
    BotController,
)
from macro_clicker.bot.status import build_dashboard_snapshot
from macro_clicker.bot.team_state import TeamActivity, TeamObservation, TeamStateTracker


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
    # Continuous Gather is no longer a queued one-shot stage. Rally is the next
    # queued clicking task; the Gather service reports separately.
    assert snapshot.next_task == "Gold Mob Rally"
    assert snapshot.overall == "● Running — Development Position"


def test_dashboard_snapshot_reports_continuous_gather_team_state_and_timers():
    config = BotConfig()
    config.gather.enabled = True
    config.rally.enabled = False
    tracker = TeamStateTracker()
    tracker.update(
        (
            TeamObservation(1, TeamActivity.GATHERING, remaining_seconds=3600),
            TeamObservation(2, TeamActivity.IDLE),
            TeamObservation(3, TeamActivity.RETURNING, remaining_seconds=15),
        ),
        sidebar_visible=True,
    )
    service = ContinuousGatherService(lambda: config, tracker, lambda _team: True)
    assert service.start() is True
    controller = _controller(config)

    snapshot = build_dashboard_snapshot(
        config=config,
        controller=controller,
        team_tracker=tracker,
        continuous_gather=service,
    )

    assert snapshot.current_task == "Auto Gather"
    assert snapshot.overall == "● Running — Auto Gather"
    assert snapshot.gather == "Watching — available: Team 2"
    assert snapshot.team_status[1].startswith("Gathering — 01:00:")
    assert snapshot.team_status[2] == "Idle"
    assert snapshot.team_status[3].startswith("Returning — 00:00:")
    assert snapshot.last_status == "Watching team status for an available team"


def test_dashboard_snapshot_reports_exact_team_while_dispatching():
    config = BotConfig()
    config.gather.enabled = True
    config.rally.enabled = False
    tracker = TeamStateTracker()
    tracker.update(
        (
            TeamObservation(1, TeamActivity.GATHERING, remaining_seconds=100),
            TeamObservation(2, TeamActivity.IDLE),
            TeamObservation(3, TeamActivity.GATHERING, remaining_seconds=200),
        ),
        sidebar_visible=True,
    )
    service = ContinuousGatherService(lambda: config, tracker, lambda _team: True)
    service.start()
    service.status.in_flight_team = 2
    engine = SimpleNamespace(_last_fired={"Gather - Dispatch Ready": 12.0})
    controller = _controller(config)

    snapshot = build_dashboard_snapshot(
        config=config,
        controller=controller,
        engine=engine,
        team_tracker=tracker,
        continuous_gather=service,
    )

    assert snapshot.overall == "● Running — Auto Gather — Team 2"
    assert snapshot.gather == "Running — Team 2 — selecting team and dispatching"


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
    assert snapshot.last_status == "Selected Team 3 for Lv58 rally"


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


def test_dashboard_snapshot_describes_position_yield_when_retry_is_disabled():
    config = BotConfig()
    config.positions.development_enabled = True
    config.positions.retry_automatically = False
    controller = _controller(config)
    controller.status.active_feature = FEATURE_DEVELOPMENT
    controller.status.running = True
    engine = SimpleNamespace(
        _last_fired={"Retry - Apply Unavailable": 8.0},
    )

    snapshot = build_dashboard_snapshot(
        config=config,
        controller=controller,
        engine=engine,
    )

    assert "auto retry off" in snapshot.positions
    assert snapshot.last_status == "Development Position unavailable; finishing this task"


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
