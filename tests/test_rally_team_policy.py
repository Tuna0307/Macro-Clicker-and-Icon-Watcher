import unittest

from macro_clicker.rally_team_policy import (
    RALLY_TEAM_BUSY,
    RALLY_TEAM_IDLE,
    RALLY_TEAM_LEVEL_CAP_UNBOUNDED,
    RALLY_TEAM_UNKNOWN,
    available_rally_team_level_cap,
    eligible_rally_teams_for_level,
    select_rally_team_for_level,
)


class RallyTeamPolicyTests(unittest.TestCase):
    limits = {1: 70, 2: 60, 3: 50}
    priority = [3, 2, 1]

    def test_three_team_level_matrix(self):
        statuses = {1: RALLY_TEAM_IDLE, 2: RALLY_TEAM_IDLE, 3: RALLY_TEAM_IDLE}
        expected = {
            1: 3,
            40: 3,
            50: 3,
            51: 2,
            60: 2,
            61: 1,
            70: 1,
            71: None,
        }
        for level, team_number in expected.items():
            with self.subTest(level=level):
                self.assertEqual(
                    select_rally_team_for_level(
                        level, statuses, self.limits, self.priority
                    ),
                    team_number,
                )

    def test_three_team_status_matrix(self):
        cases = (
            ({1: RALLY_TEAM_IDLE, 2: RALLY_TEAM_IDLE, 3: RALLY_TEAM_BUSY}, 40, 2),
            ({1: RALLY_TEAM_IDLE, 2: RALLY_TEAM_BUSY, 3: RALLY_TEAM_IDLE}, 55, 1),
            ({1: RALLY_TEAM_BUSY, 2: RALLY_TEAM_IDLE, 3: RALLY_TEAM_IDLE}, 65, None),
            ({1: RALLY_TEAM_BUSY, 2: RALLY_TEAM_IDLE, 3: RALLY_TEAM_BUSY}, 60, 2),
            ({1: RALLY_TEAM_BUSY, 2: RALLY_TEAM_IDLE, 3: RALLY_TEAM_BUSY}, 61, None),
            ({1: RALLY_TEAM_BUSY, 2: RALLY_TEAM_BUSY, 3: RALLY_TEAM_IDLE}, 50, 3),
            ({1: RALLY_TEAM_BUSY, 2: RALLY_TEAM_BUSY, 3: RALLY_TEAM_IDLE}, 51, None),
            ({1: RALLY_TEAM_IDLE, 2: RALLY_TEAM_IDLE, 3: RALLY_TEAM_UNKNOWN}, 40, 2),
            ({1: RALLY_TEAM_UNKNOWN, 2: RALLY_TEAM_BUSY, 3: RALLY_TEAM_BUSY}, 40, None),
        )
        for statuses, level, expected in cases:
            with self.subTest(statuses=statuses, level=level):
                self.assertEqual(
                    select_rally_team_for_level(
                        level, statuses, self.limits, self.priority
                    ),
                    expected,
                )

    def test_custom_limits_do_not_assume_strength_from_team_number(self):
        statuses = {1: RALLY_TEAM_IDLE, 2: RALLY_TEAM_IDLE, 3: RALLY_TEAM_IDLE}
        limits = {1: 60, 2: 70, 3: 45}

        self.assertEqual(
            select_rally_team_for_level(40, statuses, limits, self.priority), 3
        )
        self.assertEqual(
            select_rally_team_for_level(50, statuses, limits, self.priority), 1
        )
        self.assertEqual(
            select_rally_team_for_level(61, statuses, limits, self.priority), 2
        )

    def test_priority_breaks_ties_between_equal_capable_limits(self):
        statuses = {1: RALLY_TEAM_IDLE, 2: RALLY_TEAM_IDLE, 3: RALLY_TEAM_IDLE}
        limits = {1: 60, 2: 60, 3: 60}

        self.assertEqual(
            eligible_rally_teams_for_level(50, statuses, limits, self.priority),
            (3, 2, 1),
        )

    def test_unlimited_team_is_eligible_and_respects_priority(self):
        statuses = {1: RALLY_TEAM_IDLE, 2: RALLY_TEAM_IDLE, 3: RALLY_TEAM_IDLE}

        self.assertEqual(
            select_rally_team_for_level(
                999, statuses, {1: 70, 2: None, 3: 50}, self.priority
            ),
            2,
        )
        self.assertEqual(
            select_rally_team_for_level(
                999, statuses, {1: 70, 2: 60, 3: None}, self.priority
            ),
            3,
        )

    def test_pre_entry_level_cap_uses_only_enabled_idle_teams(self):
        cases = (
            ({1: RALLY_TEAM_IDLE, 2: RALLY_TEAM_BUSY, 3: RALLY_TEAM_IDLE}, 70),
            ({1: RALLY_TEAM_BUSY, 2: RALLY_TEAM_IDLE, 3: RALLY_TEAM_IDLE}, 60),
            ({1: RALLY_TEAM_UNKNOWN, 2: RALLY_TEAM_BUSY, 3: RALLY_TEAM_IDLE}, 50),
            ({1: RALLY_TEAM_BUSY, 2: RALLY_TEAM_BUSY, 3: RALLY_TEAM_UNKNOWN}, None),
        )
        for statuses, expected in cases:
            with self.subTest(statuses=statuses):
                self.assertEqual(
                    available_rally_team_level_cap(
                        statuses, self.limits, self.priority
                    ),
                    expected,
                )

    def test_pre_entry_level_cap_reports_unbounded(self):
        statuses = {1: RALLY_TEAM_BUSY, 2: RALLY_TEAM_IDLE, 3: RALLY_TEAM_IDLE}

        self.assertEqual(
            available_rally_team_level_cap(
                statuses, {1: 70, 2: None, 3: 50}, self.priority
            ),
            RALLY_TEAM_LEVEL_CAP_UNBOUNDED,
        )

    def test_legacy_priority_ignores_team2_status_and_limit(self):
        both_idle = {1: RALLY_TEAM_IDLE, 2: RALLY_TEAM_IDLE, 3: RALLY_TEAM_IDLE}
        cases = (
            (both_idle, 40, 3),
            (both_idle, 55, 1),
            (both_idle, 71, None),
            ({1: RALLY_TEAM_IDLE, 2: RALLY_TEAM_IDLE, 3: RALLY_TEAM_BUSY}, 40, 1),
            ({1: RALLY_TEAM_BUSY, 2: RALLY_TEAM_IDLE, 3: RALLY_TEAM_IDLE}, 40, 3),
            ({1: RALLY_TEAM_BUSY, 2: RALLY_TEAM_IDLE, 3: RALLY_TEAM_BUSY}, 40, None),
        )
        limits = {1: 70, 2: None, 3: 50}
        for statuses, level, expected in cases:
            with self.subTest(statuses=statuses, level=level):
                self.assertEqual(
                    select_rally_team_for_level(level, statuses, limits),
                    expected,
                )
        self.assertEqual(
            eligible_rally_teams_for_level(40, both_idle, limits),
            (3, 1),
        )

    def test_missing_status_or_limit_fails_closed(self):
        self.assertIsNone(
            select_rally_team_for_level(
                40,
                {1: RALLY_TEAM_BUSY, 3: RALLY_TEAM_BUSY},
                {1: 70, 2: 60, 3: 50},
                self.priority,
            )
        )
        self.assertIsNone(
            select_rally_team_for_level(
                40,
                {2: RALLY_TEAM_IDLE},
                {1: 70, 3: 50},
                self.priority,
            )
        )


if __name__ == "__main__":
    unittest.main()
