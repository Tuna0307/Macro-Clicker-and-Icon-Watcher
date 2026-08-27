import sys
from pathlib import Path
from types import SimpleNamespace

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
    TeamTimerReader,
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


def test_ocr_initialization_emits_start_and_ready_diagnostics(monkeypatch):
    logs = []

    class FakeTextRecognition:
        def __init__(self, **_kwargs):
            pass

    monkeypatch.setitem(
        sys.modules,
        "paddleocr",
        SimpleNamespace(TextRecognition=FakeTextRecognition),
    )
    reader = TeamTimerReader(diagnostic_log=logs.append)

    engine = reader._get_engine()

    assert isinstance(engine, FakeTextRecognition)
    assert logs[0] == "[team-diag] OCR initialization started"
    assert logs[1].startswith("[team-diag] OCR initialization ready in ")
    assert logs[1].endswith("s")


def test_busy_count_records_each_template_score_and_selected_count(monkeypatch):
    detector = TeamStatusDetector(portrait_cache_dir=None)
    by_path = {
        BUSY_COUNT_TEMPLATES[1]: 0.31,
        BUSY_COUNT_TEMPLATES[2]: 0.91,
        BUSY_COUNT_TEMPLATES[3]: 0.27,
    }
    monkeypatch.setattr(detector, "_template", lambda path: path)
    monkeypatch.setattr(
        detector,
        "_best_match",
        lambda _frame, template: (by_path[template], (0, 0)),
    )

    count, score = detector._busy_count(np.zeros((28, 51, 3), dtype=np.uint8))

    assert count == 2
    assert score == 0.91
    assert detector.last_busy_count_score == 0.91
    assert detector.last_busy_count_scores == {1: 0.31, 2: 0.91, 3: 0.27}


def test_world_map_score_is_retained_when_gate_rejects_frame(monkeypatch):
    detector = TeamStatusDetector(portrait_cache_dir=None)
    monkeypatch.setattr(
        detector,
        "_template",
        lambda _path: np.zeros((10, 10, 3), dtype=np.uint8),
    )
    monkeypatch.setattr(
        detector,
        "_best_match",
        lambda _frame, _template: (0.42, (0, 0)),
    )

    visible, observations = detector.detect(
        np.zeros((1080, 1920, 3), dtype=np.uint8),
        read_timers=False,
    )

    assert not visible
    assert observations == ()
    assert detector.last_world_map_score == 0.42
    assert detector.last_busy_count is None


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


def test_missing_optional_status_templates_degrade_to_busy(monkeypatch):
    detector = TeamStatusDetector(portrait_cache_dir=None)
    missing = set(ACTIVITY_TEMPLATES.values())

    def unavailable(path):
        if path in missing:
            raise FileNotFoundError(path)
        raise AssertionError(f"unexpected template request: {path}")

    monkeypatch.setattr(detector, "_template", unavailable)

    activity, score = detector._activity(
        np.zeros((60, 205, 3), dtype=np.uint8)
    )

    assert activity == TeamActivity.BUSY
    assert score == 0.0
    assert set(detector.missing_activity_templates) == missing


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
