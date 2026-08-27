from pathlib import Path

from macro_clicker.bot.team_state import TeamActivity
from macro_clicker.bot.team_status import (
    BUSY_COUNT_TEMPLATES,
    BUSY_IDENTITY_TEMPLATES,
    WORLD_MAP_TEMPLATE,
    TeamStatusDetector,
)
from macro_clicker.models import project_path


def _activities(observations):
    return {item.team: item.activity for item in observations}


def test_zero_busy_status_means_all_three_teams_are_idle_candidates():
    observations = TeamStatusDetector._observations_from_busy_evidence(
        0,
        team1_busy=False,
        team3_busy=False,
    )

    assert _activities(observations) == {
        1: TeamActivity.IDLE,
        2: TeamActivity.IDLE,
        3: TeamActivity.IDLE,
    }


def test_single_busy_team2_is_inferred_when_known_portraits_are_absent():
    observations = TeamStatusDetector._observations_from_busy_evidence(
        1,
        team1_busy=False,
        team3_busy=False,
    )

    assert _activities(observations) == {
        1: TeamActivity.IDLE,
        2: TeamActivity.BUSY,
        3: TeamActivity.IDLE,
    }


def test_two_busy_teams_are_inferred_from_count_and_team1_portrait():
    observations = TeamStatusDetector._observations_from_busy_evidence(
        2,
        team1_busy=True,
        team3_busy=False,
    )

    assert _activities(observations) == {
        1: TeamActivity.BUSY,
        2: TeamActivity.BUSY,
        3: TeamActivity.IDLE,
    }


def test_three_busy_count_marks_every_team_busy():
    observations = TeamStatusDetector._observations_from_busy_evidence(
        3,
        team1_busy=True,
        team3_busy=True,
    )

    assert all(item.activity == TeamActivity.BUSY for item in observations)


def test_contradictory_zero_count_and_busy_portrait_fails_closed():
    observations = TeamStatusDetector._observations_from_busy_evidence(
        0,
        team1_busy=True,
        team3_busy=False,
    )

    assert all(item.activity == TeamActivity.UNKNOWN for item in observations)


def test_contradictory_single_count_with_two_known_busy_portraits_fails_closed():
    observations = TeamStatusDetector._observations_from_busy_evidence(
        1,
        team1_busy=True,
        team3_busy=True,
    )

    assert all(item.activity == TeamActivity.UNKNOWN for item in observations)


def test_team_status_detector_uses_only_committed_existing_templates():
    paths = {
        WORLD_MAP_TEMPLATE,
        *BUSY_COUNT_TEMPLATES.values(),
        *BUSY_IDENTITY_TEMPLATES.values(),
    }

    assert "templates/TeamStatusSidebarHeader.png" not in paths
    for relative_path in paths:
        assert Path(project_path(relative_path)).is_file(), relative_path
