import unittest
from types import SimpleNamespace

import numpy as np

from macro_clicker import rally_hot_path_v16_runtime as v16


class RallyHotPathV16RuntimeTests(unittest.TestCase):
    def setUp(self):
        self._original = v16._ORIGINAL_EVALUATE_TEMPLATE_CONDITION
        self._template = v16._HIGH_LEVEL_GOLD_TEMPLATE
        v16._HIGH_LEVEL_GOLD_TEMPLATE = None

    def tearDown(self):
        v16._ORIGINAL_EVALUATE_TEMPLATE_CONDITION = self._original
        v16._HIGH_LEVEL_GOLD_TEMPLATE = self._template

    @staticmethod
    def _engine(*, three_team=True, template_matches=None):
        engine = SimpleNamespace(
            _rally_hot_path_three_team=three_team,
            _rally_v16_last_high_gold_log=0.0,
            log_messages=[],
        )
        engine.log = engine.log_messages.append
        engine._condition_matching_kwargs = lambda _cond: {}
        engine.find_calls = []

        def find(frame, template, confidence, **kwargs):
            engine.find_calls.append(
                {
                    "frame": frame,
                    "template_shape": tuple(template.shape),
                    "confidence": confidence,
                    "kwargs": dict(kwargs),
                }
            )
            return list(template_matches or [])

        engine._find_template_matches_in_frame = find
        engine._template_matches_to_runtime_matches = (
            lambda index, cond, matches, off_x, off_y: [
                {
                    "condition_index": index,
                    "center": (off_x + 47, off_y + 37),
                    "source_count": len(matches),
                }
            ]
            if matches
            else []
        )
        return engine

    @staticmethod
    def _cond(
        *,
        template_path="templates/GoldMob.png",
        negate=False,
        comparison_template_path="",
    ):
        return SimpleNamespace(
            template_path=template_path,
            negate=negate,
            comparison_template_path=comparison_template_path,
            confidence=0.85,
        )

    @staticmethod
    def _frame():
        return np.zeros((160, 240, 3), dtype=np.uint8)

    def test_embedded_high_level_gold_template_decodes(self):
        template = v16._high_level_gold_template()

        self.assertEqual(template.shape, (75, 95, 3))
        self.assertGreater(int(template.max()), 0)

    def test_existing_normal_gold_match_stays_authoritative(self):
        engine = self._engine(template_matches=[object()])
        original = [{"center": (100, 100)}]
        v16._ORIGINAL_EVALUATE_TEMPLATE_CONDITION = (
            lambda *_args, **_kwargs: (True, original)
        )

        result = v16._evaluate_template_condition(
            engine,
            0,
            self._cond(),
            self._frame(),
            0,
            0,
            True,
        )

        self.assertEqual(result, (True, original))
        self.assertEqual(engine.find_calls, [])

    def test_three_team_original_miss_accepts_high_level_gold_variant(self):
        marker = object()
        engine = self._engine(template_matches=[marker])
        v16._ORIGINAL_EVALUATE_TEMPLATE_CONDITION = (
            lambda *_args, **_kwargs: (False, [])
        )

        ok, matches = v16._evaluate_template_condition(
            engine,
            0,
            self._cond(),
            self._frame(),
            100,
            200,
            True,
        )

        self.assertTrue(ok)
        self.assertEqual(matches[0]["center"], (147, 237))
        self.assertEqual(len(engine.find_calls), 1)
        self.assertEqual(engine.find_calls[0]["template_shape"], (75, 95, 3))
        self.assertEqual(engine.find_calls[0]["confidence"], 0.85)
        self.assertTrue(
            any("Lv80+ GoldMob artwork matched" in line for line in engine.log_messages)
        )

    def test_high_level_variant_miss_keeps_original_failure(self):
        engine = self._engine(template_matches=[])
        v16._ORIGINAL_EVALUATE_TEMPLATE_CONDITION = (
            lambda *_args, **_kwargs: (False, [])
        )

        result = v16._evaluate_template_condition(
            engine,
            0,
            self._cond(),
            self._frame(),
            0,
            0,
            False,
        )

        self.assertEqual(result, (False, []))
        self.assertEqual(len(engine.find_calls), 1)

    def test_two_team_path_is_unchanged(self):
        engine = self._engine(three_team=False, template_matches=[object()])
        v16._ORIGINAL_EVALUATE_TEMPLATE_CONDITION = (
            lambda *_args, **_kwargs: (False, [])
        )

        result = v16._evaluate_template_condition(
            engine,
            0,
            self._cond(),
            self._frame(),
            0,
            0,
            True,
        )

        self.assertEqual(result, (False, []))
        self.assertEqual(engine.find_calls, [])

    def test_non_gold_condition_is_unchanged(self):
        engine = self._engine(template_matches=[object()])
        v16._ORIGINAL_EVALUATE_TEMPLATE_CONDITION = (
            lambda *_args, **_kwargs: (False, [])
        )

        result = v16._evaluate_template_condition(
            engine,
            1,
            self._cond(template_path="templates/Join.png"),
            self._frame(),
            0,
            0,
            True,
        )

        self.assertEqual(result, (False, []))
        self.assertEqual(engine.find_calls, [])

    def test_negated_or_competing_gold_condition_is_unchanged(self):
        v16._ORIGINAL_EVALUATE_TEMPLATE_CONDITION = (
            lambda *_args, **_kwargs: (False, [])
        )
        for cond in (
            self._cond(negate=True),
            self._cond(comparison_template_path="templates/Other.png"),
        ):
            with self.subTest(cond=cond):
                engine = self._engine(template_matches=[object()])
                result = v16._evaluate_template_condition(
                    engine,
                    0,
                    cond,
                    self._frame(),
                    0,
                    0,
                    True,
                )
                self.assertEqual(result, (False, []))
                self.assertEqual(engine.find_calls, [])


if __name__ == "__main__":
    unittest.main()
