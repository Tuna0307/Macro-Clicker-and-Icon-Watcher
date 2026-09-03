import unittest
from types import SimpleNamespace

from macro_clicker import rally_hot_path_v13_runtime as v13


class RallyHotPathV13RuntimeTests(unittest.TestCase):
    def setUp(self):
        self._original_no_match = v13._ORIGINAL_NO_MATCH_FALLBACK

    def tearDown(self):
        v13._ORIGINAL_NO_MATCH_FALLBACK = self._original_no_match

    @staticmethod
    def _engine(*, three_team=True, latched=True, retry=False):
        engine = SimpleNamespace(
            _rally_hot_path_three_team=three_team,
            _rally_hot_entry_latched=latched,
            _retry_current_step=retry,
            log_messages=[],
        )
        engine.log = engine.log_messages.append
        return engine

    @staticmethod
    def _step(*, name="Joining", template_path="templates/BackButton.png"):
        return SimpleNamespace(
            name=name,
            conditions=[SimpleNamespace(template_path=template_path)],
        )

    @staticmethod
    def _action(*, action_type="click_matching_row", fallback_index=0):
        return SimpleNamespace(
            type=action_type,
            no_match_condition_index=fallback_index,
        )

    def test_successful_three_team_joining_back_fallback_releases_entry_latch(self):
        engine = self._engine()
        step = self._step()
        action = self._action()
        v13._ORIGINAL_NO_MATCH_FALLBACK = lambda *_args: True

        result = v13._run_no_match_fallback(engine, step, action, {})

        self.assertTrue(result)
        self.assertFalse(engine._rally_hot_entry_latched)
        self.assertTrue(
            any("Rally entry latch released" in line for line in engine.log_messages)
        )

    def test_failed_back_fallback_keeps_entry_latch(self):
        engine = self._engine()
        v13._ORIGINAL_NO_MATCH_FALLBACK = lambda *_args: False

        result = v13._run_no_match_fallback(
            engine,
            self._step(),
            self._action(),
            {},
        )

        self.assertFalse(result)
        self.assertTrue(engine._rally_hot_entry_latched)
        self.assertEqual(engine.log_messages, [])

    def test_retrying_fallback_keeps_entry_latch_fail_closed(self):
        engine = self._engine(retry=True)
        v13._ORIGINAL_NO_MATCH_FALLBACK = lambda *_args: True

        result = v13._run_no_match_fallback(
            engine,
            self._step(),
            self._action(),
            {},
        )

        self.assertTrue(result)
        self.assertTrue(engine._rally_hot_entry_latched)

    def test_non_back_joining_fallback_does_not_release_latch(self):
        engine = self._engine()
        v13._ORIGINAL_NO_MATCH_FALLBACK = lambda *_args: True

        v13._run_no_match_fallback(
            engine,
            self._step(template_path="templates/Join.png"),
            self._action(),
            {},
        )

        self.assertTrue(engine._rally_hot_entry_latched)

    def test_two_team_path_is_unchanged(self):
        engine = self._engine(three_team=False)
        v13._ORIGINAL_NO_MATCH_FALLBACK = lambda *_args: True

        result = v13._run_no_match_fallback(
            engine,
            self._step(),
            self._action(),
            {},
        )

        self.assertTrue(result)
        self.assertTrue(engine._rally_hot_entry_latched)
        self.assertEqual(engine.log_messages, [])


if __name__ == "__main__":
    unittest.main()
