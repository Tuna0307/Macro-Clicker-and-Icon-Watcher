import queue
import unittest

from macro_clicker.activity_clear_runtime import clear_activity_view


class _FakeText:
    def __init__(self):
        self.delete_calls = []

    def delete(self, start, end):
        self.delete_calls.append((start, end))


class _FakeApp:
    def __init__(self):
        self.log_text = _FakeText()
        self.log_queue = queue.Queue()
        self.log_queue.put("old line 1")
        self.log_queue.put("old line 2")
        self._log_line_count = 2
        self.engine = object()
        self.disk_lines = []

    def _write_log_file(self, message):
        self.disk_lines.append(message)


class ActivityClearRuntimeTests(unittest.TestCase):
    def test_clear_activity_view_clears_visible_and_queued_lines_only(self):
        app = _FakeApp()
        engine_before = app.engine

        clear_activity_view(app)

        self.assertEqual(app.log_text.delete_calls, [("1.0", "end")])
        self.assertEqual(app._log_line_count, 0)
        self.assertTrue(app.log_queue.empty())
        self.assertIs(app.engine, engine_before)
        self.assertEqual(app.disk_lines, ["---- activity view cleared ----"])

    def test_clear_activity_view_is_safe_when_queue_is_already_empty(self):
        app = _FakeApp()
        while not app.log_queue.empty():
            app.log_queue.get_nowait()

        clear_activity_view(app)

        self.assertTrue(app.log_queue.empty())
        self.assertEqual(app._log_line_count, 0)
        self.assertEqual(app.log_text.delete_calls, [("1.0", "end")])


if __name__ == "__main__":
    unittest.main()
