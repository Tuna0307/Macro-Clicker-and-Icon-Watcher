import unittest

from macro_clicker.editors import (
    RALLY_TEAM_MODE_LEGACY,
    RALLY_TEAM_MODE_THREE,
    rally_team_editor_modes,
    rally_team_max_level_editor_values,
)
from macro_clicker.models import Action


class RallyTeamEditorTests(unittest.TestCase):
    def test_old_action_opens_and_saves_as_legacy_membership(self):
        selected, options = rally_team_editor_modes(None)

        self.assertEqual(selected, RALLY_TEAM_MODE_LEGACY)
        self.assertIsNone(options[selected])

    def test_three_team_mode_exposes_exact_priority(self):
        selected, options = rally_team_editor_modes([3, 2, 1])

        self.assertEqual(selected, RALLY_TEAM_MODE_THREE)
        self.assertEqual(options[selected], [3, 2, 1])

    def test_existing_custom_priority_is_preserved(self):
        selected, options = rally_team_editor_modes([2, 3, 1])

        self.assertEqual(selected, "Custom priority: 2 -> 3 -> 1")
        self.assertEqual(options[selected], [2, 3, 1])

    def test_all_three_maximums_are_exposed_and_blank_means_unlimited(self):
        action = Action(
            type="select_rally_team",
            team1_max_level=70,
            team2_max_level=None,
            team3_max_level=50,
        )

        self.assertEqual(
            rally_team_max_level_editor_values(action),
            ("70", "", "50"),
        )


if __name__ == "__main__":
    unittest.main()
