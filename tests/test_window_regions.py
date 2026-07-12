import unittest

from models import ImageCondition, Scenario
from engine import MacroEngine, _WINDOW_UNAVAILABLE
from app import resolve_condition_preview_box
from window_locator import (
    absolute_region_from_window,
    absolute_region_from_window_ratio,
    proportional_region_from_window,
    find_window_rect,
    resolve_window_region,
    visible_window_titles,
)


class WindowRegionTests(unittest.TestCase):
    def test_converts_window_relative_region_to_screen_coordinates(self):
        window_rect = (100, 200, 800, 600)
        relative_region = [10, 20, 300, 50]

        self.assertEqual(
            absolute_region_from_window(relative_region, window_rect),
            (110, 220, 300, 50),
        )

    def test_converts_absolute_region_to_proportional_window_region(self):
        window_rect = (100, 200, 800, 600)
        absolute_region = [180, 320, 200, 120]

        self.assertEqual(
            proportional_region_from_window(absolute_region, window_rect),
            (0.1, 0.2, 0.25, 0.2),
        )

    def test_converts_proportional_window_region_to_screen_coordinates_after_resize(self):
        resized_window_rect = (300, 400, 1600, 1200)
        proportional_region = [0.1, 0.2, 0.25, 0.2]

        self.assertEqual(
            absolute_region_from_window_ratio(proportional_region, resized_window_rect),
            (460, 640, 400, 240),
        )

    def test_resolve_window_region_uses_pixels_until_window_size_changes(self):
        moved_window = (300, 400, 800, 600)
        resized_window = (300, 400, 1600, 1200)
        relative_region = [80, 120, 200, 120]
        proportional_region = [0.1, 0.2, 0.25, 0.2]
        base_size = [800, 600]

        self.assertEqual(
            resolve_window_region(relative_region, moved_window, proportional_region, base_size),
            (380, 520, 200, 120),
        )
        self.assertEqual(
            resolve_window_region(relative_region, resized_window, proportional_region, base_size),
            (460, 640, 400, 240),
        )

    def test_models_default_to_screen_regions_for_old_scenarios(self):
        scenario = Scenario.from_dict({"name": "old", "steps": []})
        condition = ImageCondition.from_dict({"template_path": "templates/icon.png"})

        self.assertEqual(scenario.target_window_title, "")
        self.assertEqual(condition.region_mode, "screen")

    def test_models_round_trip_window_target_fields(self):
        scenario = Scenario(name="game", target_window_title="My Offline Game")
        condition = ImageCondition(
            template_path="templates/icon.png",
            comparison_template_path="templates/full.png",
            comparison_margin=0.06,
            region=[10, 20, 30, 40],
            region_mode="window",
            region_ratio=[0.0125, 0.0333333333, 0.0375, 0.0666666667],
            region_window_size=[800, 600],
        )

        self.assertEqual(
            Scenario.from_dict(scenario.to_dict()).target_window_title,
            "My Offline Game",
        )
        self.assertEqual(
            ImageCondition.from_dict(condition.to_dict()).region_mode,
            "window",
        )
        self.assertEqual(
            ImageCondition.from_dict(condition.to_dict()).region_ratio,
            [0.0125, 0.0333333333, 0.0375, 0.0666666667],
        )
        restored_condition = ImageCondition.from_dict(condition.to_dict())
        self.assertEqual(restored_condition.comparison_template_path, "templates/full.png")
        self.assertEqual(restored_condition.comparison_margin, 0.06)

    def test_engine_resolves_window_relative_condition_region(self):
        engine = object.__new__(MacroEngine)
        engine.scenario = Scenario(name="game", target_window_title="My Offline Game")
        engine._target_window_rect = (100, 200, 800, 600)
        condition = ImageCondition(
            template_path="templates/icon.png",
            region=[10, 20, 30, 40],
            region_mode="window",
            region_ratio=[0.0125, 0.0333333333, 0.0375, 0.0666666667],
            region_window_size=[800, 600],
        )

        self.assertEqual(engine._resolve_capture_region(condition), (110, 220, 30, 40))

    def test_engine_scales_window_relative_condition_region_after_resize(self):
        engine = object.__new__(MacroEngine)
        engine.scenario = Scenario(name="game", target_window_title="My Offline Game")
        engine._target_window_rect = (100, 200, 1600, 1200)
        condition = ImageCondition(
            template_path="templates/icon.png",
            region=[10, 20, 30, 40],
            region_mode="window",
            region_ratio=[0.0125, 0.0333333333, 0.0375, 0.0666666667],
            region_window_size=[800, 600],
        )

        self.assertEqual(engine._resolve_capture_region(condition), (120, 240, 60, 80))

    def test_engine_does_not_use_stale_window_rect_when_target_disappears(self):
        engine = object.__new__(MacroEngine)
        engine.scenario = Scenario(name="game", target_window_title="My Offline Game")
        engine._target_window_rect = (100, 200, 800, 600)
        engine._target_window_missing_logged = False
        engine._window_rect_provider = lambda title: None
        engine.log = lambda message: None
        condition = ImageCondition(
            template_path="templates/icon.png",
            region=[10, 20, 30, 40],
            region_mode="window",
        )

        self.assertIs(engine._resolve_capture_region(condition), _WINDOW_UNAVAILABLE)

    def test_condition_preview_box_uses_current_window_position(self):
        condition = ImageCondition(
            template_path="templates/icon.png",
            region=[10, 20, 30, 40],
            region_mode="window",
            region_ratio=[0.0125, 0.0333333333, 0.0375, 0.0666666667],
            region_window_size=[800, 600],
        )

        box = resolve_condition_preview_box(
            condition,
            target_window_title="Game",
            monitor_index=1,
            window_rect_provider=lambda title: (300, 400, 800, 600),
        )

        self.assertEqual(box, (310, 420, 30, 40))

    def test_visible_window_titles_filters_empty_and_duplicate_titles(self):
        class FakeWindow:
            def __init__(self, title, width=100, height=100):
                self.title = title
                self.width = width
                self.height = height

        windows = [
            FakeWindow("Discord"),
            FakeWindow(""),
            FakeWindow("Discord"),
            FakeWindow("Hidden", width=0),
            FakeWindow("Game"),
        ]

        self.assertEqual(visible_window_titles(lambda: windows), ["Discord", "Game"])

    def test_minimized_and_hidden_windows_are_not_targeted(self):
        class FakeWindow:
            def __init__(self, title, *, visible=True, minimized=False):
                self.title = title
                self.left = 1
                self.top = 2
                self.width = 100
                self.height = 80
                self.isVisible = visible
                self.isMinimized = minimized

        windows = [
            FakeWindow("Game hidden", visible=False),
            FakeWindow("Game minimized", minimized=True),
            FakeWindow("Game ready"),
        ]

        self.assertEqual(
            find_window_rect("Game", lambda: windows),
            (1, 2, 100, 80),
        )
        self.assertEqual(visible_window_titles(lambda: windows), ["Game ready"])

    def test_exact_window_title_beats_an_earlier_substring_match(self):
        class FakeWindow:
            def __init__(self, title, left):
                self.title = title
                self.left = left
                self.top = 0
                self.width = 100
                self.height = 80

        windows = [FakeWindow("Game launcher", 10), FakeWindow("Game", 20)]

        self.assertEqual(find_window_rect("Game", lambda: windows), (20, 0, 100, 80))


if __name__ == "__main__":
    unittest.main()
