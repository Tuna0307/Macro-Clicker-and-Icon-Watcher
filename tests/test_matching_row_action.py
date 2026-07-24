import unittest
from unittest.mock import patch

import numpy as np

from macro_clicker.engine import MacroEngine
from macro_clicker.level_ocr import LevelOcrResult
from macro_clicker.models import Action, ImageCondition, Scenario, Step


class MatchingRowActionTests(unittest.TestCase):
    def test_matching_row_anchors_and_level_crops_share_one_atomic_snapshot(self):
        engine = object.__new__(MacroEngine)
        engine.scenario = Scenario(name="atomic", monitor_index=1)
        engine._stop_event = type("Stop", (), {"is_set": lambda self: False})()
        engine._all_match_indices = {}
        engine._evaluate_uses_frame_cache = True
        engine._level_offset_cache = {}
        engine._window_rect_lookup_cache = None
        engine._matching_row_snapshot = None
        captures = []

        def grab(region):
            captures.append(region)
            generation = len(captures)
            return (
                np.full((region[3], region[2], 3), generation, dtype=np.uint8),
                region[0],
                region[1],
            )

        def evaluate(index, _condition, frame, _off_x, _off_y, collect_all):
            self.assertTrue(collect_all)
            self.assertEqual(int(frame[0, 0, 0]), 1)
            center = (100, 100) if index == 0 else (250, 100)
            return True, [{"center": center, "box": (*center, *center)}]

        engine._grab = grab
        engine._resolve_capture_region = lambda condition: condition.region
        engine._evaluate_template_condition = evaluate
        action = Action(
            type="click_matching_row",
            match_condition_index=0,
            on_condition_index=1,
            max_level=60,
            level_roi=[-20, -10, 40, 20],
        )
        step = Step(
            name="Joining",
            conditions=[
                ImageCondition(template_path="mob.png", region=[0, 0, 200, 200]),
                ImageCondition(template_path="join.png", region=[200, 0, 100, 200]),
            ],
            actions=[action],
        )

        refreshed = engine._refresh_click_matching_row_matches(step, action)
        self.assertIsNotNone(refreshed)
        _points, matches = refreshed
        candidates = engine._capture_level_crop_candidates(
            action,
            matches[0][0],
        )

        self.assertEqual(len(captures), 1)
        capture_left, capture_top, capture_width, capture_height = captures[0]
        self.assertLessEqual(capture_left, 0)
        self.assertLessEqual(capture_top, 0)
        self.assertGreaterEqual(capture_left + capture_width, 300)
        self.assertGreaterEqual(capture_top + capture_height, 200)
        self.assertEqual(len(candidates), 6)
        self.assertTrue(all(np.all(frame == 1) for _offset, _rect, frame in candidates))
        self.assertTrue(
            all(
                not np.shares_memory(frame, engine._matching_row_snapshot.frame)
                for _offset, _rect, frame in candidates
            )
        )
        engine._matching_row_snapshot.frame.fill(9)
        self.assertTrue(all(np.all(frame == 1) for _offset, _rect, frame in candidates))

    def test_atomic_snapshot_includes_level_roi_outside_tight_search_region(self):
        engine = object.__new__(MacroEngine)
        engine.scenario = Scenario(name="tight atomic", monitor_index=1)
        engine._stop_event = type("Stop", (), {"is_set": lambda self: False})()
        engine._all_match_indices = {}
        engine._evaluate_uses_frame_cache = True
        engine._level_offset_cache = {}
        engine._window_rect_lookup_cache = None
        engine._matching_row_snapshot = None
        captures = []

        def grab(region):
            captures.append(region)
            return (
                np.full((region[3], region[2], 3), len(captures), dtype=np.uint8),
                region[0],
                region[1],
            )

        def evaluate(index, _condition, frame, _off_x, _off_y, collect_all):
            self.assertTrue(collect_all)
            self.assertEqual(int(frame[0, 0, 0]), 1)
            center = (110, 110) if index == 0 else (210, 110)
            return True, [{"center": center, "box": (*center, *center)}]

        engine._grab = grab
        engine._resolve_capture_region = lambda condition: condition.region
        engine._evaluate_template_condition = evaluate
        action = Action(
            type="click_matching_row",
            match_condition_index=0,
            on_condition_index=1,
            max_level=60,
            level_roi=[0, 30, 20, 20],
        )
        step = Step(
            name="Joining",
            conditions=[
                ImageCondition(template_path="mob.png", region=[100, 100, 20, 20]),
                ImageCondition(template_path="join.png", region=[200, 100, 20, 20]),
            ],
            actions=[action],
        )

        refreshed = engine._refresh_click_matching_row_matches(step, action)
        self.assertIsNotNone(refreshed)
        _points, matches = refreshed
        candidates = engine._capture_level_crop_candidates(action, matches[0][0])

        self.assertEqual(len(captures), 1)
        self.assertEqual(len(candidates), 6)
        self.assertTrue(all(np.all(frame == 1) for _offset, _rect, frame in candidates))

    def test_unbounded_smart_row_snapshot_still_contains_level_crops(self):
        engine = object.__new__(MacroEngine)
        engine.scenario = Scenario(name="smart atomic", monitor_index=1)
        engine._stop_event = type("Stop", (), {"is_set": lambda self: False})()
        engine._all_match_indices = {}
        engine._evaluate_uses_frame_cache = True
        engine._level_offset_cache = {}
        engine._window_rect_lookup_cache = None
        engine._matching_row_snapshot = None
        captures = []

        def grab(region):
            captures.append(region)
            return (
                np.full((region[3], region[2], 3), len(captures), dtype=np.uint8),
                region[0],
                region[1],
            )

        def evaluate(index, _condition, frame, _off_x, _off_y, collect_all):
            self.assertTrue(collect_all)
            self.assertEqual(int(frame[0, 0, 0]), 1)
            center = (110, 110) if index == 0 else (210, 110)
            return True, [{"center": center, "box": (*center, *center)}]

        engine._grab = grab
        engine._resolve_capture_region = lambda condition: condition.region
        engine._evaluate_template_condition = evaluate
        action = Action(
            type="click_matching_row",
            match_condition_index=0,
            on_condition_index=1,
            min_level=None,
            max_level=None,
            level_roi=[0, 30, 20, 20],
            team_status_region=[0, 0, 100, 100],
            team_status_reference_size=[1920, 1080],
            team1_busy_template_path="team1-busy.png",
            team3_busy_template_path="team3-busy.png",
        )
        step = Step(
            name="Joining",
            conditions=[
                ImageCondition(template_path="mob.png", region=[100, 100, 20, 20]),
                ImageCondition(template_path="join.png", region=[200, 100, 20, 20]),
            ],
            actions=[action],
        )

        refreshed = engine._refresh_click_matching_row_matches(step, action)
        self.assertIsNotNone(refreshed)
        _points, matches = refreshed
        candidates = engine._capture_level_crop_candidates(action, matches[0][0])

        self.assertEqual(len(captures), 1)
        self.assertEqual(len(candidates), 6)
        self.assertTrue(all(np.all(frame == 1) for _offset, _rect, frame in candidates))

    def test_row_tolerance_scales_with_reference_match_geometry(self):
        engine = object.__new__(MacroEngine)
        action = Action(
            type="click_matching_row",
            match_condition_index=0,
            on_condition_index=1,
            row_tolerance=60,
        )
        reference = {
            "center": (100, 100),
            "scale_x": 4 / 3,
            "scale_y": 4 / 3,
        }
        target = {
            "center": (300, 167),
            "scale_x": 4 / 3,
            "scale_y": 4 / 3,
        }

        selected = engine._find_matching_row_targets(
            action,
            {0: [reference], 1: [target]},
        )

        self.assertEqual(selected, [target])

    def test_rally_level_roi_and_retry_offsets_scale_to_1440p(self):
        engine = object.__new__(MacroEngine)
        action = Action(
            type="click_matching_row",
            level_roi=[-65, 25, 150, 45],
        )
        reference = {
            "center": (1000, 500),
            "scale_x": 4 / 3,
            "scale_y": 4 / 3,
        }

        rects = engine._level_crop_rects(
            action,
            reference,
            window_rect=(0, 0, 2560, 1440),
        )

        self.assertEqual(rects[0], (913, 533, 200, 60))
        self.assertEqual(rects[1][1] - rects[0][1], 11)

    def test_detected_click_offsets_scale_with_target_match(self):
        clicked = []
        engine = object.__new__(MacroEngine)
        engine._stop_event = type("Stop", (), {"is_set": lambda self: False})()
        engine.log = lambda _message: None
        engine._click_point = lambda x, y, button: clicked.append((x, y, button))
        action = Action(
            type="click",
            on_condition_index=0,
            offset_x=3,
            offset_y=6,
        )

        engine._run_action(
            Step(name="scaled click"),
            action,
            {0: (100, 200)},
            {0: [{"center": (100, 200), "scale_x": 4 / 3, "scale_y": 4 / 3}]},
        )

        self.assertEqual(clicked, [(104, 208, "left")])

    def test_matching_row_action_clicks_target_on_same_row_as_reference(self):
        clicked = []
        engine = object.__new__(MacroEngine)
        engine.scenario = Scenario(name="war")
        engine._stop_event = type("Stop", (), {"is_set": lambda self: False})()
        engine._last_fired = {"join": 0.0}
        engine._evaluate_step = lambda step: (
            True,
            {0: (80, 120), 1: (300, 120)},
            {
                0: [
                    {
                        "center": (80, 120),
                        "box": (40, 90, 120, 150),
                        "label": "Mob.png 0.91",
                    },
                    {
                        "center": (80, 320),
                        "box": (40, 290, 120, 350),
                        "label": "Mob.png 0.90",
                    },
                ],
                1: [
                    {
                        "center": (300, 320),
                        "box": (260, 290, 340, 350),
                        "label": "joining 0.95",
                    },
                    {
                        "center": (300, 120),
                        "box": (260, 90, 340, 150),
                        "label": "joining 0.93",
                    },
                ],
            },
        )
        engine.log = lambda message: None
        engine._click_point = lambda x, y, button: clicked.append((x, y, button))
        step = Step(
            name="join",
            conditions=[
                ImageCondition(template_path="templates/Mob.png"),
                ImageCondition(template_path="templates/Join.png"),
            ],
            actions=[
                Action(
                    type="click_matching_row",
                    match_condition_index=0,
                    on_condition_index=1,
                    row_tolerance=40,
                )
            ],
            cooldown=10.0,
            repeatable=True,
        )
        engine.scenario.steps = [step]

        engine._cycle()

        self.assertEqual(clicked, [(300, 120, "left")])

    def test_matching_row_action_clicks_rightmost_target_for_all_matching_rows(self):
        clicked = []
        engine = object.__new__(MacroEngine)
        engine.scenario = Scenario(name="war")
        engine._stop_event = type("Stop", (), {"is_set": lambda self: False})()
        engine._last_fired = {"join": 0.0}
        engine._evaluate_step = lambda step: (
            True,
            {0: (80, 120), 1: (200, 120)},
            {
                0: [
                    {
                        "center": (80, 120),
                        "box": (40, 90, 120, 150),
                        "label": "Mob.png 0.91",
                    },
                    {
                        "center": (80, 320),
                        "box": (40, 290, 120, 350),
                        "label": "Mob.png 0.90",
                    },
                ],
                1: [
                    {
                        "center": (180, 120),
                        "box": (160, 100, 200, 140),
                        "label": "joining 0.91",
                    },
                    {
                        "center": (260, 120),
                        "box": (240, 100, 280, 140),
                        "label": "joining 0.92",
                    },
                    {
                        "center": (340, 120),
                        "box": (320, 100, 360, 140),
                        "label": "joining 0.93",
                    },
                    {
                        "center": (180, 320),
                        "box": (160, 300, 200, 340),
                        "label": "joining 0.94",
                    },
                    {
                        "center": (300, 320),
                        "box": (280, 300, 320, 340),
                        "label": "joining 0.95",
                    },
                ],
            },
        )
        engine.log = lambda message: None
        engine._click_point = lambda x, y, button: clicked.append((x, y, button))
        step = Step(
            name="join",
            conditions=[
                ImageCondition(template_path="templates/Mob.png"),
                ImageCondition(template_path="templates/Join.png"),
            ],
            actions=[
                Action(
                    type="click_matching_row",
                    match_condition_index=0,
                    on_condition_index=1,
                    row_tolerance=40,
                    row_mode="all",
                    target_choice="rightmost",
                )
            ],
            cooldown=0.0,
            repeatable=True,
        )
        engine.scenario.steps = [step]

        engine._cycle()

        self.assertEqual(clicked, [(340, 120, "left"), (300, 320, "left")])

    def test_matching_row_action_skips_rows_below_min_level(self):
        clicked = []
        engine = object.__new__(MacroEngine)
        engine.scenario = Scenario(name="war")
        engine._stop_event = type("Stop", (), {"is_set": lambda self: False})()
        engine._last_fired = {"join": 0.0}
        engine._evaluate_step = lambda step: (
            True,
            {0: (80, 120), 1: (200, 120)},
            {
                0: [
                    {
                        "center": (80, 120),
                        "box": (40, 90, 120, 150),
                        "label": "Mob.png 0.91",
                    },
                    {
                        "center": (80, 320),
                        "box": (40, 290, 120, 350),
                        "label": "Mob.png 0.90",
                    },
                ],
                1: [
                    {
                        "center": (260, 120),
                        "box": (240, 100, 280, 140),
                        "label": "joining 0.92",
                    },
                    {
                        "center": (300, 320),
                        "box": (280, 300, 320, 340),
                        "label": "joining 0.95",
                    },
                ],
            },
        )
        engine._read_level_for_row = lambda action, reference: (
            26 if reference["center"][1] == 120 else 27
        )
        engine.log = lambda message: None
        engine._click_point = lambda x, y, button: clicked.append((x, y, button))
        step = Step(
            name="join",
            conditions=[
                ImageCondition(template_path="templates/Mob.png"),
                ImageCondition(template_path="templates/Join.png"),
            ],
            actions=[
                Action(
                    type="click_matching_row",
                    match_condition_index=0,
                    on_condition_index=1,
                    row_tolerance=40,
                    row_mode="all",
                    min_level=27,
                )
            ],
            cooldown=0.0,
            repeatable=True,
        )
        engine.scenario.steps = [step]

        engine._cycle()

        self.assertEqual(clicked, [(300, 320, "left")])

    def test_matching_row_action_skips_rows_when_level_cannot_be_read(self):
        clicked = []
        engine = object.__new__(MacroEngine)
        engine.scenario = Scenario(name="war")
        engine._stop_event = type("Stop", (), {"is_set": lambda self: False})()
        engine._last_fired = {"join": 0.0}
        engine._evaluate_step = lambda step: (
            True,
            {0: (80, 120), 1: (260, 120)},
            {
                0: [
                    {
                        "center": (80, 120),
                        "box": (40, 90, 120, 150),
                        "label": "Mob.png 0.91",
                    }
                ],
                1: [
                    {
                        "center": (260, 120),
                        "box": (240, 100, 280, 140),
                        "label": "joining 0.92",
                    }
                ],
            },
        )
        engine._read_level_for_row = lambda action, reference: None
        engine.log = lambda message: None
        engine._click_point = lambda x, y, button: clicked.append((x, y, button))
        step = Step(
            name="join",
            conditions=[
                ImageCondition(template_path="templates/Mob.png"),
                ImageCondition(template_path="templates/Join.png"),
            ],
            actions=[
                Action(
                    type="click_matching_row",
                    match_condition_index=0,
                    on_condition_index=1,
                    row_tolerance=40,
                    min_level=27,
                )
            ],
            cooldown=0.0,
            repeatable=True,
        )
        engine.scenario.steps = [step]

        engine._cycle()

        self.assertEqual(clicked, [])

    def test_matching_row_action_rechecks_conditions_after_wait_before_clicking(self):
        clicked = []
        logs = []
        evaluations = []
        engine = object.__new__(MacroEngine)
        engine.scenario = Scenario(name="war")
        engine._stop_event = type("Stop", (), {"is_set": lambda self: False})()
        engine._last_fired = {"join": 0.0}
        engine._sleep_until_stop = lambda seconds: False

        def evaluate_step(step, frame_cache=None):
            evaluations.append(1)
            if len(evaluations) == 1:
                return (
                    True,
                    {0: (80, 120), 1: (260, 120)},
                    {
                        0: [
                            {
                                "center": (80, 120),
                                "box": (40, 90, 120, 150),
                                "label": "GoldMob.png 0.91",
                            }
                        ],
                        1: [
                            {
                                "center": (260, 120),
                                "box": (240, 100, 280, 140),
                                "label": "Join.png 0.92",
                            }
                        ],
                    },
                )
            return False, {}, {}

        engine._evaluate_step = evaluate_step
        engine._evaluate_uses_frame_cache = True
        engine.log = logs.append
        engine._click_point = lambda x, y, button: clicked.append((x, y, button))
        step = Step(
            name="join",
            conditions=[
                ImageCondition(template_path="templates/GoldMob.png"),
                ImageCondition(template_path="templates/Join.png"),
            ],
            actions=[
                Action(type="wait", seconds=1.2),
                Action(
                    type="click_matching_row",
                    match_condition_index=0,
                    on_condition_index=1,
                    row_tolerance=40,
                ),
                Action(type="click", x=10, y=20),
            ],
            cooldown=10.0,
            repeatable=True,
        )
        engine.scenario.steps = [step]

        engine._cycle()

        self.assertEqual(clicked, [])
        self.assertEqual(len(evaluations), 2)
        self.assertTrue(
            any("conditions changed before row click" in message for message in logs)
        )
        self.assertEqual(engine._last_fired["join"], 0.0)

    def test_conditions_changed_during_delay_abort_remaining_actions_without_cooldown(
        self,
    ):
        clicked = []
        engine = object.__new__(MacroEngine)
        engine.scenario = Scenario(name="war")
        engine._stop_event = type("Stop", (), {"is_set": lambda self: False})()
        engine._last_fired = {"Joining": 0.0}
        engine._evaluate_uses_frame_cache = False
        reference = {"center": (80, 120), "box": (40, 90, 120, 150)}
        target = {"center": (260, 120), "box": (240, 100, 280, 140)}
        matches = {0: [reference], 1: [target]}
        engine._evaluate_step = lambda step: (
            True,
            {0: reference["center"], 1: target["center"]},
            matches,
        )
        refreshed = iter(
            (({0: reference["center"], 1: target["center"]}, matches), None)
        )
        engine._refresh_click_matching_row_matches = lambda step, action: next(
            refreshed
        )
        engine._read_level_for_row = lambda action, row: 45
        engine._record_matching_row_diagnostic = lambda *args, **kwargs: None
        engine._sleep_until_stop = lambda seconds: False
        engine._click_point = lambda x, y, button: clicked.append((x, y, button))
        engine.log = lambda _message: None
        step = Step(
            name="Joining",
            conditions=[ImageCondition(template_path="mob.png")],
            actions=[
                Action(
                    type="click_matching_row",
                    match_condition_index=0,
                    on_condition_index=1,
                    max_level=60,
                    pre_click_delay=1.5,
                ),
                Action(type="click", x=10, y=20),
            ],
            cooldown=10.0,
        )
        engine.scenario.steps = [step]

        engine._cycle()

        self.assertEqual(clicked, [])
        self.assertEqual(engine._last_fired["Joining"], 0.0)

    def test_row_changed_during_delay_aborts_remaining_actions_without_cooldown(self):
        clicked = []
        engine = object.__new__(MacroEngine)
        engine.scenario = Scenario(name="war")
        engine._stop_event = type("Stop", (), {"is_set": lambda self: False})()
        engine._last_fired = {"Joining": 0.0}
        engine._evaluate_uses_frame_cache = False
        reference = {"center": (80, 120), "box": (40, 90, 120, 150)}
        target = {"center": (260, 120), "box": (240, 100, 280, 140)}
        matches = {0: [reference], 1: [target]}
        refreshed = ({0: reference["center"], 1: target["center"]}, matches)
        engine._evaluate_step = lambda step: (True, refreshed[0], matches)
        engine._refresh_click_matching_row_matches = lambda step, action: refreshed
        engine._read_level_for_row = lambda action, row: 45
        engine._revalidate_row_selections = lambda action, selections, new_matches: []
        engine._record_matching_row_diagnostic = lambda *args, **kwargs: None
        engine._sleep_until_stop = lambda seconds: False
        engine._click_point = lambda x, y, button: clicked.append((x, y, button))
        engine.log = lambda _message: None
        step = Step(
            name="Joining",
            conditions=[ImageCondition(template_path="mob.png")],
            actions=[
                Action(
                    type="click_matching_row",
                    match_condition_index=0,
                    on_condition_index=1,
                    max_level=60,
                    pre_click_delay=1.5,
                ),
                Action(type="click", x=10, y=20),
            ],
            cooldown=10.0,
        )
        engine.scenario.steps = [step]

        engine._cycle()

        self.assertEqual(clicked, [])
        self.assertEqual(engine._last_fired["Joining"], 0.0)

    def test_matching_row_action_does_not_read_level_for_rows_without_targets(self):
        clicked = []
        level_reads = []
        engine = object.__new__(MacroEngine)
        engine.scenario = Scenario(name="war")
        engine._stop_event = type("Stop", (), {"is_set": lambda self: False})()
        engine._last_fired = {"join": 0.0}
        engine._evaluate_step = lambda step: (
            True,
            {0: (80, 120), 1: (260, 320)},
            {
                0: [
                    {
                        "center": (80, 120),
                        "box": (40, 90, 120, 150),
                        "label": "Mob.png 0.91",
                    },
                    {
                        "center": (80, 320),
                        "box": (40, 290, 120, 350),
                        "label": "Mob.png 0.90",
                    },
                ],
                1: [
                    {
                        "center": (260, 320),
                        "box": (240, 300, 280, 340),
                        "label": "joining 0.92",
                    }
                ],
            },
        )

        def read_level(action, reference):
            level_reads.append(reference["center"])
            return 30

        engine._read_level_for_row = read_level
        engine.log = lambda message: None
        engine._click_point = lambda x, y, button: clicked.append((x, y, button))
        step = Step(
            name="join",
            conditions=[
                ImageCondition(template_path="templates/Mob.png"),
                ImageCondition(template_path="templates/Join.png"),
            ],
            actions=[
                Action(
                    type="click_matching_row",
                    match_condition_index=0,
                    on_condition_index=1,
                    row_tolerance=40,
                    max_level=60,
                )
            ],
            cooldown=0.0,
            repeatable=True,
        )
        engine.scenario.steps = [step]

        engine._cycle()

        self.assertEqual(level_reads, [(80, 320)])
        self.assertEqual(clicked, [(260, 320, "left")])

    def test_matching_row_action_clicks_no_match_condition_and_disables_steps(self):
        clicked = []
        waits = []
        logs = []
        engine = object.__new__(MacroEngine)
        engine.scenario = Scenario(name="war")
        engine._stop_event = type("Stop", (), {"is_set": lambda self: False})()
        engine._last_fired = {
            "Joining": 0.0,
            "Attack Confirm": 0.0,
            "Back if wrong mob": 0.0,
        }
        engine._evaluate_step = lambda step: (
            True,
            {0: (80, 120), 1: (260, 120), 2: (40, 700)},
            {
                0: [
                    {
                        "center": (80, 120),
                        "box": (40, 90, 120, 150),
                        "label": "Mob.png 0.91",
                    }
                ],
                1: [
                    {
                        "center": (260, 120),
                        "box": (240, 100, 280, 140),
                        "label": "joining 0.92",
                    }
                ],
                2: [
                    {
                        "center": (40, 700),
                        "box": (20, 680, 60, 720),
                        "label": "BackButton.png 0.95",
                    }
                ],
            },
        )
        engine._read_level_for_row = lambda action, reference: 30
        engine.log = logs.append
        engine._click_point = lambda x, y, button: clicked.append((x, y, button))
        engine._sleep_until_stop = lambda seconds: waits.append(seconds) or False
        step = Step(
            name="Joining",
            conditions=[
                ImageCondition(template_path="templates/Mob.png"),
                ImageCondition(template_path="templates/Join.png"),
                ImageCondition(template_path="templates/BackButton.png"),
            ],
            actions=[
                Action(
                    type="click_matching_row",
                    match_condition_index=0,
                    on_condition_index=1,
                    row_tolerance=40,
                    max_level=25,
                    no_match_condition_index=2,
                    no_match_disable_steps=[
                        "Joining",
                        "Attack Confirm",
                        "Back if wrong mob",
                    ],
                ),
                Action(type="wait", seconds=1.2),
            ],
            cooldown=0.0,
            repeatable=True,
        )
        attack = Step(name="Attack Confirm", enabled=True)
        back = Step(name="Back if wrong mob", enabled=True)
        engine.scenario.steps = [step, attack, back]

        engine._cycle()

        self.assertEqual(clicked, [(40, 700, "left")])
        self.assertFalse(step.enabled)
        self.assertFalse(attack.enabled)
        self.assertFalse(back.enabled)
        self.assertEqual(waits, [])
        self.assertTrue(
            any("no valid matching row target" in message for message in logs)
        )

    def test_failed_no_match_fallback_retries_without_wait_or_state_advance(self):
        waits = []
        engine = object.__new__(MacroEngine)
        engine.scenario = Scenario(name="war")
        engine._stop_event = type("Stop", (), {"is_set": lambda self: False})()
        engine._last_fired = {"Joining": 0.0, "Attack Confirm": 0.0}
        engine._evaluate_step = lambda step: (
            True,
            {0: (80, 120), 1: (260, 120), 2: (40, 700)},
            {
                0: [{"center": (80, 120), "box": (40, 90, 120, 150)}],
                1: [{"center": (260, 120), "box": (240, 100, 280, 140)}],
                2: [{"center": (40, 700), "box": (20, 680, 60, 720)}],
            },
        )
        engine._read_level_for_row = lambda action, reference: 30
        engine._click_point = lambda x, y, button: False
        engine._sleep_until_stop = lambda seconds: waits.append(seconds) or False
        engine.log = lambda message: None
        step = Step(
            name="Joining",
            conditions=[
                ImageCondition(template_path="templates/Mob.png"),
                ImageCondition(template_path="templates/Join.png"),
                ImageCondition(template_path="templates/BackButton.png"),
            ],
            actions=[
                Action(
                    type="click_matching_row",
                    match_condition_index=0,
                    on_condition_index=1,
                    max_level=25,
                    no_match_condition_index=2,
                    no_match_disable_steps=["Joining", "Attack Confirm"],
                ),
                Action(type="wait", seconds=1.2),
            ],
            cooldown=0.0,
            repeatable=True,
        )
        attack = Step(name="Attack Confirm", enabled=True)
        engine.scenario.steps = [step, attack]

        engine._cycle()

        self.assertEqual(waits, [])
        self.assertTrue(step.enabled)
        self.assertTrue(attack.enabled)
        self.assertEqual(engine._last_fired["Joining"], 0.0)

    def test_unreadable_level_retries_without_running_no_match_fallback(self):
        clicked = []
        logs = []
        engine = object.__new__(MacroEngine)
        engine.scenario = Scenario(name="war")
        engine._stop_event = type("Stop", (), {"is_set": lambda self: False})()
        engine._last_fired = {"Joining": 0.0, "Attack Confirm": 0.0}
        engine._evaluate_step = lambda step: (
            True,
            {0: (80, 120), 1: (260, 120), 2: (40, 700)},
            {
                0: [{"center": (80, 120), "box": (40, 90, 120, 150)}],
                1: [{"center": (260, 120), "box": (240, 100, 280, 140)}],
                2: [{"center": (40, 700), "box": (20, 680, 60, 720)}],
            },
        )
        engine._read_level_for_row = lambda action, reference: None
        engine.log = logs.append
        engine._click_point = lambda x, y, button: clicked.append((x, y, button))
        step = Step(
            name="Joining",
            conditions=[
                ImageCondition(template_path="templates/Mob.png"),
                ImageCondition(template_path="templates/Join.png"),
                ImageCondition(template_path="templates/BackButton.png"),
            ],
            actions=[
                Action(
                    type="click_matching_row",
                    match_condition_index=0,
                    on_condition_index=1,
                    max_level=60,
                    no_match_condition_index=2,
                    no_match_disable_steps=["Joining", "Attack Confirm"],
                ),
                Action(
                    type="set_step",
                    step_name="Attack Confirm",
                    set_enabled=False,
                ),
            ],
            cooldown=10.0,
        )
        attack = Step(name="Attack Confirm", enabled=True)
        engine.scenario.steps = [step, attack]

        engine._cycle()

        self.assertEqual(clicked, [])
        self.assertTrue(step.enabled)
        self.assertTrue(attack.enabled)
        self.assertEqual(engine._last_fired["Joining"], 0.0)
        self.assertTrue(any("level unreadable" in message for message in logs))
        self.assertFalse(any("[no-match]" in message for message in logs))

    def test_pre_click_delay_runs_after_level_check_and_revalidates_before_click(self):
        events = []
        engine = object.__new__(MacroEngine)
        engine.scenario = Scenario(name="war")
        engine._stop_event = type("Stop", (), {"is_set": lambda self: False})()
        engine._last_fired = {"Joining": 0.0}
        engine._evaluate_uses_frame_cache = False
        engine._evaluate_step = lambda step: (
            True,
            {0: (80, 120), 1: (260, 120)},
            {
                0: [{"center": (80, 120), "box": (40, 90, 120, 150)}],
                1: [{"center": (260, 120), "box": (240, 100, 280, 140)}],
            },
        )

        def read_level(_action, _reference):
            events.append("level")
            return 45

        engine._read_level_for_row = read_level
        engine._sleep_until_stop = lambda seconds: (
            events.append(("wait", seconds)) or False
        )
        engine._click_point = lambda x, y, button: events.append(
            ("click", x, y, button)
        )
        engine.log = lambda message: None
        step = Step(
            name="Joining",
            conditions=[
                ImageCondition(template_path="templates/Mob.png"),
                ImageCondition(template_path="templates/Join.png"),
            ],
            actions=[
                Action(
                    type="click_matching_row",
                    match_condition_index=0,
                    on_condition_index=1,
                    max_level=60,
                    pre_click_delay=1.5,
                )
            ],
            cooldown=0.0,
        )
        engine.scenario.steps = [step]

        engine._cycle()

        self.assertEqual(events[0], "level")
        self.assertEqual(events[1][0], "wait")
        self.assertAlmostEqual(events[1][1], 1.5, places=3)
        self.assertEqual(events[2:], ["level", ("click", 260, 120, "left")])

    def test_pre_click_delay_subtracts_diagnostic_work_even_when_event_is_deduplicated(
        self,
    ):
        waits = []
        engine = object.__new__(MacroEngine)
        engine._stop_event = type("Stop", (), {"is_set": lambda self: False})()
        reference = {"center": (80, 120), "box": (40, 90, 120, 150)}
        target = {"center": (260, 120), "box": (240, 100, 280, 140)}
        refreshed = (
            {0: reference["center"], 1: target["center"]},
            {0: [reference], 1: [target]},
        )
        engine._refresh_click_matching_row_matches = lambda step, action: refreshed
        engine._read_level_for_row = lambda action, row: 45
        engine._record_matching_row_diagnostic = lambda *args, **kwargs: None
        engine._sleep_until_stop = lambda seconds: waits.append(seconds) or False
        engine._click_point = lambda x, y, button: True
        engine.log = lambda message: None
        action = Action(
            type="click_matching_row",
            match_condition_index=0,
            on_condition_index=1,
            max_level=60,
            pre_click_delay=1.5,
        )
        step = Step(name="Joining", actions=[action])

        with patch(
            "macro_clicker.engine.time.monotonic",
            side_effect=(10.0, 10.4),
        ):
            engine._run_action(step, action, {}, {})

        self.assertEqual(len(waits), 1)
        self.assertAlmostEqual(waits[0], 1.1)

    def test_zero_pre_click_delay_records_success_evidence_after_click(self):
        events = []
        engine = object.__new__(MacroEngine)
        engine._stop_event = type("Stop", (), {"is_set": lambda self: False})()
        reference = {"center": (80, 120), "box": (40, 90, 120, 150)}
        target = {"center": (260, 120), "box": (240, 100, 280, 140)}
        matches = {0: [reference], 1: [target]}
        engine._refresh_click_matching_row_matches = lambda step, action: (
            {0: reference["center"], 1: target["center"]},
            matches,
        )
        engine._read_level_for_row = lambda action, row: 45
        engine._click_point = lambda x, y, button: events.append("click") or True
        engine._record_matching_row_diagnostic = lambda *args, **kwargs: events.append(
            "diagnostic"
        )
        engine.log = lambda message: None
        action = Action(
            type="click_matching_row",
            match_condition_index=0,
            on_condition_index=1,
            max_level=60,
            pre_click_delay=0.0,
        )

        self.assertTrue(engine._run_action(Step(name="Joining"), action, {}, {}))
        self.assertEqual(events, ["click", "diagnostic"])

    def test_failed_matching_row_click_requests_retry(self):
        engine = object.__new__(MacroEngine)
        engine._stop_event = type("Stop", (), {"is_set": lambda self: False})()
        reference = {"center": (80, 120), "box": (40, 90, 120, 150)}
        target = {"center": (260, 120), "box": (240, 100, 280, 140)}
        matches = {0: [reference], 1: [target]}
        engine._refresh_click_matching_row_matches = lambda step, action: (
            {0: reference["center"], 1: target["center"]},
            matches,
        )
        engine._read_level_for_row = lambda action, row: 45
        engine._click_point = lambda x, y, button: False
        engine._record_matching_row_diagnostic = lambda *args, **kwargs: None
        engine.log = lambda message: None
        engine._retry_current_step = False
        action = Action(
            type="click_matching_row",
            match_condition_index=0,
            on_condition_index=1,
            max_level=60,
        )

        self.assertFalse(engine._run_action(Step(name="Joining"), action, {}, {}))
        self.assertTrue(engine._retry_current_step)

    def test_level_filter_logs_unread_as_not_compared_to_limits(self):
        logs = []
        engine = object.__new__(MacroEngine)
        engine.log = logs.append
        engine._read_level_for_row = lambda action, reference: None
        action = Action(type="click_matching_row", max_level=25)

        allowed = engine._row_level_allowed(action, {"center": (80, 120)})

        self.assertFalse(allowed)
        self.assertTrue(
            any("unread" in message and "max 25" in message for message in logs)
        )

    def test_level_filter_logs_read_level_decision(self):
        logs = []
        engine = object.__new__(MacroEngine)
        engine.log = logs.append
        engine._read_level_for_row = lambda action, reference: 30
        action = Action(type="click_matching_row", max_level=25)

        allowed = engine._row_level_allowed(action, {"center": (80, 120)})

        self.assertFalse(allowed)
        self.assertTrue(any("read 30" in message for message in logs))
        self.assertTrue(any("30 > max 25" in message for message in logs))

    def test_failed_level_read_records_ocr_attempts_in_diagnostics(self):
        logs = []
        submitted = []
        engine = object.__new__(MacroEngine)
        engine.scenario = Scenario(name="war", target_window_title="Game")
        engine.log = logs.append
        engine._level_ocr_reader = type(
            "Reader",
            (),
            {"read_level": lambda self, frame: LevelOcrResult(None, engine="fakeocr")},
        )()
        engine._get_target_window_rect = lambda: (100, 200, 500, 400)
        engine._grab = lambda region: (
            np.zeros((region[3], region[2], 3), dtype=np.uint8),
            region[0],
            region[1],
        )
        engine._submit_rally_diagnostic = lambda *args, **kwargs: submitted.append(
            (args, kwargs)
        )
        action = Action(
            type="click_matching_row",
            min_level=25,
            level_roi=[10, 20, 30, 40],
        )

        level = engine._read_level_for_row(action, {"center": (150, 260)})

        self.assertIsNone(level)
        self.assertTrue(
            any(
                "unread from crop rect=(160, 280, 30, 40)" in message
                for message in logs
            )
        )
        self.assertEqual(len(submitted), 1)
        level_read = submitted[0][0][1]["level_read"]
        self.assertEqual(level_read["decision"], "unread")
        self.assertEqual(len(level_read["attempts"]), 6)
        self.assertEqual(
            set(level_read["attempts"][0]),
            {"index", "base_offset", "rect", "status", "ocr"},
        )

    def test_level_read_retries_shifted_crop_when_first_crop_misses_text(self):
        logs = []
        grabbed = []
        reads = []
        engine = object.__new__(MacroEngine)
        engine.scenario = Scenario(name="war")
        engine.log = logs.append
        engine._get_target_window_rect = lambda: None

        def grab(region):
            grabbed.append(region)
            return (
                np.zeros((region[3], region[2], 3), dtype=np.uint8),
                region[0],
                region[1],
            )

        class Reader:
            def read_level(self, frame):
                reads.append(frame.shape)
                if len(reads) == 1:
                    return LevelOcrResult(None, engine="fakeocr")
                return LevelOcrResult(
                    50, text="Lv.50", confidence=0.98, engine="fakeocr"
                )

        engine._grab = grab
        engine._level_ocr_reader = Reader()
        action = Action(
            type="click_matching_row", max_level=60, level_roi=[10, 20, 30, 40]
        )

        level = engine._read_level_for_row(action, {"center": (100, 100)})

        self.assertEqual(level, 50)
        self.assertEqual(grabbed, [(110, 104, 30, 80)])
        self.assertEqual(reads, [(40, 30, 3), (40, 30, 3)])
        self.assertEqual(next(iter(engine._level_offset_cache.values())), 8)
        self.assertTrue(
            any("recovered with alternate crop" in message for message in logs)
        )

    def test_level_crop_offset_cache_is_relative_to_each_detected_row(self):
        engine = object.__new__(MacroEngine)
        engine._level_offset_cache = {}
        engine._grab = lambda region: (
            np.zeros((region[3], region[2], 3), dtype=np.uint8),
            region[0],
            region[1],
        )
        action = Action(
            type="click_matching_row",
            max_level=60,
            level_roi=[10, 20, 30, 40],
        )
        one_row_reference = {"center": (100, 100)}
        third_row_reference = {"center": (100, 500)}

        engine._remember_level_crop_offset(
            action,
            one_row_reference,
            None,
            8,
        )
        candidates = engine._capture_level_crop_candidates(
            action,
            third_row_reference,
            None,
        )

        preferred_offset, preferred_rect, _frame = candidates[0]
        self.assertEqual(preferred_offset, 8)
        self.assertEqual(preferred_rect, (110, 528, 30, 40))
        self.assertEqual(len(candidates), 6)

    def test_level_read_accepts_strong_ocr(self):
        logs = []
        engine = object.__new__(MacroEngine)
        engine.scenario = Scenario(name="war")
        engine.log = logs.append
        engine._get_target_window_rect = lambda: None
        engine._grab = lambda region: (
            np.zeros((region[3], region[2], 3), dtype=np.uint8),
            region[0],
            region[1],
        )
        engine._level_ocr_reader = type(
            "Reader",
            (),
            {
                "read_level": lambda self, frame: LevelOcrResult(
                    22, text="Lv.22", confidence=0.94, engine="fakeocr"
                )
            },
        )()
        action = Action(
            type="click_matching_row", max_level=25, level_roi=[10, 20, 30, 40]
        )

        level = engine._read_level_for_row(action, {"center": (100, 100)})

        self.assertEqual(level, 22)
        self.assertTrue(any("fakeocr read 22" in message for message in logs))

    def test_provisional_ocr_checks_alternate_crop_and_prefers_strong_result(self):
        logs = []
        reads = []
        engine = object.__new__(MacroEngine)
        engine.scenario = Scenario(name="war")
        engine.log = logs.append
        engine._get_target_window_rect = lambda: None
        engine._grab = lambda region: (
            np.zeros((region[3], region[2], 3), dtype=np.uint8),
            region[0],
            region[1],
        )

        class Reader:
            def read_level(self, frame):
                reads.append(frame.shape)
                if len(reads) == 1:
                    return LevelOcrResult(
                        1,
                        text="Lv.1",
                        confidence=0.81,
                        engine="fakeocr",
                    )
                return LevelOcrResult(
                    1,
                    text="Lv.1",
                    confidence=0.98,
                    engine="fakeocr",
                )

        engine._level_ocr_reader = Reader()
        action = Action(
            type="click_matching_row",
            max_level=60,
            level_roi=[10, 20, 30, 40],
        )

        level = engine._read_level_for_row(action, {"center": (100, 100)})

        self.assertEqual(level, 1)
        self.assertEqual(len(reads), 2)
        self.assertEqual(next(iter(engine._level_offset_cache.values())), 8)
        self.assertTrue(any("provisional OCR level 1" in message for message in logs))

    def test_repeated_provisional_ocr_level_is_accepted_after_all_crops(self):
        logs = []
        reads = []
        engine = object.__new__(MacroEngine)
        engine.scenario = Scenario(name="war")
        engine.log = logs.append
        engine._get_target_window_rect = lambda: None
        engine._grab = lambda region: (
            np.zeros((region[3], region[2], 3), dtype=np.uint8),
            region[0],
            region[1],
        )

        class Reader:
            def read_level(self, frame):
                reads.append(frame.shape)
                return LevelOcrResult(
                    23,
                    text="Lv.23",
                    confidence=0.82,
                    engine="fakeocr",
                )

        engine._level_ocr_reader = Reader()
        action = Action(
            type="click_matching_row",
            max_level=60,
            level_roi=[10, 20, 30, 40],
        )

        level = engine._read_level_for_row(action, {"center": (100, 100)})

        self.assertEqual(level, 23)
        self.assertEqual(len(reads), 6)
        self.assertTrue(
            any(
                "accepted provisional level 23 from 6 crop(s)" in message
                for message in logs
            )
        )

    def test_single_provisional_ocr_result_is_not_consensus(self):
        logs = []
        results = iter((45, None, None, None, None, None))
        engine = object.__new__(MacroEngine)
        engine.scenario = Scenario(name="war")
        engine.log = logs.append
        engine._get_target_window_rect = lambda: None
        engine._grab = lambda region: (
            np.zeros((region[3], region[2], 3), dtype=np.uint8),
            region[0],
            region[1],
        )

        class Reader:
            def read_level(self, frame):
                level = next(results)
                if level is None:
                    return LevelOcrResult(None, engine="fakeocr")
                return LevelOcrResult(
                    level,
                    text=f"Lv.{level}",
                    confidence=0.82,
                    engine="fakeocr",
                )

        engine._level_ocr_reader = Reader()
        action = Action(
            type="click_matching_row",
            max_level=60,
            level_roi=[10, 20, 30, 40],
        )

        level = engine._read_level_for_row(action, {"center": (100, 100)})

        self.assertIsNone(level)
        self.assertTrue(
            any("only one provisional OCR crop" in message for message in logs)
        )

    def test_tied_provisional_ocr_levels_are_rejected(self):
        logs = []
        results = iter((10, 20, 10, 20, None, None))
        engine = object.__new__(MacroEngine)
        engine.scenario = Scenario(name="war")
        engine.log = logs.append
        engine._get_target_window_rect = lambda: None
        engine._grab = lambda region: (
            np.zeros((region[3], region[2], 3), dtype=np.uint8),
            region[0],
            region[1],
        )

        class Reader:
            def read_level(self, frame):
                level = next(results)
                if level is None:
                    return LevelOcrResult(None, engine="fakeocr")
                return LevelOcrResult(
                    level,
                    text=f"Lv.{level}",
                    confidence=0.82,
                    engine="fakeocr",
                )

        engine._level_ocr_reader = Reader()
        action = Action(
            type="click_matching_row",
            max_level=60,
            level_roi=[10, 20, 30, 40],
        )

        level = engine._read_level_for_row(action, {"center": (100, 100)})

        self.assertIsNone(level)
        self.assertTrue(
            any("conflicting provisional OCR levels" in message for message in logs)
        )

    def test_repeated_low_confidence_ocr_is_accepted_by_consensus(self):
        logs = []
        engine = object.__new__(MacroEngine)
        engine.scenario = Scenario(name="war")
        engine.log = logs.append
        engine._get_target_window_rect = lambda: None
        engine._grab = lambda region: (
            np.zeros((region[3], region[2], 3), dtype=np.uint8),
            region[0],
            region[1],
        )
        engine._level_ocr_reader = type(
            "Reader",
            (),
            {
                "read_level": lambda self, frame: LevelOcrResult(
                    22, text="Lv.22", confidence=0.70, engine="fakeocr"
                )
            },
        )()
        action = Action(
            type="click_matching_row", max_level=25, level_roi=[10, 20, 30, 40]
        )

        level = engine._read_level_for_row(action, {"center": (100, 100)})

        self.assertEqual(level, 22)
        self.assertTrue(
            any("accepted provisional level 22" in message for message in logs)
        )

    def test_level_read_accepts_clear_lv_ocr(self):
        logs = []
        engine = object.__new__(MacroEngine)
        engine.scenario = Scenario(name="war")
        engine.log = logs.append
        engine._get_target_window_rect = lambda: None
        engine._grab = lambda region: (
            np.zeros((region[3], region[2], 3), dtype=np.uint8),
            region[0],
            region[1],
        )
        engine._level_ocr_reader = type(
            "Reader",
            (),
            {
                "read_level": lambda self, frame: LevelOcrResult(
                    30, text="Lv.30", confidence=0.94, engine="fakeocr"
                )
            },
        )()
        action = Action(
            type="click_matching_row", max_level=55, level_roi=[10, 20, 30, 40]
        )

        level = engine._read_level_for_row(action, {"center": (100, 100)})

        self.assertEqual(level, 30)
        self.assertTrue(any("fakeocr read 30" in message for message in logs))

    def test_level_read_accepts_clear_lv_ocr_rounded_to_ninety_confidence(self):
        logs = []
        engine = object.__new__(MacroEngine)
        engine.scenario = Scenario(name="war")
        engine.log = logs.append
        engine._get_target_window_rect = lambda: None
        engine._grab = lambda region: (
            np.zeros((region[3], region[2], 3), dtype=np.uint8),
            region[0],
            region[1],
        )
        engine._level_ocr_reader = type(
            "Reader",
            (),
            {
                "read_level": lambda self, frame: LevelOcrResult(
                    15, text="Lv.15", confidence=0.895, engine="fakeocr"
                )
            },
        )()
        action = Action(
            type="click_matching_row", max_level=55, level_roi=[10, 20, 30, 40]
        )

        level = engine._read_level_for_row(action, {"center": (100, 100)})

        self.assertEqual(level, 15)
        self.assertTrue(
            any("accepted provisional level 15" in message for message in logs)
        )

    def test_level_read_accepts_single_digit_ocr(self):
        engine = object.__new__(MacroEngine)
        engine.scenario = Scenario(name="war")
        engine.log = lambda _message: None
        engine._level_ocr_reader = type(
            "Reader",
            (),
            {
                "read_level": lambda self, frame: LevelOcrResult(
                    1,
                    text="Lv.1",
                    confidence=0.98,
                    engine="fakeocr",
                )
            },
        )()
        engine._get_target_window_rect = lambda: None
        engine._grab = lambda region: (
            np.zeros((region[3], region[2], 3), dtype=np.uint8),
            region[0],
            region[1],
        )
        action = Action(type="click_matching_row", max_level=25)

        level = engine._read_level_for_row(action, {"center": (100, 100)})

        self.assertEqual(level, 1)

    def test_level_read_accepts_single_digit_provisional_consensus(self):
        logs = []
        engine = object.__new__(MacroEngine)
        engine.scenario = Scenario(name="war")
        engine.log = logs.append
        engine._get_target_window_rect = lambda: None
        engine._grab = lambda region: (
            np.zeros((region[3], region[2], 3), dtype=np.uint8),
            region[0],
            region[1],
        )
        engine._level_ocr_reader = type(
            "Reader",
            (),
            {
                "read_level": lambda self, frame: LevelOcrResult(
                    7, text="Lv.7", confidence=0.78, engine="fakeocr"
                )
            },
        )()
        action = Action(
            type="click_matching_row", max_level=25, level_roi=[10, 20, 30, 40]
        )

        level = engine._read_level_for_row(action, {"center": (100, 100)})

        self.assertEqual(level, 7)
        self.assertTrue(
            any("accepted provisional level 7" in message for message in logs)
        )

    def test_level_read_accepts_single_digit_ocr_without_special_configuration(self):
        logs = []
        engine = object.__new__(MacroEngine)
        engine.scenario = Scenario(name="war")
        engine.log = logs.append
        engine._get_target_window_rect = lambda: None
        engine._grab = lambda region: (
            np.zeros((region[3], region[2], 3), dtype=np.uint8),
            region[0],
            region[1],
        )
        engine._level_ocr_reader = type(
            "Reader",
            (),
            {
                "read_level": lambda self, frame: LevelOcrResult(
                    9, text="Lv.9", confidence=0.99, engine="fakeocr"
                )
            },
        )()
        action = Action(
            type="click_matching_row", max_level=25, level_roi=[10, 20, 30, 40]
        )

        level = engine._read_level_for_row(action, {"center": (100, 100)})

        self.assertEqual(level, 9)

    def test_level_read_accepts_repeated_low_confidence_prefixed_ocr(self):
        logs = []
        engine = object.__new__(MacroEngine)
        engine.scenario = Scenario(name="war")
        engine.log = logs.append
        engine._get_target_window_rect = lambda: None
        engine._grab = lambda region: (
            np.zeros((region[3], region[2], 3), dtype=np.uint8),
            region[0],
            region[1],
        )
        engine._level_ocr_reader = type(
            "Reader",
            (),
            {
                "read_level": lambda self, frame: LevelOcrResult(
                    50, text="L.500", confidence=0.73, engine="fakeocr"
                )
            },
        )()
        action = Action(
            type="click_matching_row", max_level=50, level_roi=[10, 20, 30, 40]
        )

        level = engine._read_level_for_row(action, {"center": (100, 100)})

        self.assertEqual(level, 50)
        self.assertTrue(
            any("accepted provisional level 50" in message for message in logs)
        )

    def test_level_read_accepts_repeated_confident_ocr(self):
        logs = []
        engine = object.__new__(MacroEngine)
        engine.scenario = Scenario(name="war")
        engine.log = logs.append
        engine._get_target_window_rect = lambda: None
        engine._grab = lambda region: (
            np.zeros((region[3], region[2], 3), dtype=np.uint8),
            region[0],
            region[1],
        )
        engine._level_ocr_reader = type(
            "Reader",
            (),
            {
                "read_level": lambda self, frame: LevelOcrResult(
                    10, text="LV.10", confidence=0.81, engine="fakeocr"
                )
            },
        )()
        action = Action(
            type="click_matching_row", max_level=55, level_roi=[10, 20, 30, 40]
        )

        level = engine._read_level_for_row(action, {"center": (100, 100)})

        self.assertEqual(level, 10)
        self.assertTrue(
            any("accepted provisional level 10" in message for message in logs)
        )

    def test_level_read_accepts_repeated_ly_prefix_ocr(self):
        logs = []
        engine = object.__new__(MacroEngine)
        engine.scenario = Scenario(name="war")
        engine.log = logs.append
        engine._get_target_window_rect = lambda: None
        engine._grab = lambda region: (
            np.zeros((region[3], region[2], 3), dtype=np.uint8),
            region[0],
            region[1],
        )
        engine._level_ocr_reader = type(
            "Reader",
            (),
            {
                "read_level": lambda self, frame: LevelOcrResult(
                    15, text="Ly-15", confidence=0.77, engine="fakeocr"
                )
            },
        )()
        action = Action(
            type="click_matching_row", max_level=60, level_roi=[10, 20, 30, 40]
        )

        level = engine._read_level_for_row(action, {"center": (100, 100)})

        self.assertEqual(level, 15)
        self.assertTrue(
            any("accepted provisional level 15" in message for message in logs)
        )

    def test_level_read_accepts_high_confidence_ocr(self):
        logs = []
        engine = object.__new__(MacroEngine)
        engine.scenario = Scenario(name="war")
        engine.log = logs.append
        engine._get_target_window_rect = lambda: None
        engine._grab = lambda region: (
            np.zeros((region[3], region[2], 3), dtype=np.uint8),
            region[0],
            region[1],
        )
        engine._level_ocr_reader = type(
            "Reader",
            (),
            {
                "read_level": lambda self, frame: LevelOcrResult(
                    55, text="Lv.55", confidence=0.99, engine="fakeocr"
                )
            },
        )()
        action = Action(
            type="click_matching_row", max_level=55, level_roi=[10, 20, 30, 40]
        )

        level = engine._read_level_for_row(action, {"center": (100, 100)})

        self.assertEqual(level, 55)
        self.assertTrue(any("fakeocr read 55" in message for message in logs))


if __name__ == "__main__":
    unittest.main()
