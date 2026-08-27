from pathlib import Path

import cv2

from macro_clicker.bot.team_state import TeamActivity
from macro_clicker.bot.team_status import (
    BUSY_COUNT_TEMPLATES,
    BUSY_IDENTITY_TEMPLATES,
    WORLD_MAP_ANCHOR_REGION,
    WORLD_MAP_TEMPLATE,
    WORLD_MAP_THRESHOLD,
    TeamStatusDetector,
)
from macro_clicker.models import project_path


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "team_status"


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


def test_real_world_map_search_anchor_matches_the_gate_template():
    fixture = cv2.imread(
        str(FIXTURE_DIR / "world_map_search_anchor.jpg"),
        cv2.IMREAD_COLOR,
    )
    template = cv2.imread(project_path(WORLD_MAP_TEMPLATE), cv2.IMREAD_COLOR)

    assert fixture is not None
    assert template is not None
    score, _location = TeamStatusDetector._best_match(fixture, template)

    assert WORLD_MAP_TEMPLATE == "templates/GatherSearchIcon.jpg"
    assert WORLD_MAP_ANCHOR_REGION == (0, 780, 110, 150)
    assert score >= WORLD_MAP_THRESHOLD


def test_team_status_detector_uses_only_committed_existing_templates():
    paths = {
        WORLD_MAP_TEMPLATE,
        *BUSY_COUNT_TEMPLATES.values(),
        *BUSY_IDENTITY_TEMPLATES.values(),
    }

    assert "templates/TeamStatusSidebarHeader.png" not in paths
    for relative_path in paths:
        assert Path(project_path(relative_path)).is_file(), relative_path
