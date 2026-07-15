import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

import numpy as np

from macro_clicker.diagnostics import (
    DEFAULT_STALE_TEMP_AGE_SECONDS,
    DiagnosticCollector,
)
from macro_clicker.engine import MacroEngine
from macro_clicker.models import Action, Scenario, Step


class DiagnosticCollectorTests(unittest.TestCase):
    @staticmethod
    def _event_dirs(folder):
        return sorted(
            path.parent
            for path in Path(folder).glob("*/*/metadata.json")
        )

    def test_event_contains_compressed_context_and_structured_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            collector = DiagnosticCollector(
                folder,
                synchronous=True,
                max_age_days=None,
            )

            event_path = collector.submit(
                "rally test",
                {"decision": "accepted", "center": (10, 20)},
                {"context": np.zeros((12, 18, 3), dtype=np.uint8)},
                force=True,
            )

            self.assertTrue(os.path.isfile(os.path.join(event_path, "context.jpg")))
            with open(os.path.join(event_path, "metadata.json"), encoding="utf-8") as handle:
                metadata = json.load(handle)
            self.assertEqual(metadata["event_type"], "rally test")
            self.assertEqual(metadata["decision"], "accepted")
            self.assertEqual(metadata["center"], [10, 20])
            self.assertEqual(metadata["category"], "critical")
            self.assertEqual(metadata["images"], ["context.jpg"])
            with open(os.path.join(folder, "decisions.jsonl"), encoding="utf-8") as handle:
                decision = json.loads(handle.readline())
            self.assertEqual(decision["decision"], "accepted")

    def test_retention_is_bounded_by_event_count(self):
        with tempfile.TemporaryDirectory() as folder:
            collector = DiagnosticCollector(
                folder,
                synchronous=True,
                max_events=2,
                max_age_days=None,
                max_bytes=None,
            )
            for index in range(4):
                collector.submit(
                    f"event-{index}",
                    {"index": index},
                    {"image": np.zeros((2, 2, 3), dtype=np.uint8)},
                    force=True,
                )

            event_dirs = self._event_dirs(folder)
            self.assertEqual(len(event_dirs), 2)

    def test_retention_keeps_separate_critical_and_sample_pools(self):
        with tempfile.TemporaryDirectory() as folder:
            collector = DiagnosticCollector(
                folder,
                synchronous=True,
                max_events=None,
                max_critical_events=2,
                max_sample_events=1,
                max_age_days=None,
                max_bytes=None,
            )
            for index in range(4):
                collector.submit(
                    f"critical-{index}",
                    {},
                    {"image": np.zeros((2, 2, 3), dtype=np.uint8)},
                    category="critical",
                    force=True,
                )
            for index in range(3):
                collector.submit(
                    f"sample-{index}",
                    {},
                    {"image": np.zeros((2, 2, 3), dtype=np.uint8)},
                    category="samples",
                    force=True,
                )

            categories = [path.parent.name for path in self._event_dirs(folder)]
            self.assertEqual(categories.count("critical"), 2)
            self.assertEqual(categories.count("samples"), 1)

    def test_cleanup_removes_only_stale_incomplete_event_directories(self):
        with tempfile.TemporaryDirectory() as folder:
            category = Path(folder) / "critical"
            stale_temp = category / ".stale-event.tmp"
            fresh_temp = category / ".active-event.tmp"
            unrelated_hidden = category / ".keep-me"
            for path in (stale_temp, fresh_temp, unrelated_hidden):
                path.mkdir(parents=True, exist_ok=True)
                (path / "partial.png").write_bytes(b"partial")
            stale_at = time.time() - DEFAULT_STALE_TEMP_AGE_SECONDS - 60
            os.utime(stale_temp, (stale_at, stale_at))
            os.utime(unrelated_hidden, (stale_at, stale_at))

            DiagnosticCollector(folder, synchronous=True, max_age_days=None)

            self.assertFalse(stale_temp.exists())
            self.assertTrue(fresh_temp.exists())
            self.assertTrue(unrelated_hidden.exists())

    def test_rate_limit_prevents_duplicate_events(self):
        with tempfile.TemporaryDirectory() as folder:
            collector = DiagnosticCollector(folder, synchronous=True)

            self.assertTrue(collector.should_capture("same", now=10.0))
            self.assertFalse(
                collector.should_capture("same", min_interval=5.0, now=12.0)
            )
            self.assertTrue(
                collector.should_capture("same", min_interval=5.0, now=16.0)
            )
            self.assertFalse(collector.should_capture("never", sample_rate=0.0))

    def test_perceptual_hash_deduplicates_similar_screenshots(self):
        with tempfile.TemporaryDirectory() as folder:
            collector = DiagnosticCollector(folder, synchronous=True)
            image = np.tile(np.arange(64, dtype=np.uint8), (64, 1))

            first = collector.submit(
                "near-miss",
                {},
                {"context": image},
                force=True,
                key="same-screen",
                dedupe_image=image,
                dedupe_window=300.0,
            )
            second = collector.submit(
                "near-miss",
                {},
                {"context": image.copy()},
                force=True,
                key="same-screen",
                dedupe_image=image.copy(),
                dedupe_window=300.0,
            )

            self.assertIsNotNone(first)
            self.assertIsNone(second)
            self.assertEqual(len(self._event_dirs(folder)), 1)

    def test_decision_log_rotates_to_bounded_backups(self):
        with tempfile.TemporaryDirectory() as folder:
            collector = DiagnosticCollector(
                folder,
                synchronous=True,
                decision_log_bytes=180,
                decision_log_backups=2,
            )
            for index in range(10):
                collector.record_decision(
                    "row-check",
                    {"index": index, "details": "x" * 80},
                )

            self.assertTrue(os.path.isfile(os.path.join(folder, "decisions.jsonl")))
            self.assertTrue(os.path.isfile(os.path.join(folder, "decisions.jsonl.1")))
            self.assertTrue(os.path.isfile(os.path.join(folder, "decisions.jsonl.2")))
            self.assertFalse(os.path.exists(os.path.join(folder, "decisions.jsonl.3")))

    def test_close_drains_queued_events_before_workers_exit(self):
        with tempfile.TemporaryDirectory() as folder:
            collector = DiagnosticCollector(folder, queue_size=4)
            write_started = threading.Event()
            release_write = threading.Event()
            original_write = collector._write

            def delayed_write(payload):
                write_started.set()
                release_write.wait(1.0)
                return original_write(payload)

            collector._write = delayed_write
            image = np.zeros((4, 4, 3), dtype=np.uint8)
            collector.submit("first", {}, {"image": image}, force=True)
            self.assertTrue(write_started.wait(1.0))
            collector.submit("second", {}, {"image": image}, force=True)
            threading.Timer(0.05, release_write.set).start()

            collector.close(timeout=2.0)

            self.assertEqual(len(self._event_dirs(folder)), 2)
            self.assertFalse(collector._worker.is_alive())
            self.assertFalse(collector._decision_worker.is_alive())
            self.assertIsNone(
                collector.submit("after-close", {}, {"image": image}, force=True)
            )
            self.assertIsNone(collector.record_decision("after-close", {}))

    def test_full_writer_queue_rolls_back_capture_rate_limit(self):
        with tempfile.TemporaryDirectory() as folder:
            collector = DiagnosticCollector(folder, queue_size=1)
            write_started = threading.Event()
            release_write = threading.Event()
            original_write = collector._write

            def delayed_write(payload):
                write_started.set()
                release_write.wait(1.0)
                return original_write(payload)

            collector._write = delayed_write
            image = np.zeros((4, 4, 3), dtype=np.uint8)
            collector.submit("blocking", {}, {"image": image}, force=True)
            self.assertTrue(write_started.wait(1.0))
            collector.submit("queued", {}, {"image": image}, force=True)

            dropped = collector.submit(
                "retry",
                {},
                {"image": image},
                key="retry-key",
                min_interval=300.0,
                log_decision=False,
            )
            self.assertIsNone(dropped)
            release_write.set()
            self.assertTrue(collector.flush(2.0))

            retried = collector.submit(
                "retry",
                {},
                {"image": image},
                key="retry-key",
                min_interval=300.0,
                log_decision=False,
            )
            self.assertIsNotNone(retried)
            collector.close(timeout=2.0)

    def test_reserved_metadata_fields_cannot_override_collector_fields(self):
        with tempfile.TemporaryDirectory() as folder:
            collector = DiagnosticCollector(folder, synchronous=True)
            event_path = collector.submit(
                "real-event",
                {
                    "schema_version": 99,
                    "event_type": "spoofed",
                    "category": "spoofed",
                    "images": ["spoofed.png"],
                },
                {"image": np.zeros((2, 2, 3), dtype=np.uint8)},
                force=True,
            )

            with open(os.path.join(event_path, "metadata.json"), encoding="utf-8") as handle:
                metadata = json.load(handle)
            self.assertEqual(metadata["schema_version"], 1)
            self.assertEqual(metadata["event_type"], "real-event")
            self.assertEqual(metadata["category"], "critical")
            self.assertEqual(metadata["images"], ["image.png"])

    def test_rally_event_combines_context_crops_and_decision_data(self):
        with tempfile.TemporaryDirectory() as folder:
            collector = DiagnosticCollector(folder, synchronous=True)
            engine = object.__new__(MacroEngine)
            engine.scenario = Scenario(name="Rally")
            engine.diagnostics_enabled = True
            engine._diagnostic_collector = collector
            engine._get_target_window_rect = lambda: (0, 0, 320, 200)
            engine._grab = lambda region: (
                np.zeros((region[3], region[2], 3), dtype=np.uint8),
                region[0],
                region[1],
            )
            reference = {"center": (80, 100), "box": (50, 70, 110, 130)}
            target = {"center": (250, 100), "box": (230, 80, 270, 120)}
            engine._last_level_diagnostics = {
                (80, 100): {
                    "decision": "strong_ocr",
                    "level": 45,
                    "selected_attempt_index": 0,
                    "attempts": [{
                        "ocr": {
                            "level": 45,
                            "text": "Lv.45",
                            "confidence": 0.98,
                        }
                    }],
                    "images": {
                        "crop_00_offset_0": np.zeros((45, 150, 3), dtype=np.uint8),
                    },
                }
            }
            step = Step(name="Joining")
            action = Action(
                type="click_matching_row",
                match_condition_index=0,
                on_condition_index=1,
                max_level=60,
                pre_click_delay=1.5,
            )

            engine._record_matching_row_diagnostic(
                step,
                action,
                [{"reference": reference, "target": target, "level": 45}],
                {0: [reference], 1: [target]},
                "eligible_before_delay",
            )

            event_path = self._event_dirs(folder)[0]
            with open(event_path / "metadata.json", encoding="utf-8") as handle:
                metadata = json.load(handle)
            self.assertEqual(metadata["decision"], "eligible_before_delay")
            self.assertEqual(metadata["action"]["max_level"], 60)
            self.assertEqual(metadata["level_reads"][0]["level"], 45)
            self.assertEqual(metadata["category"], "samples")
            self.assertIn("context_annotated.jpg", metadata["images"])
            self.assertIn("row_0_crop_00_offset_0.png", metadata["images"])

            engine._record_matching_row_diagnostic(
                step,
                action,
                [{"reference": reference, "target": target, "level": 45}],
                {0: [reference], 1: [target]},
                "eligible_before_delay",
            )
            self.assertEqual(len(self._event_dirs(folder)), 1)
            with open(os.path.join(folder, "decisions.jsonl"), encoding="utf-8") as handle:
                decisions = [json.loads(line) for line in handle]
            self.assertEqual(len(decisions), 2)
            self.assertTrue(decisions[0]["screenshot_policy"]["selected"])
            self.assertFalse(decisions[1]["screenshot_policy"]["selected"])

    def test_low_confidence_success_is_classified_as_critical(self):
        engine = object.__new__(MacroEngine)
        policy = engine._matching_row_diagnostic_policy(
            "eligible_before_delay",
            [{
                "decision": "strong_ocr",
                "selected_attempt_index": 0,
                "attempts": [{"ocr": {"confidence": 0.94}}],
            }],
            0.0,
        )

        self.assertEqual(policy["category"], "critical")
        self.assertEqual(policy["min_interval"], 0.0)

if __name__ == "__main__":
    unittest.main()
