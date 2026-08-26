import time

from macro_clicker.bot.config import BotConfig
from macro_clicker.bot.continuous_gather import ContinuousGatherService
from macro_clicker.bot.team_state import TeamActivity, TeamObservation, TeamStateTracker


def _config(*teams):
    config = BotConfig()
    config.gather.enabled = True
    config.gather.teams_enabled = list(teams or (1, 2, 3))
    return config


def test_timer_expiry_requests_visual_refresh_but_never_makes_team_idle():
    tracker = TeamStateTracker()
    tracker.update(
        (TeamObservation(1, TeamActivity.GATHERING, remaining_seconds=5),),
        sidebar_visible=True,
        observed_at=100.0,
    )

    snapshot = tracker.snapshot(1, now=106.0)

    assert snapshot.activity == TeamActivity.GATHERING
    assert snapshot.remaining_seconds == 0
    assert snapshot.needs_visual_refresh is True
    assert tracker.available_teams((1,), now=106.0) == ()


def test_hidden_sidebar_never_dispatches_from_last_known_idle_state():
    tracker = TeamStateTracker()
    tracker.update(
        (TeamObservation(1, TeamActivity.IDLE),),
        sidebar_visible=True,
    )
    service = ContinuousGatherService(lambda: _config(1), tracker, lambda _team: True)
    service.start()
    assert service.next_idle_team() == 1

    tracker.update((), sidebar_visible=False)

    assert tracker.snapshot(1).activity == TeamActivity.IDLE
    assert service.next_idle_team() is None


def test_service_ignores_disabled_idle_team_and_chooses_enabled_idle_team():
    tracker = TeamStateTracker()
    tracker.update(
        (
            TeamObservation(1, TeamActivity.GATHERING, remaining_seconds=120),
            TeamObservation(2, TeamActivity.IDLE),
            TeamObservation(3, TeamActivity.IDLE),
        ),
        sidebar_visible=True,
    )
    started = []
    config = _config(1, 3)
    service = ContinuousGatherService(
        lambda: config,
        tracker,
        lambda team: started.append(team) or True,
    )
    service.start()

    assert service.try_start_next(input_engine_busy=False) is True
    assert started == [3]
    assert service.status.in_flight_team == 3


def test_all_busy_teams_wait_without_starting_or_replacing_any_team():
    tracker = TeamStateTracker()
    tracker.update(
        (
            TeamObservation(1, TeamActivity.GATHERING, remaining_seconds=120),
            TeamObservation(2, TeamActivity.RETURNING, remaining_seconds=10),
            TeamObservation(3, TeamActivity.TRAVELLING, remaining_seconds=20),
        ),
        sidebar_visible=True,
    )
    started = []
    service = ContinuousGatherService(
        lambda: _config(),
        tracker,
        lambda team: started.append(team) or True,
    )
    service.start()

    assert service.try_start_next(input_engine_busy=False) is False
    assert started == []
    assert service.status.active is True
    assert service.status.in_flight_team is None
    assert service.status.last_message == "All configured teams are busy; waiting"


def test_successful_attempt_immediately_marks_exact_team_non_idle():
    tracker = TeamStateTracker()
    tracker.update(
        (
            TeamObservation(1, TeamActivity.IDLE),
            TeamObservation(2, TeamActivity.GATHERING, remaining_seconds=200),
            TeamObservation(3, TeamActivity.GATHERING, remaining_seconds=300),
        ),
        sidebar_visible=True,
    )
    service = ContinuousGatherService(lambda: _config(), tracker, lambda _team: True)
    service.start()

    assert service.try_start_next(input_engine_busy=False) is True
    assert service.status.in_flight_team == 1

    service.complete_attempt(success=True)

    assert service.status.in_flight_team is None
    assert service.status.successful_dispatches == 1
    assert tracker.snapshot(1).activity == TeamActivity.TRAVELLING
    assert service.next_idle_team() is None


def test_unconfirmed_attempt_pauses_fail_closed_instead_of_retrying():
    tracker = TeamStateTracker()
    tracker.update(
        (TeamObservation(2, TeamActivity.IDLE),),
        sidebar_visible=True,
    )
    started = []
    service = ContinuousGatherService(
        lambda: _config(2),
        tracker,
        lambda team: started.append(team) or True,
    )
    service.start()

    assert service.try_start_next(input_engine_busy=False) is True
    service.complete_attempt(success=False)

    assert started == [2]
    assert service.status.active is False
    assert service.status.in_flight_team is None
    assert "dispatch was not confirmed" in service.status.last_message
    assert service.try_start_next(input_engine_busy=False) is False


def test_stale_idle_observation_is_not_used_for_dispatch():
    tracker = TeamStateTracker()
    tracker.update(
        (TeamObservation(3, TeamActivity.IDLE),),
        sidebar_visible=True,
        observed_at=time.monotonic() - 10.0,
    )
    started = []
    service = ContinuousGatherService(
        lambda: _config(3),
        tracker,
        lambda team: started.append(team) or True,
    )
    service.start()

    assert service.try_start_next(input_engine_busy=False) is False
    assert started == []
