import json
import os
import queue
import tempfile
import time
import unittest
from unittest.mock import Mock, patch, sentinel

import cv2
import numpy as np

import alert_watcher as watcher
import window_locator


class FakeWindow:
    def __init__(self, title, left=0, top=0, width=100, height=100):
        self.title = title
        self.left = left
        self.top = top
        self.width = width
        self.height = height


class InvalidWindow:
    @property
    def title(self):
        raise RuntimeError("Invalid window handle")


class TemplateManagerTests(unittest.TestCase):
    def _manager_in_temp_dir(self):
        temp_dir = tempfile.TemporaryDirectory()
        templates_dir = os.path.join(temp_dir.name, "templates")
        os.makedirs(templates_dir, exist_ok=True)
        manifest_path = os.path.join(templates_dir, "manifest.json")
        patchers = [
            patch.object(watcher, "TEMPLATES_DIR", templates_dir),
            patch.object(watcher, "MANIFEST_PATH", manifest_path),
        ]
        for patcher in patchers:
            patcher.start()
        self.addCleanup(lambda: [patcher.stop() for patcher in reversed(patchers)])
        self.addCleanup(temp_dir.cleanup)
        return watcher.TemplateManager()

    def test_snapshot_is_safe_to_read_while_manager_changes(self):
        tm = self._manager_in_temp_dir()
        image = np.zeros((8, 8, 3), dtype=np.uint8)

        tid = tm.add(image, "temporary")
        snapshot = tm.snapshot()
        tm.remove(tid)

        self.assertIn(tid, {item["id"] for item in snapshot})
        self.assertNotIn(tid, tm.items)

    def test_template_region_is_saved_in_snapshots(self):
        tm = self._manager_in_temp_dir()
        image = np.zeros((8, 8, 3), dtype=np.uint8)
        tid = tm.add(image, "temporary-region")

        tm.set_region(
            tid,
            region=(10, 20, 30, 40),
            region_mode="window",
            region_ratio=(0.1, 0.2, 0.3, 0.4),
            region_window_size=(100, 100),
        )
        item = next(item for item in tm.snapshot() if item["id"] == tid)

        self.assertEqual(item["region"], (10, 20, 30, 40))
        self.assertEqual(item["region_mode"], "window")
        self.assertEqual(item["region_ratio"], (0.1, 0.2, 0.3, 0.4))
        self.assertEqual(item["region_window_size"], (100, 100))

    def test_snapshot_reuses_prepared_template_variants(self):
        tm = self._manager_in_temp_dir()
        image = np.zeros((24, 24, 3), dtype=np.uint8)
        image[6:18, 6:18] = (255, 255, 255)
        tm.add(image, "cached")

        first = tm.snapshot(use_grayscale=True)[0]
        second = tm.snapshot(use_grayscale=True)[0]

        self.assertIs(first["variants"], second["variants"])
        self.assertGreater(len(first["variants"]), 1)

    def test_manifest_path_escape_is_ignored_and_cannot_delete_outside_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            templates_dir = os.path.join(temp_dir, "templates")
            os.makedirs(templates_dir)
            outside_path = os.path.join(temp_dir, "outside.png")
            cv2.imwrite(outside_path, np.zeros((8, 8, 3), dtype=np.uint8))
            manifest_path = os.path.join(templates_dir, "manifest.json")
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump({
                    "items": [{
                        "id": 1,
                        "name": "unsafe",
                        "file": "../outside.png",
                        "threshold": 0.8,
                    }]
                }, f)

            with patch.object(watcher, "TEMPLATES_DIR", templates_dir), \
                    patch.object(watcher, "MANIFEST_PATH", manifest_path):
                tm = watcher.TemplateManager()
                tm.remove(1)

            self.assertEqual(tm.snapshot(), [])
            self.assertTrue(os.path.exists(outside_path))
            self.assertTrue(any("escapes" in message for message in tm.load_warnings))

    def test_corrupt_manifest_shape_falls_back_to_empty_template_list(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            templates_dir = os.path.join(temp_dir, "templates")
            os.makedirs(templates_dir)
            manifest_path = os.path.join(templates_dir, "manifest.json")
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(None, f)

            with patch.object(watcher, "TEMPLATES_DIR", templates_dir), \
                    patch.object(watcher, "MANIFEST_PATH", manifest_path):
                tm = watcher.TemplateManager()

            self.assertEqual(tm.snapshot(), [])
            self.assertTrue(tm.load_warnings)

    def test_add_skips_unlisted_existing_template_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            templates_dir = os.path.join(temp_dir, "templates")
            os.makedirs(templates_dir)
            existing_path = os.path.join(templates_dir, "template_14.png")
            existing = np.full((8, 8, 3), 77, dtype=np.uint8)
            cv2.imwrite(existing_path, existing)
            manifest_path = os.path.join(templates_dir, "manifest.json")

            with patch.object(watcher, "TEMPLATES_DIR", templates_dir), \
                    patch.object(watcher, "MANIFEST_PATH", manifest_path):
                tm = watcher.TemplateManager()
                tid = tm.add(np.zeros((8, 8, 3), dtype=np.uint8), "new")

            self.assertEqual(tid, 15)
            np.testing.assert_array_equal(cv2.imread(existing_path), existing)

    def test_failed_manifest_save_rolls_back_new_template_image(self):
        tm = self._manager_in_temp_dir()

        with patch.object(tm, "_save", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                tm.add(np.zeros((8, 8, 3), dtype=np.uint8), "rollback")

        self.assertEqual(tm.snapshot(), [])
        self.assertEqual(
            [name for name in os.listdir(watcher.TEMPLATES_DIR) if name.endswith(".png")],
            [],
        )


class DetectionTests(unittest.TestCase):
    def test_matching_finds_smaller_icon_with_small_rotation(self):
        icon = np.zeros((48, 58, 3), dtype=np.uint8)
        cv2.rectangle(icon, (8, 8), (50, 40), (40, 180, 240), -1)
        cv2.circle(icon, (20, 20), 8, (220, 60, 30), -1)
        cv2.line(icon, (8, 40), (50, 8), (255, 255, 255), 2)
        smaller = cv2.resize(
            icon,
            (int(icon.shape[1] * 0.65), int(icon.shape[0] * 0.65)),
            interpolation=cv2.INTER_AREA,
        )
        matrix = cv2.getRotationMatrix2D((smaller.shape[1] / 2, smaller.shape[0] / 2), 5, 1.0)
        rotated = cv2.warpAffine(
            smaller,
            matrix,
            (smaller.shape[1], smaller.shape[0]),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_REPLICATE,
        )
        screen = np.zeros((120, 160, 3), dtype=np.uint8)
        screen[40:40 + rotated.shape[0], 50:50 + rotated.shape[1]] = rotated

        score, loc, scale = watcher.match_template_multiscale(screen, icon, use_grayscale=False)

        self.assertGreaterEqual(score, 0.95)
        self.assertEqual(loc, (50, 40))
        self.assertAlmostEqual(scale, 0.65)

    def test_test_detection_on_screenshot_returns_best_match(self):
        screen = np.zeros((60, 60, 3), dtype=np.uint8)
        icon = np.zeros((12, 12, 3), dtype=np.uint8)
        icon[:, :, 0] = 255
        screen[25:37, 30:42] = icon

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp:
            path = temp.name
        try:
            cv2.imwrite(path, screen)
            results = watcher.test_detection_on_screenshot(
                path,
                [{"id": 1, "name": "blue", "threshold": 0.85, "image": icon}],
                use_grayscale=True,
            )
        finally:
            os.remove(path)

        self.assertEqual(results[0]["name"], "blue")
        self.assertTrue(results[0]["matched"])
        self.assertGreaterEqual(results[0]["score"], 0.99)

    def test_matching_can_use_prepared_template_variants(self):
        screen = np.zeros((60, 60, 3), dtype=np.uint8)
        icon = np.zeros((12, 12, 3), dtype=np.uint8)
        icon[:, :, 1] = 255
        screen[25:37, 30:42] = icon
        variants = watcher.prepare_template_variants(
            icon,
            scales=[1.0],
            rotations=[0],
            use_grayscale=True,
        )

        score, loc, scale = watcher.match_template_multiscale(
            screen,
            icon,
            use_grayscale=True,
            variants=variants,
        )

        self.assertGreaterEqual(score, 0.99)
        self.assertEqual(loc, (30, 25))
        self.assertEqual(scale, 1.0)

    def test_screenshot_test_uses_item_region_origin_and_cached_variants(self):
        screen = np.zeros((80, 80, 3), dtype=np.uint8)
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp:
            path = temp.name
        cv2.imwrite(path, screen)
        try:
            with patch.object(
                watcher,
                "match_template_multiscale",
                return_value=(0.9, (32, 43), 1.0),
            ) as match:
                results = watcher.test_detection_on_screenshot(
                    path,
                    [{
                        "id": 2,
                        "name": "region icon",
                        "threshold": 0.8,
                        "image": np.zeros((4, 4, 3), dtype=np.uint8),
                        "region": (130, 240, 20, 25),
                        "region_mode": "screen",
                        "variants": sentinel.cached_variants,
                    }],
                    region=(100, 200, 70, 70),
                    region_origin=(100, 200),
                )
        finally:
            os.remove(path)

        self.assertTrue(results[0]["matched"])
        self.assertEqual(match.call_args.kwargs["region"], (30, 40, 20, 25))
        self.assertIs(match.call_args.kwargs["variants"], sentinel.cached_variants)

    def test_cancel_event_stops_between_template_variants(self):
        screen = np.zeros((30, 30, 3), dtype=np.uint8)
        icon = np.zeros((5, 5, 3), dtype=np.uint8)
        variants = watcher.prepare_template_variants(icon, scales=[1.0, 1.1], rotations=[0])
        cancelled = watcher.threading.Event()
        cancelled.set()

        with patch.object(watcher.cv2, "matchTemplate") as match:
            score, loc, _scale = watcher.match_template_multiscale(
                screen,
                icon,
                variants=variants,
                cancel_event=cancelled,
            )

        self.assertEqual(score, -1.0)
        self.assertIsNone(loc)
        match.assert_not_called()

    def test_flat_colored_template_rejects_ambiguous_solid_screen(self):
        icon = np.full((10, 12, 3), (20, 90, 210), dtype=np.uint8)
        screen = np.full((60, 70, 3), (20, 90, 210), dtype=np.uint8)

        score, loc, _scale = watcher.match_template_multiscale(
            screen, icon, scales=[1.0], rotations=[0]
        )

        self.assertEqual(score, -1.0)
        self.assertIsNone(loc)

    def test_large_capture_coarse_search_returns_verified_pixel_location(self):
        rng = np.random.default_rng(107)
        icon = rng.integers(0, 256, (24, 30, 3), dtype=np.uint8)
        screen = np.zeros((600, 1000, 3), dtype=np.uint8)
        screen[417:441, 709:739] = icon

        score, loc, scale = watcher.match_template_multiscale(
            screen, icon, use_grayscale=True, early_exit_score=0.9
        )

        self.assertGreaterEqual(score, 0.99)
        self.assertEqual(loc, (709, 417))
        self.assertEqual(scale, 1.0)


class TemplateStateTests(unittest.TestCase):
    def test_alerts_once_until_disarmed_and_respects_cooldown(self):
        state = watcher.TemplateState(threshold=0.8, hysteresis=0.05, cooldown_sec=1.0)
        now = time.monotonic()

        self.assertTrue(state.update(0.9, now=now))
        self.assertFalse(state.update(0.9, now=now + 0.2))
        self.assertFalse(state.update(0.7, now=now + 0.3))
        self.assertFalse(state.update(0.9, now=now + 0.4))
        self.assertTrue(state.update(0.9, now=now + 1.1))


class WatcherThreadTests(unittest.TestCase):
    @staticmethod
    def _template_item(tid=1, name="icon"):
        return {
            "id": tid,
            "name": name,
            "threshold": 0.8,
            "region": None,
            "region_mode": "screen",
            "region_ratio": None,
            "region_window_size": None,
            "image": np.zeros((5, 5, 3), dtype=np.uint8),
            "variants": (),
        }

    def test_multi_monitor_scores_update_template_state_once_per_cycle(self):
        item = self._template_item()

        class FakeManager:
            def snapshot(self, use_grayscale=None):
                return [item]

        class FakeCapture:
            monitors = [
                {"left": 0, "top": 0, "width": 20, "height": 10},
                {"left": 0, "top": 0, "width": 10, "height": 10},
                {"left": 10, "top": 0, "width": 10, "height": 10},
            ]

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def grab(self, _monitor):
                return np.zeros((10, 10, 4), dtype=np.uint8)

        events = queue.Queue()
        thread = watcher.WatcherThread(
            FakeManager(),
            events,
            queue.Queue(),
            cooldown_sec=0.0,
        )
        waits = []

        def finish_after_two_cycles():
            waits.append(True)
            if len(waits) == 2:
                thread.stop()

        thread._wait_for_next_cycle = finish_after_two_cycles
        scores = iter((0.92, 0.10, 0.92, 0.10))
        with patch.object(watcher.mss, "MSS", return_value=FakeCapture()), \
                patch.object(
                    watcher,
                    "match_template_multiscale",
                    side_effect=lambda *_args, **_kwargs: (next(scores), (1, 1), 1.0),
                ):
            thread.run()

        alerts = [event for event in watcher._drain_queue(events) if "id" in event]
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["monitor"], 1)
        self.assertAlmostEqual(alerts[0]["score"], 0.92)

    def test_partial_scan_can_activate_but_cannot_disarm_template(self):
        item = self._template_item()
        events = queue.Queue()
        thread = watcher.WatcherThread(Mock(), events, queue.Queue(), cooldown_sec=0.0)
        thread._sync_states([item], cooldown_sec=0.0)

        thread._emit_aggregated_matches(
            [item], {1: (0.91, 2)}, now=10.0, complete_ids=set()
        )
        self.assertTrue(thread.states[1].active)
        self.assertEqual(events.get_nowait()["monitor"], 2)

        thread._emit_aggregated_matches(
            [item], {1: (-1.0, None)}, now=11.0, complete_ids=set()
        )
        self.assertTrue(thread.states[1].active)

        thread._emit_aggregated_matches(
            [item], {1: (-1.0, None)}, now=12.0, complete_ids={1}
        )
        self.assertFalse(thread.states[1].active)

    def test_stop_is_checked_before_scanning_the_next_template(self):
        items = [self._template_item(1, "first"), self._template_item(2, "second")]

        class FakeManager:
            def snapshot(self, use_grayscale=None):
                return items

        class FakeCapture:
            monitors = [
                {"left": 0, "top": 0, "width": 10, "height": 10},
                {"left": 0, "top": 0, "width": 10, "height": 10},
            ]

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def grab(self, _monitor):
                return np.zeros((10, 10, 4), dtype=np.uint8)

        thread = watcher.WatcherThread(FakeManager(), queue.Queue(), queue.Queue())
        calls = []

        def stop_during_first_match(*_args, **_kwargs):
            calls.append(True)
            thread.stop()
            return 0.9, (1, 1), 1.0

        with patch.object(watcher.mss, "MSS", return_value=FakeCapture()), \
                patch.object(
                    watcher,
                    "match_template_multiscale",
                    side_effect=stop_during_first_match,
                ):
            thread.run()

        self.assertEqual(len(calls), 1)

    def test_live_config_update_wakes_watcher_and_changes_runtime_snapshot(self):
        thread = watcher.WatcherThread(Mock(), queue.Queue(), queue.Queue())

        thread.update_config(
            monitor_filter=2,
            scan_region=(1, 2, 30, 40),
            scan_region_mode="window",
            scan_region_ratio=(0.1, 0.2, 0.3, 0.4),
            scan_region_window_size=(100, 100),
            target_window_title=" Game ",
            use_grayscale=False,
            debug=True,
            cooldown_sec=2.5,
        )

        config = thread._config_snapshot()
        self.assertEqual(config["monitor_filter"], 2)
        self.assertEqual(config["scan_region"], (1, 2, 30, 40))
        self.assertEqual(config["target_window_title"], "Game")
        self.assertFalse(config["use_grayscale"])
        self.assertTrue(config["debug"])
        self.assertEqual(config["cooldown_sec"], 2.5)
        self.assertTrue(thread._wake_flag.is_set())


class WatcherFrameLifecycleTests(unittest.TestCase):
    class FakeControl:
        def __init__(self):
            self.options = {}

        def config(self, **kwargs):
            self.options.update(kwargs)

    def test_stop_retains_reference_while_watcher_is_still_alive(self):
        class SlowWatcher:
            def __init__(self):
                self.stop_called = False
                self.join_timeout = None

            def stop(self):
                self.stop_called = True

            def join(self, timeout=None):
                self.join_timeout = timeout

            def is_alive(self):
                return True

        frame = object.__new__(watcher.AlertWatcherFrame)
        slow = SlowWatcher()
        frame.watcher = slow
        frame.start_btn = self.FakeControl()
        frame.stop_btn = self.FakeControl()
        frame.status_label = self.FakeControl()
        frame._append_log = Mock()

        stopped = frame._stop_watching()

        self.assertFalse(stopped)
        self.assertIs(frame.watcher, slow)
        self.assertTrue(slow.stop_called)
        self.assertEqual(slow.join_timeout, 2.0)
        self.assertEqual(frame.status_label.options["text"], "Stopping…")
        self.assertEqual(frame.start_btn.options["state"], "disabled")

    def test_start_does_not_overlap_a_live_watcher(self):
        frame = object.__new__(watcher.AlertWatcherFrame)
        frame.tm = Mock()
        frame.tm.snapshot.return_value = [{"id": 1}]
        frame.watcher = Mock()
        frame.watcher.is_alive.return_value = True
        frame._append_log = Mock()

        frame._start_watching()

        frame._append_log.assert_called_once_with("Watcher is already running or still stopping.")

    def test_hotkey_callback_queues_ui_work_without_calling_tk(self):
        frame = object.__new__(watcher.AlertWatcherFrame)
        frame.event_queue = queue.Queue()
        frame.after = Mock(side_effect=AssertionError("Tk must not be called from hotkey thread"))

        frame._toggle_watching_from_hotkey()

        self.assertEqual(
            frame.event_queue.get_nowait(),
            {"type": "ui_command", "command": "toggle"},
        )

    def test_finish_app_quit_destroys_toplevel_not_only_frame(self):
        frame = object.__new__(watcher.AlertWatcherFrame)
        root = Mock()
        frame.watcher = None
        frame._destroy_scheduled = False
        frame.winfo_toplevel = Mock(return_value=root)
        frame.after_idle = lambda callback: callback()

        frame._finish_app_quit()

        root.destroy.assert_called_once_with()


class SettingsTests(unittest.TestCase):
    def test_settings_round_trip_preserves_user_options(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "settings.json")
            settings = watcher.AppSettings(
                monitor_choice="Monitor 2",
                grayscale=False,
                debug=True,
                cooldown_sec=7.5,
                scan_region=(1, 2, 30, 40),
                scan_region_mode="window",
                scan_region_ratio=(0.1, 0.2, 0.3, 0.4),
                scan_region_window_size=(300, 100),
                target_window_title="Game Window",
                start_stop_hotkey="ctrl+shift+f8",
                test_alert_hotkey="ctrl+shift+f9",
                minimize_to_tray=True,
                alert_volume=0.42,
            )

            watcher.save_settings(path, settings)
            loaded = watcher.load_settings(path)

        self.assertEqual(loaded, settings)

    def test_alert_volume_is_clamped_when_loading_settings(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "settings.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"alert_volume": 1.8}, f)

            loaded = watcher.load_settings(path)

        self.assertEqual(loaded.alert_volume, 1.0)

    def test_non_object_settings_json_uses_defaults(self):
        for invalid_data in (None, 7, ["not", "settings"]):
            with self.subTest(invalid_data=invalid_data), tempfile.TemporaryDirectory() as temp_dir:
                path = os.path.join(temp_dir, "settings.json")
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(invalid_data, f)

                loaded = watcher.load_settings(path)

            self.assertEqual(loaded, watcher.AppSettings())

    def test_nonfinite_and_wrong_type_settings_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "settings.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({
                    "cooldown_sec": float("nan"),
                    "alert_volume": float("inf"),
                    "grayscale": "yes",
                    "scan_region": [1, 2, -3, 4],
                    "target_window_title": 123,
                }, f)

            loaded = watcher.load_settings(path)

        self.assertEqual(loaded.cooldown_sec, watcher.DEFAULT_COOLDOWN_SEC)
        self.assertEqual(loaded.alert_volume, watcher.DEFAULT_ALERT_VOLUME)
        self.assertTrue(loaded.grayscale)
        self.assertIsNone(loaded.scan_region)
        self.assertEqual(loaded.target_window_title, "")


class SoundTests(unittest.TestCase):
    def test_play_alert_sound_uses_pygame_volume(self):
        class FakeThread:
            def __init__(self, target, daemon):
                self.target = target
                self.daemon = daemon

            def start(self):
                self.target()

        class FakeSound:
            def __init__(self, *args, **kwargs):
                self.volume = None

            def set_volume(self, volume):
                self.volume = volume
                fake_pygame.last_sound = self

            def play(self):
                fake_pygame.play_called = True

        fake_pygame = Mock()
        fake_pygame.mixer.get_init.return_value = True
        fake_pygame.mixer.Sound.side_effect = FakeSound
        fake_pygame.play_called = False
        fake_pygame.last_sound = None

        with patch.object(watcher, "HAVE_PYGAME", True), \
                patch.object(watcher, "pygame", fake_pygame), \
                patch.object(watcher, "threading") as threading_module:
            threading_module.Thread.side_effect = FakeThread

            watcher.play_alert_sound(volume=0.37)

        self.assertTrue(fake_pygame.play_called)
        self.assertAlmostEqual(fake_pygame.last_sound.volume, 0.37)

    def test_sound_requests_use_one_worker_and_coalesce_pending_alerts(self):
        workers = []

        class DeferredThread:
            def __init__(self, target, daemon):
                self.target = target
                self.daemon = daemon
                workers.append(self)

            def start(self):
                pass

        watcher._SOUND_THREAD = None
        watcher._PENDING_SOUND_VOLUME = None
        self.addCleanup(setattr, watcher, "_SOUND_THREAD", None)
        self.addCleanup(setattr, watcher, "_PENDING_SOUND_VOLUME", None)
        with patch.object(watcher.threading, "Thread", DeferredThread), \
                patch.object(watcher, "_play_alert_once") as play_once:
            watcher.play_alert_sound(0.2)
            watcher.play_alert_sound(0.3)
            watcher.play_alert_sound(0.4)

            self.assertEqual(len(workers), 1)
            workers[0].target()

        play_once.assert_called_once_with(0.4)
        self.assertIsNone(watcher._SOUND_THREAD)
        self.assertIsNone(watcher._PENDING_SOUND_VOLUME)


class WindowRegionTests(unittest.TestCase):
    def test_visible_window_titles_skips_invalid_window_handles(self):
        titles = window_locator.visible_window_titles(
            window_provider=lambda: [
                InvalidWindow(),
                FakeWindow("Last War-Survival Game", width=800, height=600),
            ]
        )

        self.assertEqual(titles, ["Last War-Survival Game"])

    def test_find_window_rect_skips_invalid_window_handles(self):
        rect = window_locator.find_window_rect(
            "Last War",
            window_provider=lambda: [
                InvalidWindow(),
                FakeWindow("Last War-Survival Game", left=10, top=20, width=800, height=600),
            ],
        )

        self.assertEqual(rect, (10, 20, 800, 600))

    def test_resolves_window_relative_region_after_resize_with_ratio(self):
        wt = watcher.WatcherThread(
            watcher.TemplateManager(),
            queue.Queue(),
            queue.Queue(),
            scan_region=(80, 120, 200, 120),
            scan_region_mode="window",
            scan_region_ratio=(0.1, 0.2, 0.25, 0.2),
            scan_region_window_size=(800, 600),
            target_window_title="Game",
            window_rect_provider=lambda _title: (300, 400, 1600, 1200),
        )

        self.assertEqual(wt._resolve_absolute_scan_region(), (460, 640, 400, 240))

    def test_window_item_region_is_unavailable_when_target_window_is_missing(self):
        logs = queue.Queue()
        wt = watcher.WatcherThread(
            watcher.TemplateManager(),
            queue.Queue(),
            logs,
            target_window_title="Missing Game",
            window_rect_provider=lambda _title: None,
        )
        item = {
            "region": (10, 20, 30, 40),
            "region_mode": "window",
            "region_ratio": None,
            "region_window_size": None,
        }

        self.assertIs(wt._resolve_item_scan_region(item, None), watcher.REGION_UNAVAILABLE)
        self.assertIn("Target window not found", logs.get_nowait())


class SingleInstanceTests(unittest.TestCase):
    def test_single_instance_lock_reports_existing_lock(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "app.lock")
            first = watcher.SingleInstanceLock(path)
            second = watcher.SingleInstanceLock(path)
            try:
                self.assertTrue(first.acquire())
                self.assertFalse(second.acquire())
                second.release()
                self.assertTrue(os.path.exists(path))
            finally:
                first.release()


if __name__ == "__main__":
    unittest.main()
