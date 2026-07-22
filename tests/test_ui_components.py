import unittest
from unittest.mock import patch

from macro_clicker.models import Action, ImageCondition
from macro_clicker.ui_components import (
    BUTTON_STATE_COLORS,
    UiFeedback,
    _tone_wave,
    action_display_summary,
    condition_choice_for_index,
    condition_choices,
    condition_index_from_choice,
    preserved_level_roi,
)


class UiComponentTests(unittest.TestCase):
    def setUp(self):
        self.conditions = [
            ImageCondition(template_path="templates/GoldMob.png"),
            ImageCondition(template_path="templates/Join.png"),
            ImageCondition(template_path="templates/BackButton.png"),
        ]

    def test_condition_choices_use_names_but_round_trip_original_indices(self):
        choices = condition_choices(self.conditions)

        self.assertEqual(choices[1], "1: Join.png")
        self.assertEqual(condition_choice_for_index(self.conditions, 2), "2: BackButton.png")
        self.assertEqual(condition_index_from_choice(choices[1], "Click target"), 1)

    def test_optional_condition_choices_preserve_none(self):
        self.assertIsNone(
            condition_index_from_choice("Automatic target", "Click target", allow_blank=True)
        )
        self.assertIsNone(
            condition_index_from_choice("None", "Fallback", allow_blank=True)
        )

    def test_action_summary_uses_template_names_without_mutating_action(self):
        action = Action(
            type="click_matching_row",
            match_condition_index=0,
            on_condition_index=1,
            row_mode="all",
            min_level=20,
            max_level=60,
            pre_click_delay=1.5,
            row_tolerance=47,
            offset_x=8,
            offset_y=-3,
        )

        summary = action_display_summary(action, self.conditions)

        self.assertIn("Join.png", summary)
        self.assertIn("GoldMob.png", summary)
        self.assertIn("wait 1.5s after level check", summary)
        self.assertEqual(action.row_tolerance, 47)
        self.assertEqual((action.offset_x, action.offset_y), (8, -3))

    def test_collapsed_advanced_options_preserve_unset_level_roi(self):
        defaults = (-90, -45, 220, 100)

        self.assertIsNone(preserved_level_roi(None, False, defaults))
        self.assertEqual(preserved_level_roi(None, True, defaults), list(defaults))
        self.assertEqual(
            preserved_level_roi([1, 2, 3, 4], False, [1, 2, 3, 4]),
            [1, 2, 3, 4],
        )

    def test_button_interaction_states_have_distinct_colors(self):
        colors = {
            BUTTON_STATE_COLORS["default"],
            BUTTON_STATE_COLORS["hover"],
            BUTTON_STATE_COLORS["pressed"],
            BUTTON_STATE_COLORS["disabled"],
        }

        self.assertEqual(len(colors), 4)

    def test_generated_ui_tone_is_a_small_wave_file(self):
        sound = _tone_wave(((440, 20),), volume=0.1)

        self.assertTrue(sound.startswith(b"RIFF"))
        self.assertIn(b"WAVE", sound[:16])
        self.assertGreater(len(sound), 44)

    def test_disabled_ui_feedback_never_starts_audio_worker(self):
        feedback = UiFeedback(enabled=False)

        with patch("macro_clicker.ui_components.winsound") as mocked_sound:
            feedback.play("success")

        mocked_sound.PlaySound.assert_not_called()
        self.assertIsNone(feedback._worker)


if __name__ == "__main__":
    unittest.main()
