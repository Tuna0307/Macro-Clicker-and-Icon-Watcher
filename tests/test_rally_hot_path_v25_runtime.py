import unittest
from types import SimpleNamespace
from unittest.mock import patch

from macro_clicker import rally_hot_path_v25_runtime as v25
from macro_clicker.rally_team_policy import RALLY_TEAM_BUSY


class RallyHotPathV25RuntimeTests(unittest.TestCase):
    @staticmethod
    def _tray_result(*, formation_visible=False):
        return {
            "tray_valid": True,
            "formation_visible": formation_visible,
            "states": {
                1: RALLY_TEAM_BUSY,
                2: RALLY_TEAM_BUSY,
                3: RALLY_TEAM_BUSY,
            },
            "window_rect": (-1920, 0, 1920, 1080),
        }

    @staticmethod
    def _engine(**overrides):
        values = {
            "_rally_hot_path_three_team": True,
            "_rally_hot_entry_latched": True,
            "_pending_rally_level": 65,
            "_pending_rally_team_selected": None,
            "_rally_v25_dismiss_pending": False,
            "_rally_v25_dismiss_attempts": 0,
            "_rally_v25_last_dismiss_click_at": 0.0,
            "_rally_v25_tray_absent_since": 0.0,
            "_rally_hot_profile_armed": False,
            "_rally_v17_profile_recovery_owned": False,
            "_rally_v17_profile_recovery_until": 0.0,
            "_rally_hot_base_armed": False,
            "_rally_hot_base_not_before": 0.0,
            "_rally_v9_base_arm_expires": 0.0,
            "log_messages": [],
            "clicks": [],
        }
        values.update(overrides)
        engine = SimpleNamespace(**values)
        engine.log = engine.log_messages.append

        def click_point(x, y, button):
            engine.clicks.append((x, y, button))
            return True

        engine._click_point = click_point
        return engine

    def test_reference_dismiss_points_are_outside_tray_rectangle(self):
        left, top, width, height = v25._v7.TRAY_ANCHOR_REGION
        right = left + width
        bottom = top + height

        for x, y in v25.TRAY_DISMISS_REFERENCE_POINTS:
            inside = left <= x <= right and top <= y <= bottom
            self.assertFalse(inside, (x, y))

    def test_first_click_stays_latched_until_closure_is_verified(self):
        engine = self._engine()
        tray = self._tray_result()

        with patch.object(v25._v7, "_capture_full_squad_tray", return_value=tray), \
             patch.object(v25.time, "monotonic", return_value=10.0):
            result = v25._attempt_verified_tray_dismiss(engine)

        self.assertFalse(result)
        self.assertTrue(engine._rally_hot_entry_latched)
        self.assertTrue(engine._rally_v25_dismiss_pending)
        self.assertEqual(engine._rally_v25_dismiss_attempts, 1)
        self.assertEqual(engine.clicks, [(-1320, 1045, "left")])
        self.assertTrue(engine._rally_hot_base_armed)
        self.assertTrue(engine._rally_hot_profile_armed)
        self.assertTrue(
            any("workflow remains latched" in line for line in engine.log_messages)
        )

    def test_persistent_tray_uses_alternate_outside_point(self):
        engine = self._engine(
            _rally_v25_dismiss_pending=True,
            _rally_v25_dismiss_attempts=1,
            _rally_v25_last_dismiss_click_at=10.0,
        )
        tray = self._tray_result()

        with patch.object(v25._v7, "_capture_full_squad_tray", return_value=tray), \
             patch.object(v25.time, "monotonic", return_value=10.5):
            result = v25._recover_all_busy_tray(engine)

        self.assertFalse(result)
        self.assertTrue(engine._rally_hot_entry_latched)
        self.assertEqual(engine._rally_v25_dismiss_attempts, 2)
        self.assertEqual(engine.clicks, [(-600, 1045, "left")])
        self.assertTrue(
            any("tray still positively present" in line for line in engine.log_messages)
        )

    def test_two_absent_captures_confirm_close_before_releasing_latch(self):
        engine = self._engine(
            _rally_v25_dismiss_pending=True,
            _rally_v25_dismiss_attempts=1,
            _rally_v25_last_dismiss_click_at=10.0,
        )
        cleared = []

        with patch.object(v25._v7, "_capture_full_squad_tray", return_value=None), \
             patch.object(v25._v9, "_condition_visible", return_value=True), \
             patch.object(v25._v7, "_clear_workflow_after_tray",
                          side_effect=lambda _engine: cleared.append(True)), \
             patch.object(v25.time, "monotonic", return_value=10.5):
            self.assertFalse(v25._recover_all_busy_tray(engine))

        self.assertTrue(engine._rally_hot_entry_latched)
        self.assertFalse(cleared)

        with patch.object(v25._v7, "_capture_full_squad_tray", return_value=None), \
             patch.object(v25._v7, "_clear_workflow_after_tray",
                          side_effect=lambda _engine: cleared.append(True)), \
             patch.object(v25.time, "monotonic", return_value=10.8):
            self.assertTrue(v25._recover_all_busy_tray(engine))

        self.assertEqual(cleared, [True])
        self.assertFalse(engine._rally_hot_entry_latched)
        self.assertFalse(engine._rally_v25_dismiss_pending)
        self.assertTrue(
            any("tray closure confirmed" in line for line in engine.log_messages)
        )

    def test_formation_appearance_cancels_pending_tray_recovery(self):
        engine = self._engine(
            _rally_v25_dismiss_pending=True,
            _rally_v25_dismiss_attempts=1,
            _rally_v25_last_dismiss_click_at=10.0,
        )

        with patch.object(
            v25._v7,
            "_capture_full_squad_tray",
            return_value=self._tray_result(formation_visible=True),
        ), patch.object(v25.time, "monotonic", return_value=10.5):
            result = v25._recover_all_busy_tray(engine)

        self.assertFalse(result)
        self.assertTrue(engine._rally_hot_entry_latched)
        self.assertFalse(engine._rally_v25_dismiss_pending)
        self.assertTrue(
            any("formation panel appeared" in line for line in engine.log_messages)
        )

    def test_two_team_path_delegates_unchanged(self):
        engine = self._engine(_rally_hot_path_three_team=False)

        with patch.object(v25, "_ORIGINAL_RECOVER", return_value=True) as original:
            self.assertTrue(v25._recover_all_busy_tray(engine))

        original.assert_called_once_with(engine)


if __name__ == "__main__":
    unittest.main()
