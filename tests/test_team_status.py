from pathlib import Path

import cv2
import numpy as np

from macro_clicker.bot.team_state import TeamActivity
from macro_clicker.bot.team_status import (
    ACTIVITY_TEMPLATES,
    BOOTSTRAP_IDENTITY_TEMPLATES,
    BUSY_COUNT_TEMPLATES,
    BUSY_ROW_REGIONS,
    WORLD_MAP_ANCHOR_REGION,
    WORLD_MAP_TEMPLATE,
    WORLD_MAP_THRESHOLD,
    TeamStatusDetector,
    parse_duration_text,
)
from macro_clicker.models import project_path


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "team_status"


def _activities(observations):
    return {item.team: item.activity for item in observations}


def _remaining(observations):
    return {item.team: item.remaining_seconds for item in observations}


def test_duration_parser_handles_normal_and_common_ocr_confusions():
    assert parse_duration_text("06:50:29") == 6 * 3600 + 50 * 60 + 29
    assert parse_duration_text("OO:O3:39") == 219
    assert parse_duration_text("00；00；06") == 6
    assert parse_duration_text("not a timer") is None


def test_partial_busy_without_identity_does_not_guess_team2():
    candidates = TeamStatusDetector._candidate_assignments(1, (None,))

    assert candidates == [(1,), (2,), (3,)]


def test_exact_dispatch_history_can_resolve_one_compressed_busy_row():
    candidates = TeamStatusDetector._candidate_assignments(
        1,
        (None,),
        known_busy_teams=(3,),
    )

    assert candidates == [(3,)]


def test_two_rows_with_team1_and_team3_keep_team3_in_second_visual_slot():
    candidates = TeamStatusDetector._candidate_assignments(2, (1, 3))

    assert candidates == [(1, 3)]


def test_three_busy_rows_are_always_team1_team2_team3_order():
    candidates = TeamStatusDetector._candidate_assignments(3, (None, None, None))

    assert candidates == [(1, 2, 3)]


def test_resolved_rows_keep_status_and_timer_attached_to_team_identity():
    detector = TeamStatusDetector(portrait_cache_dir=None)
    rows = [np.zeros((60, 205, 3), dtype=np.uint8) for _ in range(3)]
    observations = detector._observations(
        3,
        rows,
        [1, 2, 3],
        [
            TeamActivity.GATHERING,
            TeamActivity.RETURNING,
            TeamActivity.RALLYING,
        ],
        [6 * 3600 + 50 * 60 + 29, 219, 54],
        known_busy_teams=(),
        confidence=0.95,
    )

    assert _activities(observations) == {
        1: TeamActivity.GATHERING,
        2: TeamActivity.RETURNING,
        3: TeamActivity.RALLYING,
    }
    assert _remaining(observations) == {
        1: 6 * 3600 + 50 * 60 + 29,
        2: 219,
        3: 54,
    }


def test_each_real_status_crop_is_recognized_as_its_activity():
    detector = TeamStatusDetector(portrait_cache_dir=None)

    for expected, relative_path in ACTIVITY_TEMPLATES.items():
        image = cv2.imread(project_path(relative_path), cv2.IMREAD_COLOR)
        assert image is not None, relative_path
        activity, score = detector._activity(image)
        assert activity == expected
        assert score >= 0.99


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
        *ACTIVITY_TEMPLATES.values(),
        *BOOTSTRAP_IDENTITY_TEMPLATES.values(),
    }

    assert "templates/TeamStatusSidebarHeader.png" not in paths
    assert len(BUSY_ROW_REGIONS) == 3
    for relative_path in paths:
        assert Path(project_path(relative_path)).is_file(), relative_path
