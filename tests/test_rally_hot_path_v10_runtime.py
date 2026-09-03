import unittest
from unittest.mock import patch

from macro_clicker import rally_hot_path_v10_runtime as v10
from macro_clicker.engine import MacroEngine
from macro_clicker.models import load_scenario
from macro_clicker.rally_team_policy import RALLY_TEAM_BUSY, RALLY_TEAM_IDLE


class RallyHotPathV10RuntimeTests(unittest.TestCase):
    def _engine(self, scenario_name="Rally gold mob_ 3 team"):
        engine = object.__new__(MacroEngine)
        engine.scenario = load_scenario(scenario_name)
        engine.log_messages = []
        engine.log = engine.log_messages.append
        engine._rally_hot_path_three_team = scenario_name.endswith("3 team")
        engine._rally_hot_entry_latched = True
        engine._pending_rally_level = 65
        engine._pending_rally_team_selected = None
        engine._rally_v10_join_click_at = 10.0
        engine._rally_v10_tray_busy_since = None
        return engine

    @staticmethod
    def _all_busy_result():
        return {
            "tray_valid": True,
            "formation_visible": False,
            "states": {
                1: RALLY_TEAM_BUSY,
                2: RALLY_TEAM_BUSY,
                3: RALLY_TEAM_BUSY,
            },
        }

    def test_transition_grace_never_probes_or_dismisses_tray(self):
        engine = self._engine()
        with (
            patch.object(
                v10._v7,
                "_capture_full_squad_tray",
                side_effect=AssertionError("tray probed during formation grace"),
            ),
            patch.object(
                v10,
                "_ORIGINAL_V7_RECOVER",
                side_effect=AssertionError("tray dismissed during formation grace"),
            ),
        ):
            recovered = v10._guarded_recover_all_busy_tray(engine, now=10.45)

        self.assertFalse(recovered)
        self.assertIsNone(engine._rally_v10_tray_busy_since)

    def test_all_busy_tray_requires_stable_confirmation_before_v7_dismiss(self):
        engine = self._engine()
        original_calls = []

        def original(_engine):
            original_calls.append(True)
            return True

        with (
            patch.object(
                v10._v7,
                "_capture_full_squad_tray",
                return_value=self._all_busy_result(),
            ),
            patch.object(v10, "_ORIGINAL_V7_RECOVER", side_effect=original),
        ):
            self.assertFalse(v10._guarded_recover_all_busy_tray(engine, now=11.10))
            self.assertFalse(v10._guarded_recover_all_busy_tray(engine, now=11.20))
            self.assertTrue(v10._guarded_recover_all_busy_tray(engine, now=11.40))

        self.assertEqual(len(original_calls), 1)
        self.assertIsNone(engine._rally_v10_tray_busy_since)
        self.assertTrue(
            any("stable all-busy tray confirmed" in line for line in engine.log_messages)
        )

    def test_idle_evidence_cancels_pending_all_busy_confirmation(self):
        engine = self._engine()
        idle_result = self._all_busy_result()
        idle_result["states"] = dict(idle_result["states"])
        idle_result["states"][3] = RALLY_TEAM_IDLE

        with (
            patch.object(
                v10._v7,
                "_capture_full_squad_tray",
                side_effect=[self._all_busy_result(), idle_result],
            ),
            patch.object(
                v10,
                "_ORIGINAL_V7_RECOVER",
                side_effect=AssertionError("IDLE evidence must not dismiss tray"),
            ),
        ):
            self.assertFalse(v10._guarded_recover_all_busy_tray(engine, now=11.10))
            self.assertFalse(v10._guarded_recover_all_busy_tray(engine, now=11.40))

        self.assertIsNone(engine._rally_v10_tray_busy_since)

    def test_record_join_click_resets_old_tray_candidate(self):
        engine = self._engine()
        engine._rally_v10_tray_busy_since = 5.0

        v10._record_join_click(engine, now=20.0)

        self.assertEqual(engine._rally_v10_join_click_at, 20.0)
        self.assertIsNone(engine._rally_v10_tray_busy_since)

    def test_two_team_path_delegates_to_v7_unchanged(self):
        engine = self._engine("Rally gold mob_ 2 team")
        sentinel = object()

        with patch.object(v10, "_ORIGINAL_V7_RECOVER", return_value=sentinel):
            result = v10._guarded_recover_all_busy_tray(engine, now=10.1)

        self.assertIs(result, sentinel)


if __name__ == "__main__":
    unittest.main()
