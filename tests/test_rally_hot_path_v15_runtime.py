import unittest
from types import SimpleNamespace

from macro_clicker import rally_hot_path_v15_runtime as v15


class RallyHotPathV15RuntimeTests(unittest.TestCase):
    def setUp(self):
        self._original = v15._ORIGINAL_EVALUATE_LOCAL_TARGET

    def tearDown(self):
        v15._ORIGINAL_EVALUATE_LOCAL_TARGET = self._original

    @staticmethod
    def _engine(*, three_team=True):
        engine = SimpleNamespace(
            _rally_hot_path_three_team=three_team,
            log_messages=[],
        )
        engine.log = engine.log_messages.append
        return engine

    @staticmethod
    def _action():
        return SimpleNamespace(
            type="click_matching_row",
            match_condition_index=0,
            on_condition_index=1,
        )

    @classmethod
    def _step(cls, *, name="Joining"):
        return SimpleNamespace(name=name, actions=[cls._action()])

    @staticmethod
    def _call(engine, step, matches):
        return v15._evaluate_matching_row_target_locally(
            engine,
            step,
            1,
            SimpleNamespace(),
            matches,
            {},
            collect_all=True,
        )

    def test_three_team_gold_reference_without_same_row_plus_is_pass_with_no_target(self):
        engine = self._engine()
        gold_reference = {"center": (780, 560)}
        v15._ORIGINAL_EVALUATE_LOCAL_TARGET = lambda *_args, **_kwargs: (False, [])

        result = self._call(engine, self._step(), {0: [gold_reference]})

        self.assertEqual(result, (True, []))
        self.assertTrue(
            any("no same-row Join +" in line for line in engine.log_messages)
        )

    def test_non_gold_row_plus_cannot_make_target_eligible_when_local_search_is_empty(self):
        engine = self._engine()
        # The local evaluator has already ignored targets outside the GoldMob
        # row band.  v15 must keep that result empty rather than substitute a
        # global/non-gold-row target.
        gold_reference = {"center": (780, 560)}
        v15._ORIGINAL_EVALUATE_LOCAL_TARGET = lambda *_args, **_kwargs: (False, [])

        ok, targets = self._call(engine, self._step(), {0: [gold_reference]})

        self.assertTrue(ok)
        self.assertEqual(targets, [])

    def test_existing_same_row_target_result_is_unchanged(self):
        engine = self._engine()
        target = {"center": (950, 587)}
        v15._ORIGINAL_EVALUATE_LOCAL_TARGET = lambda *_args, **_kwargs: (
            True,
            [target],
        )

        result = self._call(
            engine,
            self._step(),
            {0: [{"center": (780, 560)}]},
        )

        self.assertEqual(result, (True, [target]))
        self.assertEqual(engine.log_messages, [])

    def test_missing_gold_reference_keeps_original_failure(self):
        engine = self._engine()
        v15._ORIGINAL_EVALUATE_LOCAL_TARGET = lambda *_args, **_kwargs: (False, [])

        result = self._call(engine, self._step(), {0: []})

        self.assertEqual(result, (False, []))
        self.assertEqual(engine.log_messages, [])

    def test_two_team_path_is_unchanged(self):
        engine = self._engine(three_team=False)
        v15._ORIGINAL_EVALUATE_LOCAL_TARGET = lambda *_args, **_kwargs: (False, [])

        result = self._call(
            engine,
            self._step(),
            {0: [{"center": (780, 560)}]},
        )

        self.assertEqual(result, (False, []))
        self.assertEqual(engine.log_messages, [])

    def test_unrelated_step_is_unchanged(self):
        engine = self._engine()
        v15._ORIGINAL_EVALUATE_LOCAL_TARGET = lambda *_args, **_kwargs: (False, [])

        result = self._call(
            engine,
            self._step(name="Other step"),
            {0: [{"center": (780, 560)}]},
        )

        self.assertEqual(result, (False, []))
        self.assertEqual(engine.log_messages, [])


if __name__ == "__main__":
    unittest.main()
