import unittest

import cv2
import numpy as np

from macro_clicker.engine import MacroEngine
from macro_clicker.models import (
    Action,
    ImageCondition,
    Scenario,
    Step,
    load_scenario,
    project_path,
    validate_scenario,
)


class RallyTeamSelectionTests(unittest.TestCase):
    @staticmethod
    def _action():
        return Action(
            type="select_rally_team",
            on_condition_index=0,
            team_idle_template_path="templates/TeamIdle.png",
            team_idle_confidence=0.85,
            team1_idle_region=[10, 10, 10, 10],
            team1_click_offset=[15, 20],
            team1_max_level=65,
            team3_idle_region=[-20, 10, 10, 10],
            team3_click_offset=[-15, 20],
            team3_max_level=45,
        )

    def _engine(self, level, *, team1_idle, team3_idle):
        engine = object.__new__(MacroEngine)
        engine.scenario = Scenario(name="Two rally teams")
        engine._stop_event = type("Stop", (), {"is_set": lambda self: False})()
        engine._pending_rally_level = level
        engine._scaled_template_cache = {}
        engine._retry_current_step = False
        engine.log = lambda _message: None
        engine._get_target_window_rect = lambda: None
        engine._load_template = lambda _path: np.full((4, 4, 3), 255, dtype=np.uint8)
        engine._best_scaled_template_match = lambda crop, _template: (
            float(crop.mean() / 255.0),
            (0, 0),
        )
        engine._submit_rally_diagnostic = lambda *_args, **_kwargs: None
        clicked = []
        engine._click_point = (
            lambda x, y, button: clicked.append((x, y, button)) or True
        )

        def grab(region):
            frame = np.zeros((region[3], region[2], 3), dtype=np.uint8)
            # At anchor (500, 300), Team 3 occupies union x=0..9 and
            # Team 1 occupies x=30..39.
            if team3_idle and region[2] >= 10:
                frame[:, :10] = 255
            if team1_idle and region[2] >= 40:
                frame[:, 30:40] = 255
            return frame, region[0], region[1]

        engine._grab = grab
        return engine, clicked

    @staticmethod
    def _context():
        return (
            {0: (500, 300)},
            {
                0: [
                    {
                        "center": (500, 300),
                        "scale_x": 1.0,
                        "scale_y": 1.0,
                    }
                ]
            },
        )

    def _availability_from_queue_fixture(self, fixture_name):
        scenario = load_scenario("Rally gold mob_ 2 team")
        action = next(
            action
            for step in scenario.steps
            if step.name == "Joining"
            for action in step.actions
            if action.type == "click_matching_row"
        )
        frame = cv2.imread(
            project_path(f"tests/fixtures/rally_team_status/{fixture_name}")
        )
        self.assertIsNotNone(frame)

        engine = object.__new__(MacroEngine)
        engine.scenario = scenario
        engine._stop_event = type("Stop", (), {"is_set": lambda self: False})()
        engine._scaled_template_cache = {}
        engine._last_rally_team_busy_state = None
        engine._last_rally_team_availability = {}
        engine.low_variance_threshold = 1.0
        engine.log = lambda _message: None
        engine._get_target_window_rect = lambda: (0, 0, 1920, 1080)
        engine._load_template = lambda path: cv2.imread(project_path(path))
        engine._grab = lambda region: (frame.copy(), region[0], region[1])

        cap = engine._available_rally_team_level_cap(action)
        return cap, engine._last_rally_team_availability["busy"]

    def test_low_level_prefers_idle_team3_even_when_team1_is_idle(self):
        engine, clicked = self._engine(45, team1_idle=True, team3_idle=True)
        points, matches = self._context()

        result = engine._run_select_rally_team_action(
            self._action(), points, matches
        )

        self.assertTrue(result)
        self.assertEqual(clicked, [(485, 320, "left")])
        self.assertIsNone(engine._pending_rally_level)

    def test_supplied_two_idle_frame_selects_stetmann_for_level_45(self):
        scenario = load_scenario("Rally gold mob_ 2 team")
        action = next(
            action
            for step in scenario.steps
            if step.name == "Attack Confirm"
            for action in step.actions
            if action.type == "select_rally_team"
        )
        frame = cv2.imread(
            project_path(
                "tests/fixtures/rally_team_selection/both_idle_union.png"
            )
        )
        self.assertIsNotNone(frame)

        engine = object.__new__(MacroEngine)
        engine.scenario = scenario
        engine._stop_event = type("Stop", (), {"is_set": lambda self: False})()
        engine._pending_rally_level = 45
        engine._scaled_template_cache = {}
        engine._retry_current_step = False
        engine.low_variance_threshold = 1.0
        logs = []
        engine.log = logs.append
        engine._get_target_window_rect = lambda: None
        engine._load_template = lambda path: cv2.imread(project_path(path))
        engine._grab = lambda _region: (frame.copy(), 713, 938)
        engine._submit_rally_diagnostic = lambda *_args, **_kwargs: None
        clicked = []
        engine._click_point = (
            lambda x, y, button: clicked.append((x, y, button)) or True
        )
        points = {0: (962, 808)}
        matches = {
            0: [{"center": (962, 808), "scale_x": 1.0, "scale_y": 1.0}]
        }

        result = engine._run_select_rally_team_action(action, points, matches)

        self.assertTrue(result)
        self.assertEqual(clicked, [(1025, 976, "left")])
        self.assertIn("Team 3=1.00", "\n".join(logs))

    def test_low_level_falls_back_to_team1_when_team3_is_busy(self):
        engine, clicked = self._engine(30, team1_idle=True, team3_idle=False)
        points, matches = self._context()

        result = engine._run_select_rally_team_action(
            self._action(), points, matches
        )

        self.assertTrue(result)
        self.assertEqual(clicked, [(515, 320, "left")])

    def test_low_level_fallback_records_scores_from_exact_dispatch_frame(self):
        engine, clicked = self._engine(45, team1_idle=True, team3_idle=False)
        points, matches = self._context()
        logs = []
        engine.log = logs.append
        captured_frames = []
        original_grab = engine._grab

        def grab(region):
            result = original_grab(region)
            captured_frames.append(result)
            return result

        engine._grab = grab
        submissions = []

        def submit(event_type, metadata, **kwargs):
            submissions.append((event_type, metadata, kwargs))

        engine._submit_rally_diagnostic = submit

        result = engine._run_select_rally_team_action(
            self._action(), points, matches
        )

        self.assertTrue(result)
        self.assertEqual(clicked, [(515, 320, "left")])
        self.assertIn("Team 3=0.00, Team 1=1.00", "\n".join(logs))
        self.assertEqual(len(submissions), 1)
        event_type, metadata, kwargs = submissions[0]
        self.assertEqual(event_type, "rally_team_preferred_fallback")
        self.assertEqual(metadata["decision"], "preferred_team_fallback")
        self.assertEqual(metadata["selected_team"], 1)
        self.assertEqual(metadata["level"], 45)
        snapshot = kwargs["context_snapshot"]
        self.assertIs(snapshot.frame, captured_frames[0][0])
        self.assertEqual((snapshot.left, snapshot.top), captured_frames[0][1:])
        self.assertIn("matches", kwargs)

    def test_high_level_uses_team1_and_never_team3(self):
        engine, clicked = self._engine(60, team1_idle=True, team3_idle=True)
        points, matches = self._context()

        result = engine._run_select_rally_team_action(
            self._action(), points, matches
        )

        self.assertTrue(result)
        self.assertEqual(clicked, [(515, 320, "left")])

    def test_no_eligible_idle_team_aborts_before_attack_action(self):
        engine, clicked = self._engine(45, team1_idle=False, team3_idle=False)
        engine._abort_current_step = False
        points, matches = self._context()

        result = engine._run_select_rally_team_action(
            self._action(), points, matches
        )

        self.assertFalse(result)
        self.assertEqual(clicked, [])
        self.assertFalse(engine._retry_current_step)
        self.assertTrue(engine._abort_current_step)
        self.assertIsNone(engine._pending_rally_level)

    def test_high_level_with_busy_team1_aborts_for_back_recovery(self):
        engine, clicked = self._engine(50, team1_idle=False, team3_idle=False)
        engine._abort_current_step = False
        points, matches = self._context()

        result = engine._run_select_rally_team_action(
            self._action(), points, matches
        )

        self.assertFalse(result)
        self.assertEqual(clicked, [])
        self.assertFalse(engine._retry_current_step)
        self.assertTrue(engine._abort_current_step)
        self.assertIsNone(engine._pending_rally_level)

    def test_aborted_attack_step_skips_attack_click_and_runs_recovery_step(self):
        attack_step = Step(
            name="Attack Confirm",
            actions=[
                Action(type="wait", seconds=1.0),
                Action(type="click", x=10, y=20),
            ],
        )
        recovery_step = Step(
            name="Back if wrong mob",
            actions=[Action(type="wait", seconds=2.0)],
        )
        engine = object.__new__(MacroEngine)
        engine.scenario = Scenario(
            name="Abort recovery",
            steps=[attack_step, recovery_step],
        )
        engine._stop_event = type("Stop", (), {"is_set": lambda self: False})()
        engine._last_fired = {attack_step.name: 0.0, recovery_step.name: 0.0}
        engine._evaluate_uses_frame_cache = False
        engine._evaluate_step = lambda _step: (True, {}, {})
        engine._refresh_step_caches = lambda: [attack_step, recovery_step]
        engine._prepare_rally_team_availability_for_entry = lambda _step: True
        engine._should_log_perf = lambda *_args, **_kwargs: False
        engine.log = lambda _message: None
        executed = []

        def run_action(step, action, _points, _matches):
            executed.append((step.name, action.type, action.seconds))
            if step is attack_step and action is attack_step.actions[0]:
                engine._abort_current_step = True
            return False

        engine._run_action = run_action

        self.assertTrue(engine._cycle())
        self.assertEqual(
            executed,
            [
                ("Attack Confirm", "wait", 1.0),
                ("Back if wrong mob", "wait", 2.0),
            ],
        )

    def test_team_action_round_trips_and_validates(self):
        action = self._action()
        action.team1_idle_template_path = "templates/Team1Idle.png"
        action.team3_idle_template_path = "templates/Team3Idle.png"
        restored = Action.from_dict(action.to_dict())
        scenario = Scenario(
            name="Two rally teams",
            steps=[
                Step(
                    name="Select",
                    conditions=[ImageCondition(template_path="templates/Attack.png")],
                    actions=[restored],
                )
            ],
        )

        validate_scenario(scenario)

        self.assertEqual(
            restored.team1_idle_template_path,
            action.team1_idle_template_path,
        )
        self.assertEqual(
            restored.team3_idle_template_path,
            action.team3_idle_template_path,
        )

    def test_team_action_with_only_shared_idle_template_remains_valid(self):
        action = self._action()
        scenario = Scenario(
            name="Legacy two rally teams",
            steps=[
                Step(
                    name="Select",
                    conditions=[ImageCondition(template_path="templates/Attack.png")],
                    actions=[action],
                )
            ],
        )

        validate_scenario(scenario)

    def test_matching_row_click_carries_its_ocr_level_to_team_selection(self):
        engine = object.__new__(MacroEngine)
        engine._stop_event = type("Stop", (), {"is_set": lambda self: False})()
        engine.log = lambda _message: None
        engine._pending_rally_level = None
        engine._record_matching_row_diagnostic = lambda *_args, **_kwargs: None
        engine._find_matching_row_selections = lambda *_args, **_kwargs: (
            [
                {
                    "reference": {"center": (100, 100)},
                    "target": {
                        "center": (300, 100),
                        "scale_x": 1.0,
                        "scale_y": 1.0,
                    },
                    "level": 45,
                }
            ],
            False,
        )
        clicked = []
        engine._click_point = (
            lambda x, y, button: clicked.append((x, y, button)) or True
        )
        action = Action(
            type="click_matching_row",
            match_condition_index=0,
            on_condition_index=1,
            max_level=65,
        )
        step = Step(name="Joining", actions=[action])
        engine._matching_row_reuse_context = (step, action, object())

        result = engine._run_action(
            step,
            action,
            {0: (100, 100), 1: (300, 100)},
            {0: [{"center": (100, 100)}], 1: [{"center": (300, 100)}]},
        )

        self.assertTrue(result)
        self.assertEqual(clicked, [(300, 100, "left")])
        self.assertEqual(engine._pending_rally_level, 45)

    def test_two_team_scenario_has_the_expected_gate_ranges_and_priority(self):
        scenario = load_scenario("Rally gold mob_ 2 team")
        validate_scenario(scenario, require_files=True)
        steps = {step.name: step for step in scenario.steps}

        one_third_gate = steps["Click Rally Icon 1/3"].conditions[1]
        two_thirds_gate = steps["Click Rally Icon 2/3"].conditions[1]
        row_action = next(
            action
            for action in steps["Joining"].actions
            if action.type == "click_matching_row"
        )
        team_action = next(
            action
            for action in steps["Attack Confirm"].actions
            if action.type == "select_rally_team"
        )

        self.assertEqual(one_third_gate.template_path, "templates/1_3Squad.png")
        self.assertFalse(one_third_gate.negate)
        self.assertEqual(one_third_gate.confidence, 0.9)
        self.assertEqual(two_thirds_gate.template_path, "templates/2_3Squad.png")
        self.assertFalse(two_thirds_gate.negate)
        self.assertEqual(row_action.max_level, 65)
        self.assertEqual(
            row_action.team1_busy_template_path,
            "templates/Team1Busy.png",
        )
        self.assertEqual(
            row_action.team3_busy_template_path,
            "templates/Team3Busy.png",
        )
        self.assertEqual(team_action.team3_max_level, 45)
        self.assertEqual(team_action.team1_max_level, 65)
        self.assertEqual(team_action.team1_idle_region, [-249, 130, 40, 36])
        self.assertEqual(team_action.team1_click_offset, [-189, 168])
        self.assertEqual(team_action.team3_idle_region, [3, 130, 40, 36])
        self.assertEqual(team_action.team3_click_offset, [63, 168])
        self.assertEqual(
            team_action.team_idle_template_path,
            "templates/TeamIdle.png",
        )
        self.assertEqual(
            team_action.team1_idle_template_path,
            "templates/Team1Idle.png",
        )
        self.assertEqual(
            team_action.team3_idle_template_path,
            "templates/Team3Idle.png",
        )

    def test_busy_portraits_adapt_the_row_level_cap(self):
        scenario = load_scenario("Rally gold mob_ 2 team")
        action = next(
            action
            for step in scenario.steps
            if step.name == "Joining"
            for action in step.actions
            if action.type == "click_matching_row"
        )
        templates = {
            1: cv2.imread(project_path(action.team1_busy_template_path)),
            3: cv2.imread(project_path(action.team3_busy_template_path)),
        }

        def level_cap(*, team1_busy, team3_busy):
            engine = object.__new__(MacroEngine)
            engine.scenario = scenario
            engine._stop_event = type("Stop", (), {"is_set": lambda self: False})()
            engine._scaled_template_cache = {}
            engine.low_variance_threshold = 1.0
            engine.log = lambda _message: None
            engine._get_target_window_rect = lambda: (0, 0, 1920, 1080)
            engine._load_template = lambda path: cv2.imread(project_path(path))

            def grab(region):
                frame = np.zeros((region[3], region[2], 3), dtype=np.uint8)
                if team1_busy:
                    frame[15:63, 12:62] = templates[1]
                if team3_busy:
                    frame[82:130, 12:62] = templates[3]
                return frame, region[0], region[1]

            engine._grab = grab
            return engine._available_rally_team_level_cap(action)

        self.assertEqual(level_cap(team1_busy=False, team3_busy=True), 65)
        self.assertEqual(level_cap(team1_busy=True, team3_busy=False), 45)
        self.assertEqual(level_cap(team1_busy=False, team3_busy=False), 65)
        self.assertIsNone(level_cap(team1_busy=True, team3_busy=True))

    def test_busy_team_requires_a_clear_score_drop_before_becoming_idle(self):
        scenario = load_scenario("Rally gold mob_ 2 team")
        action = next(
            action
            for step in scenario.steps
            if step.name == "Joining"
            for action in step.actions
            if action.type == "click_matching_row"
        )
        engine = object.__new__(MacroEngine)
        engine.scenario = scenario
        engine._stop_event = type("Stop", (), {"is_set": lambda self: False})()
        engine._pending_rally_team_availability = None
        engine._last_rally_team_busy_state = None
        engine._last_rally_team_availability = {}
        engine.log = lambda _message: None
        engine._get_target_window_rect = lambda: (0, 0, 1920, 1080)
        engine._grab = lambda region: (
            np.zeros((region[3], region[2], 3), dtype=np.uint8),
            region[0],
            region[1],
        )
        engine._load_template = lambda path: path
        engine._scaled_template = lambda template, _scale: template
        current_scores = {1: 1.0, 3: 0.20}

        def match(_frame, template_path):
            team_number = 1 if template_path == action.team1_busy_template_path else 3
            return current_scores[team_number], (0, 0)

        engine._best_scaled_template_match = match

        self.assertEqual(engine._available_rally_team_level_cap(action), 45)
        current_scores[1] = 0.70
        self.assertEqual(engine._available_rally_team_level_cap(action), 45)
        current_scores[1] = 0.29
        self.assertEqual(engine._available_rally_team_level_cap(action), 65)

    def test_supplied_queue_frames_identify_murphy_and_stetmann_by_portrait(self):
        cases = {
            "carlie_only.png": (65, {1: False, 3: False}),
            "murphy_carlie.png": (45, {1: True, 3: False}),
            "all_three.png": (None, {1: True, 3: True}),
            "carlie_stetmann.png": (65, {1: False, 3: True}),
        }
        for fixture_name, (expected_cap, expected_busy) in cases.items():
            with self.subTest(fixture_name=fixture_name):
                cap, busy = self._availability_from_queue_fixture(fixture_name)
                self.assertEqual(cap, expected_cap)
                self.assertEqual(busy, expected_busy)

    def test_rally_entry_reuses_availability_captured_before_queue_hides(self):
        scenario = load_scenario("Rally gold mob_ 2 team")
        steps = {step.name: step for step in scenario.steps}
        entry_step = steps["Click Rally Icon 2/3"]
        row_action = next(
            action
            for action in steps["Joining"].actions
            if action.type == "click_matching_row"
        )
        team1_template = cv2.imread(project_path(row_action.team1_busy_template_path))

        engine = object.__new__(MacroEngine)
        engine.scenario = scenario
        engine._stop_event = type("Stop", (), {"is_set": lambda self: False})()
        engine._scaled_template_cache = {}
        engine._pending_rally_team_availability = None
        engine._last_rally_team_busy_state = None
        engine._last_rally_team_availability = {}
        engine.low_variance_threshold = 1.0
        engine.log = lambda _message: None
        engine._get_target_window_rect = lambda: (0, 0, 1920, 1080)
        engine._load_template = lambda path: cv2.imread(project_path(path))
        captures = []

        def grab(region):
            captures.append(region)
            frame = np.zeros((region[3], region[2], 3), dtype=np.uint8)
            # Team 1 is busy while the map queue is visible. A second capture
            # would represent the rally page, where this queue has disappeared.
            if len(captures) == 1:
                frame[15:63, 12:62] = team1_template
            return frame, region[0], region[1]

        engine._grab = grab

        self.assertTrue(engine._prepare_rally_team_availability_for_entry(entry_step))
        self.assertEqual(engine._available_rally_team_level_cap(row_action), 45)
        self.assertEqual(len(captures), 1)

    def test_rally_entry_is_blocked_when_team1_and_team3_are_both_busy(self):
        scenario = load_scenario("Rally gold mob_ 2 team")
        steps = {step.name: step for step in scenario.steps}
        entry_step = steps["Click Rally Icon 2/3"]
        row_action = next(
            action
            for action in steps["Joining"].actions
            if action.type == "click_matching_row"
        )
        templates = {
            1: cv2.imread(project_path(row_action.team1_busy_template_path)),
            3: cv2.imread(project_path(row_action.team3_busy_template_path)),
        }

        engine = object.__new__(MacroEngine)
        engine.scenario = scenario
        engine._stop_event = type("Stop", (), {"is_set": lambda self: False})()
        engine._scaled_template_cache = {}
        engine._pending_rally_team_availability = None
        engine._last_rally_team_busy_state = None
        engine._last_rally_team_availability = {}
        engine.low_variance_threshold = 1.0
        engine.log = lambda _message: None
        engine._get_target_window_rect = lambda: (0, 0, 1920, 1080)
        engine._load_template = lambda path: cv2.imread(project_path(path))

        def grab(region):
            frame = np.zeros((region[3], region[2], 3), dtype=np.uint8)
            frame[15:63, 12:62] = templates[1]
            frame[82:130, 12:62] = templates[3]
            return frame, region[0], region[1]

        engine._grab = grab

        self.assertFalse(engine._prepare_rally_team_availability_for_entry(entry_step))

    def test_non_entry_step_preserves_pre_entry_team_availability(self):
        scenario = load_scenario("Rally gold mob_ 2 team")
        joining_step = next(step for step in scenario.steps if step.name == "Joining")
        saved = {"level_cap": 45}
        engine = object.__new__(MacroEngine)
        engine.scenario = scenario
        engine._pending_rally_team_availability = saved

        self.assertTrue(engine._prepare_rally_team_availability_for_entry(joining_step))
        self.assertIs(engine._pending_rally_team_availability, saved)


if __name__ == "__main__":
    unittest.main()
