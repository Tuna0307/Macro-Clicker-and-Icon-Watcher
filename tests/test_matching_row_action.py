import os
import unittest

import cv2
import numpy as np

from engine import MacroEngine
from level_ocr import LevelOcrResult
from models import Action, ImageCondition, Scenario, Step


class MatchingRowActionTests(unittest.TestCase):
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
                    {"center": (80, 120), "box": (40, 90, 120, 150), "label": "Mob.png 0.91"},
                    {"center": (80, 320), "box": (40, 290, 120, 350), "label": "Mob.png 0.90"},
                ],
                1: [
                    {"center": (300, 320), "box": (260, 290, 340, 350), "label": "joining 0.95"},
                    {"center": (300, 120), "box": (260, 90, 340, 150), "label": "joining 0.93"},
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
            cooldown=0.0,
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
                    {"center": (80, 120), "box": (40, 90, 120, 150), "label": "Mob.png 0.91"},
                    {"center": (80, 320), "box": (40, 290, 120, 350), "label": "Mob.png 0.90"},
                ],
                1: [
                    {"center": (180, 120), "box": (160, 100, 200, 140), "label": "joining 0.91"},
                    {"center": (260, 120), "box": (240, 100, 280, 140), "label": "joining 0.92"},
                    {"center": (340, 120), "box": (320, 100, 360, 140), "label": "joining 0.93"},
                    {"center": (180, 320), "box": (160, 300, 200, 340), "label": "joining 0.94"},
                    {"center": (300, 320), "box": (280, 300, 320, 340), "label": "joining 0.95"},
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
                    {"center": (80, 120), "box": (40, 90, 120, 150), "label": "Mob.png 0.91"},
                    {"center": (80, 320), "box": (40, 290, 120, 350), "label": "Mob.png 0.90"},
                ],
                1: [
                    {"center": (260, 120), "box": (240, 100, 280, 140), "label": "joining 0.92"},
                    {"center": (300, 320), "box": (280, 300, 320, 340), "label": "joining 0.95"},
                ],
            },
        )
        engine._read_level_for_row = lambda action, reference: 26 if reference["center"][1] == 120 else 27
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
                0: [{"center": (80, 120), "box": (40, 90, 120, 150), "label": "Mob.png 0.91"}],
                1: [{"center": (260, 120), "box": (240, 100, 280, 140), "label": "joining 0.92"}],
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
                        0: [{"center": (80, 120), "box": (40, 90, 120, 150), "label": "GoldMob.png 0.91"}],
                        1: [{"center": (260, 120), "box": (240, 100, 280, 140), "label": "Join.png 0.92"}],
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
            ],
            cooldown=0.0,
            repeatable=True,
        )
        engine.scenario.steps = [step]

        engine._cycle()

        self.assertEqual(clicked, [])
        self.assertEqual(len(evaluations), 2)
        self.assertTrue(any("conditions changed before row click" in message for message in logs))

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
                    {"center": (80, 120), "box": (40, 90, 120, 150), "label": "Mob.png 0.91"},
                    {"center": (80, 320), "box": (40, 290, 120, 350), "label": "Mob.png 0.90"},
                ],
                1: [{"center": (260, 320), "box": (240, 300, 280, 340), "label": "joining 0.92"}],
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
        logs = []
        engine = object.__new__(MacroEngine)
        engine.scenario = Scenario(name="war")
        engine._stop_event = type("Stop", (), {"is_set": lambda self: False})()
        engine._last_fired = {"Joining": 0.0, "Attack Confirm": 0.0, "Back if wrong mob": 0.0}
        engine._evaluate_step = lambda step: (
            True,
            {0: (80, 120), 1: (260, 120), 2: (40, 700)},
            {
                0: [{"center": (80, 120), "box": (40, 90, 120, 150), "label": "Mob.png 0.91"}],
                1: [{"center": (260, 120), "box": (240, 100, 280, 140), "label": "joining 0.92"}],
                2: [{"center": (40, 700), "box": (20, 680, 60, 720), "label": "BackButton.png 0.95"}],
            },
        )
        engine._read_level_for_row = lambda action, reference: 30
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
                    row_tolerance=40,
                    max_level=25,
                    no_match_condition_index=2,
                    no_match_disable_steps=["Joining", "Attack Confirm", "Back if wrong mob"],
                )
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
        self.assertTrue(any("no valid matching row target" in message for message in logs))

    def test_level_filter_logs_unread_as_not_compared_to_limits(self):
        logs = []
        engine = object.__new__(MacroEngine)
        engine.log = logs.append
        engine._read_level_for_row = lambda action, reference: None
        action = Action(type="click_matching_row", max_level=25)

        allowed = engine._row_level_allowed(action, {"center": (80, 120)})

        self.assertFalse(allowed)
        self.assertTrue(any("unread" in message and "max 25" in message for message in logs))

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

    def test_failed_level_read_logs_and_saves_debug_crop(self):
        logs = []
        saved = []
        engine = object.__new__(MacroEngine)
        engine.scenario = Scenario(name="war", target_window_title="Game")
        engine.log = logs.append
        engine._digit_template_cache = {}
        engine._level_ocr_reader = type(
            "Reader",
            (),
            {"read_level": lambda self, frame: LevelOcrResult(None, engine="fakeocr")},
        )()
        engine._load_digit_templates = lambda folder: {"2": np.ones((5, 4), dtype=np.uint8) * 255}
        engine._get_target_window_rect = lambda: (100, 200, 500, 400)
        engine._grab = lambda region: (np.zeros((region[3], region[2], 3), dtype=np.uint8), region[0], region[1])
        engine._read_level_from_frame = lambda frame, templates, min_digits=1: None
        engine._level_read_top_scores = lambda frame, templates: [("2", 0.42)]
        engine._save_level_debug_crop = lambda frame, rect, reference: saved.append((frame.shape, rect, reference["center"]))
        action = Action(
            type="click_matching_row",
            min_level=25,
            level_digit_template_dir="templates/level_digits",
            level_roi=[10, 20, 30, 40],
        )

        level = engine._read_level_for_row(action, {"center": (150, 260)})

        self.assertIsNone(level)
        self.assertTrue(any("unread from crop rect=(160, 280, 30, 40)" in message for message in logs))
        self.assertTrue(any("top digit scores: 2=0.42" in message for message in logs))
        self.assertEqual(saved, [((40, 30, 3), (160, 280, 30, 40), (150, 260))])

    def test_level_read_retries_shifted_crop_when_first_crop_misses_text(self):
        logs = []
        grabbed = []
        saved = []
        engine = object.__new__(MacroEngine)
        engine.scenario = Scenario(name="war")
        engine.log = logs.append
        engine._get_target_window_rect = lambda: None

        def grab(region):
            grabbed.append(region)
            return np.zeros((region[3], region[2], 3), dtype=np.uint8), region[0], region[1]

        class Reader:
            def read_level(self, frame):
                if len(grabbed) == 1:
                    return LevelOcrResult(None, engine="fakeocr")
                return LevelOcrResult(50, text="Lv.50", confidence=0.98, engine="fakeocr")

        engine._grab = grab
        engine._level_ocr_reader = Reader()
        engine._load_digit_templates = lambda folder: {}
        engine._save_level_debug_crop = lambda frame, rect, reference: saved.append(rect)
        action = Action(type="click_matching_row", max_level=60, level_roi=[10, 20, 30, 40], level_min_digits=2)

        level = engine._read_level_for_row(action, {"center": (100, 100)})

        self.assertEqual(level, 50)
        self.assertEqual(grabbed[:2], [(110, 120, 30, 40), (110, 128, 30, 40)])
        self.assertEqual(saved, [])
        self.assertTrue(any("recovered with alternate crop" in message for message in logs))

    def test_level_read_uses_ocr_before_digit_templates(self):
        logs = []
        engine = object.__new__(MacroEngine)
        engine.scenario = Scenario(name="war")
        engine.log = logs.append
        engine._get_target_window_rect = lambda: None
        engine._grab = lambda region: (np.zeros((region[3], region[2], 3), dtype=np.uint8), region[0], region[1])
        engine._load_digit_templates = lambda folder: {}
        engine._level_ocr_reader = type(
            "Reader",
            (),
            {"read_level": lambda self, frame: LevelOcrResult(22, text="Lv.22", confidence=0.94, engine="fakeocr")},
        )()
        action = Action(type="click_matching_row", max_level=25, level_roi=[10, 20, 30, 40])

        level = engine._read_level_for_row(action, {"center": (100, 100)})

        self.assertEqual(level, 22)
        self.assertTrue(any("fakeocr read 22" in message for message in logs))

    def test_level_read_returns_none_when_ocr_and_fallback_conflict(self):
        logs = []
        engine = object.__new__(MacroEngine)
        engine.scenario = Scenario(name="war")
        engine.log = logs.append
        engine._get_target_window_rect = lambda: None
        engine._grab = lambda region: (np.zeros((region[3], region[2], 3), dtype=np.uint8), region[0], region[1])
        engine._load_digit_templates = lambda folder: {"3": np.ones((5, 4), dtype=np.uint8) * 255}
        engine._read_level_from_frame = lambda frame, templates, min_digits=1: 30
        engine._level_ocr_reader = type(
            "Reader",
            (),
            {"read_level": lambda self, frame: LevelOcrResult(22, text="Lv.22", confidence=0.70, engine="fakeocr")},
        )()
        engine._save_level_debug_crop = lambda frame, rect, reference: "logs/level_debug/conflict.png"
        action = Action(type="click_matching_row", max_level=25, level_roi=[10, 20, 30, 40])

        level = engine._read_level_for_row(action, {"center": (100, 100)})

        self.assertIsNone(level)
        self.assertTrue(any("OCR conflict" in message and "22" in message and "30" in message for message in logs))

    def test_level_read_accepts_clear_lv_ocr_over_wrong_digit_fallback(self):
        logs = []
        engine = object.__new__(MacroEngine)
        engine.scenario = Scenario(name="war")
        engine.log = logs.append
        engine._get_target_window_rect = lambda: None
        engine._grab = lambda region: (np.zeros((region[3], region[2], 3), dtype=np.uint8), region[0], region[1])
        engine._load_digit_templates = lambda folder: {"1": np.ones((5, 4), dtype=np.uint8) * 255}
        engine._read_level_from_frame = lambda frame, templates, min_digits=1: 11
        engine._level_ocr_reader = type(
            "Reader",
            (),
            {"read_level": lambda self, frame: LevelOcrResult(30, text="Lv.30", confidence=0.94, engine="fakeocr")},
        )()
        action = Action(type="click_matching_row", max_level=55, level_roi=[10, 20, 30, 40], level_min_digits=2)

        level = engine._read_level_for_row(action, {"center": (100, 100)})

        self.assertEqual(level, 30)
        self.assertTrue(any("ignored digit_fallback=11" in message for message in logs))

    def test_level_read_accepts_clear_lv_ocr_rounded_to_ninety_confidence(self):
        logs = []
        engine = object.__new__(MacroEngine)
        engine.scenario = Scenario(name="war")
        engine.log = logs.append
        engine._get_target_window_rect = lambda: None
        engine._grab = lambda region: (np.zeros((region[3], region[2], 3), dtype=np.uint8), region[0], region[1])
        engine._load_digit_templates = lambda folder: {"1": np.ones((5, 4), dtype=np.uint8) * 255}
        engine._read_level_from_frame = lambda frame, templates, min_digits=1: 1011
        engine._level_ocr_reader = type(
            "Reader",
            (),
            {"read_level": lambda self, frame: LevelOcrResult(15, text="Lv.15", confidence=0.895, engine="fakeocr")},
        )()
        action = Action(type="click_matching_row", max_level=55, level_roi=[10, 20, 30, 40], level_min_digits=2)

        level = engine._read_level_for_row(action, {"center": (100, 100)})

        self.assertEqual(level, 15)
        self.assertTrue(any("ignored digit_fallback=1011" in message for message in logs))

    def test_level_read_passes_action_min_digits_to_reader(self):
        calls = []
        engine = object.__new__(MacroEngine)
        engine.scenario = Scenario(name="war")
        engine.log = lambda message: None
        engine._level_ocr_reader = type(
            "Reader",
            (),
            {"read_level": lambda self, frame: LevelOcrResult(None, engine="fakeocr")},
        )()
        engine._load_digit_templates = lambda folder: {"8": np.ones((5, 4), dtype=np.uint8) * 255}
        engine._get_target_window_rect = lambda: None
        engine._grab = lambda region: (np.zeros((region[3], region[2], 3), dtype=np.uint8), region[0], region[1])

        def read_level(frame, templates, min_digits=1):
            calls.append(min_digits)
            return None

        engine._read_level_from_frame = read_level
        engine._level_read_top_scores = lambda frame, templates: []
        engine._save_level_debug_crop = lambda frame, rect, reference: None
        action = Action(type="click_matching_row", level_min_digits=2)

        engine._read_level_for_row(action, {"center": (100, 100)})

        self.assertTrue(calls)
        self.assertTrue(all(call == 2 for call in calls))

    def test_level_read_ignores_ocr_result_below_min_digits(self):
        logs = []
        engine = object.__new__(MacroEngine)
        engine.scenario = Scenario(name="war")
        engine.log = logs.append
        engine._get_target_window_rect = lambda: None
        engine._grab = lambda region: (np.zeros((region[3], region[2], 3), dtype=np.uint8), region[0], region[1])
        engine._load_digit_templates = lambda folder: {"3": np.ones((5, 4), dtype=np.uint8) * 255}
        engine._read_level_from_frame = lambda frame, templates, min_digits=1: 30
        engine._level_ocr_reader = type(
            "Reader",
            (),
            {"read_level": lambda self, frame: LevelOcrResult(0, text="LV0", confidence=0.78, engine="fakeocr")},
        )()
        action = Action(type="click_matching_row", max_level=25, level_roi=[10, 20, 30, 40], level_min_digits=2)

        level = engine._read_level_for_row(action, {"center": (100, 100)})

        self.assertEqual(level, 30)
        self.assertTrue(any("ignored" in message and "need 2 digit" in message for message in logs))

    def test_level_read_rejects_single_digit_ocr_without_fallback_when_min_digits_is_two(self):
        logs = []
        engine = object.__new__(MacroEngine)
        engine.scenario = Scenario(name="war")
        engine.log = logs.append
        engine._get_target_window_rect = lambda: None
        engine._grab = lambda region: (np.zeros((region[3], region[2], 3), dtype=np.uint8), region[0], region[1])
        engine._load_digit_templates = lambda folder: {}
        engine._level_ocr_reader = type(
            "Reader",
            (),
            {"read_level": lambda self, frame: LevelOcrResult(9, text="[CAT9]", confidence=0.79, engine="fakeocr")},
        )()
        engine._level_read_top_scores = lambda frame, templates: []
        engine._save_level_debug_crop = lambda frame, rect, reference: None
        action = Action(type="click_matching_row", max_level=25, level_roi=[10, 20, 30, 40], level_min_digits=2)

        level = engine._read_level_for_row(action, {"center": (100, 100)})

        self.assertIsNone(level)
        self.assertTrue(any("ignored" in message and "[CAT9]" in message for message in logs))

    def test_level_read_accepts_ocr_when_fallback_adds_spurious_leading_one(self):
        logs = []
        engine = object.__new__(MacroEngine)
        engine.scenario = Scenario(name="war")
        engine.log = logs.append
        engine._get_target_window_rect = lambda: None
        engine._grab = lambda region: (np.zeros((region[3], region[2], 3), dtype=np.uint8), region[0], region[1])
        engine._load_digit_templates = lambda folder: {"5": np.ones((5, 4), dtype=np.uint8) * 255}
        engine._read_level_from_frame = lambda frame, templates, min_digits=1: 150
        engine._level_ocr_reader = type(
            "Reader",
            (),
            {"read_level": lambda self, frame: LevelOcrResult(50, text="L.500", confidence=0.73, engine="fakeocr")},
        )()
        action = Action(type="click_matching_row", max_level=50, level_roi=[10, 20, 30, 40], level_min_digits=2)

        level = engine._read_level_for_row(action, {"center": (100, 100)})

        self.assertEqual(level, 50)
        self.assertTrue(any("ignored digit_fallback=150" in message for message in logs))

    def test_level_read_accepts_confident_ocr_when_fallback_contains_extra_noise_digits(self):
        logs = []
        engine = object.__new__(MacroEngine)
        engine.scenario = Scenario(name="war")
        engine.log = logs.append
        engine._get_target_window_rect = lambda: None
        engine._grab = lambda region: (np.zeros((region[3], region[2], 3), dtype=np.uint8), region[0], region[1])
        engine._load_digit_templates = lambda folder: {"1": np.ones((5, 4), dtype=np.uint8) * 255}
        engine._read_level_from_frame = lambda frame, templates, min_digits=1: 1011
        engine._level_ocr_reader = type(
            "Reader",
            (),
            {"read_level": lambda self, frame: LevelOcrResult(10, text="LV.10", confidence=0.81, engine="fakeocr")},
        )()
        action = Action(type="click_matching_row", max_level=55, level_roi=[10, 20, 30, 40], level_min_digits=2)

        level = engine._read_level_for_row(action, {"center": (100, 100)})

        self.assertEqual(level, 10)
        self.assertTrue(any("ignored digit_fallback=1011" in message for message in logs))

    def test_level_read_accepts_ly_prefix_ocr_over_repeated_one_fallback_noise(self):
        logs = []
        engine = object.__new__(MacroEngine)
        engine.scenario = Scenario(name="war")
        engine.log = logs.append
        engine._get_target_window_rect = lambda: None
        engine._grab = lambda region: (np.zeros((region[3], region[2], 3), dtype=np.uint8), region[0], region[1])
        engine._load_digit_templates = lambda folder: {"1": np.ones((5, 4), dtype=np.uint8) * 255}
        engine._read_level_from_frame = lambda frame, templates, min_digits=1: 111
        engine._level_ocr_reader = type(
            "Reader",
            (),
            {"read_level": lambda self, frame: LevelOcrResult(15, text="Ly-15", confidence=0.77, engine="fakeocr")},
        )()
        action = Action(type="click_matching_row", max_level=60, level_roi=[10, 20, 30, 40], level_min_digits=2)

        level = engine._read_level_for_row(action, {"center": (100, 100)})

        self.assertEqual(level, 15)
        self.assertTrue(any("ignored digit_fallback=111" in message for message in logs))

    def test_level_read_accepts_high_confidence_ocr_over_wrong_digit_fallback(self):
        logs = []
        engine = object.__new__(MacroEngine)
        engine.scenario = Scenario(name="war")
        engine.log = logs.append
        engine._get_target_window_rect = lambda: None
        engine._grab = lambda region: (np.zeros((region[3], region[2], 3), dtype=np.uint8), region[0], region[1])
        engine._load_digit_templates = lambda folder: {"4": np.ones((5, 4), dtype=np.uint8) * 255}
        engine._read_level_from_frame = lambda frame, templates, min_digits=1: 41
        engine._level_ocr_reader = type(
            "Reader",
            (),
            {"read_level": lambda self, frame: LevelOcrResult(55, text="Lv.55", confidence=0.99, engine="fakeocr")},
        )()
        action = Action(type="click_matching_row", max_level=55, level_roi=[10, 20, 30, 40], level_min_digits=2)

        level = engine._read_level_for_row(action, {"center": (100, 100)})

        self.assertEqual(level, 55)
        self.assertTrue(any("ignored digit_fallback=41" in message for message in logs))

    def test_read_level_from_live_crop_ignores_lv_prefix(self):
        crop_path = os.path.join(
            "logs",
            "level_debug",
            "level_20260624-000747_-904_346_150x45_row-839_321.png",
        )
        if not os.path.exists(crop_path):
            self.skipTest(f"missing live level crop fixture: {crop_path}")
        engine = object.__new__(MacroEngine)
        crop = cv2.imread(crop_path)
        digit_templates = engine._load_digit_templates("templates/level_digits")

        level = engine._read_level_from_frame(crop, digit_templates, min_digits=2)

        self.assertEqual(level, 22)


if __name__ == "__main__":
    unittest.main()
