import json
import os
import queue
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

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


class TemplateStateTests(unittest.TestCase):
    def test_alerts_once_until_disarmed_and_respects_cooldown(self):
        state = watcher.TemplateState(threshold=0.8, hysteresis=0.05, cooldown_sec=1.0)
        now = time.monotonic()

        self.assertTrue(state.update(0.9, now=now))
        self.assertFalse(state.update(0.9, now=now + 0.2))
        self.assertFalse(state.update(0.7, now=now + 0.3))
        self.assertFalse(state.update(0.9, now=now + 0.4))
        self.assertTrue(state.update(0.9, now=now + 1.1))


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
