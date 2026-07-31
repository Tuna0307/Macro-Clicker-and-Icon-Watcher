import json
import os
import queue
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch, sentinel

import cv2
import numpy as np

from macro_clicker import alert_watcher as watcher
from macro_clicker import window_locator


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

    def test_monitor_reference_uses_monitor_size_even_with_target_window(self):
        tm = self._manager_in_temp_dir()
        tid = tm.add(
            np.zeros((8, 8, 3), dtype=np.uint8),
            "monitor-scaled",
            template_reference_size=(1920, 1080),
            template_reference_space="monitor",
        )
        tm.set_region(
            tid,
            (10, 20, 30, 40),
            "monitor",
            (0.1, 0.2, 0.3, 0.4),
            (100, 100),
            monitor_index=2,
            monitor_unique_id="DISPLAY-B",
        )

        with patch.object(
            watcher,
            "prepare_template_variants",
            return_value=(sentinel.variant,),
        ) as prepare:
            item = tm.snapshot(
                use_grayscale=True,
                current_window_size=(800, 600),
                current_monitor_size=(1920, 1080),
            )[0]

        self.assertEqual(item["variants"], (sentinel.variant,))
        self.assertEqual(prepare.call_args.kwargs["current_size"], (1920, 1080))

    def test_monitor_identity_round_trips_and_survives_reordering(self):
        tm = self._manager_in_temp_dir()
        tid = tm.add(np.zeros((8, 8, 3), dtype=np.uint8), "monitor-bound")
        tm.set_region(
            tid,
            (10, 20, 30, 40),
            "monitor",
            (0.1, 0.2, 0.3, 0.4),
            (100, 100),
            monitor_index=2,
            monitor_unique_id="DISPLAY-B",
        )

        reloaded = watcher.TemplateManager().snapshot()[0]

        self.assertEqual(reloaded["monitor_index"], 2)
        self.assertEqual(reloaded["monitor_unique_id"], "DISPLAY-B")
        self.assertTrue(
            watcher.WatcherThread._item_matches_monitor(
                reloaded,
                1,
                {"unique_id": "DISPLAY-B"},
            )
        )
        self.assertFalse(
            watcher.WatcherThread._item_matches_monitor(
                reloaded,
                2,
                {"unique_id": "DISPLAY-A"},
            )
        )

    def test_legacy_template_without_monitor_identity_keeps_all_monitor_behavior(self):
        self.assertTrue(
            watcher.WatcherThread._item_matches_monitor(
                {},
                2,
                {"unique_id": "DISPLAY-B"},
            )
        )

    def test_variant_generation_does_not_hold_manager_lock(self):
        tm = self._manager_in_temp_dir()
        tid = tm.add(np.zeros((8, 8, 3), dtype=np.uint8), "slow-variant")
        entered = threading.Event()
        release = threading.Event()
        reader_finished = threading.Event()

        def slow_prepare(*_args, **_kwargs):
            entered.set()
            release.wait(2.0)
            return (sentinel.variant,)

        def read_template():
            tm.get(tid)
            reader_finished.set()

        with patch.object(
            watcher,
            "prepare_template_variants",
            side_effect=slow_prepare,
        ):
            generator = threading.Thread(target=lambda: tm.snapshot(use_grayscale=True))
            generator.start()
            self.assertTrue(entered.wait(1.0))
            reader = threading.Thread(target=read_template)
            reader.start()
            self.assertTrue(reader_finished.wait(0.5))
            release.set()
            generator.join(2.0)
            reader.join(2.0)

        self.assertFalse(generator.is_alive())

    def test_relative_regions_reject_negative_offsets_but_screen_regions_allow_them(
        self,
    ):
        tm = self._manager_in_temp_dir()
        tid = tm.add(np.zeros((8, 8, 3), dtype=np.uint8), "negative-screen")

        tm.set_region(tid, (-100, 20, 30, 40), "screen")
        self.assertEqual(tm.get(tid)["region"], (-100, 20, 30, 40))
        with self.assertRaisesRegex(ValueError, "cannot be negative"):
            tm.set_region(tid, (-1, 20, 30, 40), "window")

    def test_fractional_template_reference_size_is_rejected(self):
        self.assertIsNone(watcher.TemplateManager._valid_window_size((1920.5, 1080)))

    def test_unknown_manifest_entry_is_disabled_and_preserved_on_save(self):
        tm = self._manager_in_temp_dir()
        templates_dir = os.path.dirname(watcher.MANIFEST_PATH)
        cv2.imwrite(
            os.path.join(templates_dir, "template_1.png"),
            np.zeros((8, 8, 3), dtype=np.uint8),
        )
        cv2.imwrite(
            os.path.join(templates_dir, "template_2.png"),
            np.zeros((8, 8, 3), dtype=np.uint8),
        )
        malformed = {
            "id": 2,
            "name": "typo",
            "file": "template_2.png",
            "enable": False,
            "threshhold": 0.99,
        }
        with open(watcher.MANIFEST_PATH, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "items": [
                        {
                            "id": 1,
                            "name": "valid",
                            "file": "template_1.png",
                            "enabled": True,
                            "threshold": 0.85,
                        },
                        malformed,
                    ]
                },
                handle,
            )

        tm = watcher.TemplateManager()
        self.assertEqual([item["id"] for item in tm.snapshot()], [1])
        self.assertTrue(any("unknown field" in item for item in tm.load_warnings))
        tm.set_enabled(1, False)
        with open(watcher.MANIFEST_PATH, encoding="utf-8") as handle:
            saved = json.load(handle)

        self.assertIn(malformed, saved["items"])

    def test_orphan_region_metadata_is_disabled_and_preserved_on_save(self):
        self._manager_in_temp_dir()
        templates_dir = os.path.dirname(watcher.MANIFEST_PATH)
        cv2.imwrite(
            os.path.join(templates_dir, "valid.png"),
            np.zeros((8, 8, 3), dtype=np.uint8),
        )
        cv2.imwrite(
            os.path.join(templates_dir, "orphan.png"),
            np.zeros((8, 8, 3), dtype=np.uint8),
        )
        orphan = {
            "id": 2,
            "name": "orphan metadata",
            "file": "orphan.png",
            "region": None,
            "region_mode": "window",
            "region_ratio": [0.1, 0.2, 0.3, 0.4],
            "region_window_size": [1920, 1080],
        }
        with open(watcher.MANIFEST_PATH, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "items": [
                        {
                            "id": 1,
                            "name": "valid",
                            "file": "valid.png",
                        },
                        orphan,
                    ]
                },
                handle,
            )

        tm = watcher.TemplateManager()
        self.assertEqual([item["id"] for item in tm.snapshot()], [1])
        self.assertTrue(
            any(
                "relative resize metadata requires a region" in warning
                for warning in tm.load_warnings
            )
        )
        tm.set_enabled(1, False)
        with open(watcher.MANIFEST_PATH, encoding="utf-8") as handle:
            saved = json.load(handle)

        self.assertIn(orphan, saved["items"])

    def test_unknown_manifest_root_blocks_rewrite_and_runtime_matching(self):
        self._manager_in_temp_dir()
        original = {"items": [], "itmes": []}
        with open(watcher.MANIFEST_PATH, "w", encoding="utf-8") as handle:
            json.dump(original, handle)

        tm = watcher.TemplateManager()

        self.assertEqual(tm.snapshot(), [])
        with self.assertRaisesRegex(ValueError, "malformed data"):
            tm.add(np.zeros((8, 8, 3), dtype=np.uint8), "must-not-save")
        with open(watcher.MANIFEST_PATH, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle), original)

    def test_template_enabled_choice_is_persisted(self):
        tm = self._manager_in_temp_dir()
        tid = tm.add(np.zeros((8, 8, 3), dtype=np.uint8), "optional")

        self.assertTrue(tm.get(tid)["enabled"])
        tm.set_enabled(tid, False)

        self.assertFalse(tm.snapshot()[0]["enabled"])
        self.assertEqual(tm.snapshot(enabled_only=True), [])
        self.assertFalse(watcher.TemplateManager().snapshot()[0]["enabled"])

    def test_snapshot_reuses_prepared_template_variants(self):
        tm = self._manager_in_temp_dir()
        image = np.zeros((24, 24, 3), dtype=np.uint8)
        image[6:18, 6:18] = (255, 255, 255)
        tm.add(image, "cached")

        first = tm.snapshot(use_grayscale=True)[0]
        second = tm.snapshot(use_grayscale=True)[0]

        self.assertIs(first["variants"], second["variants"])
        self.assertGreater(len(first["variants"]), 1)

    def test_match_mode_is_persisted_and_invalidates_variant_cache(self):
        tm = self._manager_in_temp_dir()
        image = np.full((24, 60, 3), (50, 100, 90), dtype=np.uint8)
        cv2.putText(
            image,
            "#2212",
            (2, 18),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (255, 235, 71),
            1,
            cv2.LINE_AA,
        )
        tid = tm.add(image, "chat", threshold=0.8)
        original_variants = tm.snapshot(use_grayscale=True)[0]["variants"]

        tm.set_match_mode(tid, watcher.MATCH_MODE_TEXT)
        text_item = tm.snapshot(use_grayscale=True)[0]
        reloaded = watcher.TemplateManager().snapshot()[0]

        self.assertEqual(text_item["match_mode"], watcher.MATCH_MODE_TEXT)
        self.assertEqual(text_item["threshold"], watcher.DEFAULT_TEXT_THRESHOLD)
        self.assertIsNot(text_item["variants"], original_variants)
        self.assertEqual({item["angle"] for item in text_item["variants"]}, {0})
        self.assertEqual(reloaded["match_mode"], watcher.MATCH_MODE_TEXT)

    def test_reference_size_persists_and_builds_resolution_specific_variants(self):
        tm = self._manager_in_temp_dir()
        image = np.zeros((24, 30, 3), dtype=np.uint8)
        image[4:20, 6:24] = (30, 180, 240)
        tm.add(
            image,
            "scaled",
            template_reference_size=(1920, 1080),
        )

        same_size = tm.snapshot(
            use_grayscale=False,
            current_window_size=(1920, 1080),
        )[0]
        larger = tm.snapshot(
            use_grayscale=False,
            current_window_size=(2560, 1440),
        )[0]
        reloaded = watcher.TemplateManager().snapshot()[0]

        self.assertEqual(reloaded["template_reference_size"], (1920, 1080))
        self.assertIsNot(same_size["variants"], larger["variants"])
        self.assertAlmostEqual(larger["variants"][0]["scale"], 4 / 3)

    def test_each_template_uses_its_own_reference_size(self):
        tm = self._manager_in_temp_dir()
        image = np.zeros((24, 30, 3), dtype=np.uint8)
        image[4:20, 6:24] = (30, 180, 240)
        tm.add(image, "full-hd", template_reference_size=(1920, 1080))
        tm.add(image, "hd-plus", template_reference_size=(1600, 900))

        items = {
            item["name"]: item
            for item in tm.snapshot(
                use_grayscale=False,
                current_window_size=(2560, 1440),
            )
        }

        self.assertAlmostEqual(items["full-hd"]["variants"][0]["scale"], 4 / 3)
        self.assertAlmostEqual(items["hd-plus"]["variants"][0]["scale"], 1.6)

    def test_manifest_path_escape_is_ignored_and_cannot_delete_outside_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            templates_dir = os.path.join(temp_dir, "templates")
            os.makedirs(templates_dir)
            outside_path = os.path.join(temp_dir, "outside.png")
            cv2.imwrite(outside_path, np.zeros((8, 8, 3), dtype=np.uint8))
            manifest_path = os.path.join(templates_dir, "manifest.json")
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "items": [
                            {
                                "id": 1,
                                "name": "unsafe",
                                "file": "../outside.png",
                                "threshold": 0.8,
                            }
                        ]
                    },
                    f,
                )

            with (
                patch.object(watcher, "TEMPLATES_DIR", templates_dir),
                patch.object(watcher, "MANIFEST_PATH", manifest_path),
            ):
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

            with (
                patch.object(watcher, "TEMPLATES_DIR", templates_dir),
                patch.object(watcher, "MANIFEST_PATH", manifest_path),
            ):
                tm = watcher.TemplateManager()

            self.assertEqual(tm.snapshot(), [])
            self.assertTrue(tm.load_warnings)

    def test_invalid_match_mode_fails_closed_without_crashing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            templates_dir = os.path.join(temp_dir, "templates")
            os.makedirs(templates_dir)
            cv2.imwrite(
                os.path.join(templates_dir, "template_1.png"),
                np.zeros((8, 8, 3), dtype=np.uint8),
            )
            manifest_path = os.path.join(templates_dir, "manifest.json")
            with open(manifest_path, "w", encoding="utf-8") as file:
                json.dump(
                    {
                        "items": [
                            {
                                "id": 1,
                                "name": "legacy",
                                "file": "template_1.png",
                                "threshold": 0.8,
                                "match_mode": "unknown",
                            }
                        ]
                    },
                    file,
                )

            with (
                patch.object(watcher, "TEMPLATES_DIR", templates_dir),
                patch.object(watcher, "MANIFEST_PATH", manifest_path),
            ):
                tm = watcher.TemplateManager()

            self.assertEqual(tm.snapshot(), [])
            self.assertTrue(
                any("match_mode is invalid" in item for item in tm.load_warnings)
            )

    def test_container_match_mode_fails_closed_without_crashing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            templates_dir = os.path.join(temp_dir, "templates")
            os.makedirs(templates_dir)
            cv2.imwrite(
                os.path.join(templates_dir, "template_1.png"),
                np.zeros((8, 8, 3), dtype=np.uint8),
            )
            manifest_path = os.path.join(templates_dir, "manifest.json")
            with open(manifest_path, "w", encoding="utf-8") as file:
                json.dump(
                    {
                        "items": [
                            {
                                "id": 1,
                                "name": "malformed mode",
                                "file": "template_1.png",
                                "match_mode": ["animated"],
                            }
                        ]
                    },
                    file,
                )

            with (
                patch.object(watcher, "TEMPLATES_DIR", templates_dir),
                patch.object(watcher, "MANIFEST_PATH", manifest_path),
            ):
                tm = watcher.TemplateManager()

            self.assertEqual(tm.snapshot(), [])
            self.assertTrue(
                any("match_mode is invalid" in item for item in tm.load_warnings)
            )

    def test_unreadable_template_metadata_survives_unrelated_manifest_save(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            templates_dir = os.path.join(temp_dir, "templates")
            os.makedirs(templates_dir)
            cv2.imwrite(
                os.path.join(templates_dir, "template_1.png"),
                np.zeros((8, 8, 3), dtype=np.uint8),
            )
            with open(
                os.path.join(templates_dir, "template_2.png"), "wb"
            ) as unreadable:
                unreadable.write(b"temporarily unreadable")
            missing_entry = {
                "id": 2,
                "name": "preserve me",
                "file": "template_2.png",
                "threshold": 0.93,
                "region": [10, 20, 30, 40],
                "custom_metadata": {"owner": "user"},
            }
            manifest_path = os.path.join(templates_dir, "manifest.json")
            with open(manifest_path, "w", encoding="utf-8") as file:
                json.dump(
                    {
                        "items": [
                            {
                                "id": 1,
                                "name": "readable",
                                "file": "template_1.png",
                            },
                            missing_entry,
                        ]
                    },
                    file,
                )

            with (
                patch.object(watcher, "TEMPLATES_DIR", templates_dir),
                patch.object(watcher, "MANIFEST_PATH", manifest_path),
            ):
                tm = watcher.TemplateManager()
                tm.set_threshold(1, 0.81)

            with open(manifest_path, encoding="utf-8") as file:
                saved_items = {entry["id"]: entry for entry in json.load(file)["items"]}
            self.assertEqual(saved_items[2], missing_entry)
            self.assertEqual(saved_items[1]["threshold"], 0.81)

    def test_fractional_template_region_is_rejected_instead_of_truncated(self):
        tm = self._manager_in_temp_dir()
        tid = tm.add(np.zeros((8, 8, 3), dtype=np.uint8), "fractional")

        with self.assertRaisesRegex(ValueError, "whole numbers"):
            tm.set_region(tid, (10.9, 20, 30, 40))

    def test_add_skips_unlisted_existing_template_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            templates_dir = os.path.join(temp_dir, "templates")
            os.makedirs(templates_dir)
            existing_path = os.path.join(templates_dir, "template_14.png")
            existing = np.full((8, 8, 3), 77, dtype=np.uint8)
            cv2.imwrite(existing_path, existing)
            manifest_path = os.path.join(templates_dir, "manifest.json")

            with (
                patch.object(watcher, "TEMPLATES_DIR", templates_dir),
                patch.object(watcher, "MANIFEST_PATH", manifest_path),
            ):
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
            [
                name
                for name in os.listdir(watcher.TEMPLATES_DIR)
                if name.endswith(".png")
            ],
            [],
        )

    def test_failed_match_mode_save_rolls_back_mode_threshold_and_cache(self):
        tm = self._manager_in_temp_dir()
        tid = tm.add(np.zeros((8, 8, 3), dtype=np.uint8), "rollback", threshold=0.8)
        before = tm.items[tid]
        original_cache = before["variant_cache"]

        with patch.object(tm, "_save", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                tm.set_match_mode(tid, watcher.MATCH_MODE_TEXT)

        self.assertEqual(tm.items[tid]["match_mode"], watcher.MATCH_MODE_STATIC)
        self.assertEqual(tm.items[tid]["threshold"], 0.8)
        self.assertIs(tm.items[tid]["variant_cache"], original_cache)


class DetectionTests(unittest.TestCase):
    @staticmethod
    def _text_tile(text, background):
        image = np.full((32, 130, 3), background, dtype=np.uint8)
        cv2.putText(
            image,
            text,
            (3, 24),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (255, 235, 71),
            2,
            cv2.LINE_AA,
        )
        return image

    def test_match_modes_apply_rotation_only_to_animated_pictures(self):
        image = self._text_tile("#2212", (54, 111, 99))

        text = watcher.prepare_template_variants(
            image, match_mode=watcher.MATCH_MODE_TEXT
        )
        static = watcher.prepare_template_variants(
            image, match_mode=watcher.MATCH_MODE_STATIC
        )
        animated = watcher.prepare_template_variants(
            image, match_mode=watcher.MATCH_MODE_ANIMATED
        )

        self.assertEqual({item["angle"] for item in text}, {0})
        self.assertEqual({item["angle"] for item in static}, {0})
        self.assertEqual(
            {item["angle"] for item in animated}, set(watcher.DEFAULT_ROTATIONS)
        )

    def test_colored_text_mode_ignores_background_and_rejects_similar_digits(self):
        template = self._text_tile("#2212", (54, 111, 99))

        def score_for(text):
            screen = np.full((80, 260, 3), (120, 60, 40), dtype=np.uint8)
            screen[25:57, 70:200] = self._text_tile(text, (120, 60, 40))
            return watcher.match_template_multiscale(
                screen,
                template,
                scales=[1.0],
                rotations=[0],
                match_mode=watcher.MATCH_MODE_TEXT,
            )

        true_score, location, _scale = score_for("#2212")
        wrong_score, _wrong_location, _scale = score_for("#2217")

        self.assertGreaterEqual(true_score, 0.93)
        self.assertEqual(location, (70, 25))
        self.assertLess(wrong_score, watcher.DEFAULT_TEXT_THRESHOLD)

    def test_colored_text_mode_supports_red_and_white_foreground(self):
        for color in ((0, 0, 255), (245, 245, 245)):
            with self.subTest(color=color):
                template = np.full((32, 130, 3), (54, 111, 99), dtype=np.uint8)
                candidate = np.full((32, 130, 3), (100, 50, 30), dtype=np.uint8)
                for image in (template, candidate):
                    cv2.putText(
                        image,
                        "ALERT",
                        (3, 24),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.7,
                        color,
                        2,
                        cv2.LINE_AA,
                    )
                screen = np.full((80, 260, 3), (100, 50, 30), dtype=np.uint8)
                screen[25:57, 70:200] = candidate

                score, location, _scale = watcher.match_template_multiscale(
                    screen,
                    template,
                    scales=[1.0],
                    match_mode=watcher.MATCH_MODE_TEXT,
                )

                self.assertGreaterEqual(score, 0.95)
                self.assertEqual(location, (70, 25))

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
        matrix = cv2.getRotationMatrix2D(
            (smaller.shape[1] / 2, smaller.shape[0] / 2), 5, 1.0
        )
        rotated = cv2.warpAffine(
            smaller,
            matrix,
            (smaller.shape[1], smaller.shape[0]),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_REPLICATE,
        )
        screen = np.zeros((120, 160, 3), dtype=np.uint8)
        screen[40 : 40 + rotated.shape[0], 50 : 50 + rotated.shape[1]] = rotated

        score, loc, scale = watcher.match_template_multiscale(
            screen, icon, use_grayscale=False
        )

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

    def test_screenshot_test_marks_wrong_monitor_binding_unavailable(self):
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp:
            path = temp.name
        cv2.imwrite(path, np.zeros((20, 20, 3), dtype=np.uint8))
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        item = {
            "id": 1,
            "name": "bound elsewhere",
            "enabled": True,
            "threshold": 0.8,
            "image": np.zeros((4, 4, 3), dtype=np.uint8),
            "monitor_index": 1,
            "monitor_unique_id": "DISPLAY-A",
        }

        results = watcher.test_detection_on_screenshot(
            path,
            [item],
            monitor_box=(0, 0, 20, 20),
            monitor_index=2,
            monitor_unique_id="DISPLAY-B",
        )

        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]["unavailable"])
        self.assertFalse(results[0]["matched"])
        self.assertIn("monitor", results[0]["reason"])

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
                    [
                        {
                            "id": 2,
                            "name": "region icon",
                            "threshold": 0.8,
                            "image": np.zeros((4, 4, 3), dtype=np.uint8),
                            "region": (130, 240, 20, 25),
                            "region_mode": "screen",
                            "variants": sentinel.cached_variants,
                        }
                    ],
                    region=(100, 200, 70, 70),
                    region_origin=(100, 200),
                )
        finally:
            os.remove(path)

        self.assertTrue(results[0]["matched"])
        self.assertEqual(match.call_args.kwargs["region"], (30, 40, 20, 25))
        self.assertIs(match.call_args.kwargs["variants"], sentinel.cached_variants)

    def test_cropped_screenshot_can_ignore_unrecoverable_absolute_regions(self):
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
                [
                    {
                        "id": 1,
                        "name": "cropped",
                        "threshold": 0.85,
                        "image": icon,
                        "region": (1500, 900, 100, 80),
                        "region_mode": "screen",
                    }
                ],
                monitor_box=(0, 0, 1920, 1080),
                apply_saved_regions=False,
            )
        finally:
            os.remove(path)

        self.assertTrue(results[0]["matched"])

    def test_cancel_event_stops_between_template_variants(self):
        screen = np.zeros((30, 30, 3), dtype=np.uint8)
        icon = np.zeros((5, 5, 3), dtype=np.uint8)
        variants = watcher.prepare_template_variants(
            icon, scales=[1.0, 1.1], rotations=[0]
        )
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
        self.assertTrue(state.active)
        self.assertFalse(state.update(0.9, now=now + 1.1))
        self.assertFalse(state.update(0.7, now=now + 1.2))
        self.assertTrue(state.update(0.9, now=now + 1.3))


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
        with (
            patch.object(watcher.mss, "MSS", return_value=FakeCapture()),
            patch.object(
                watcher,
                "match_template_multiscale",
                side_effect=lambda *_args, **_kwargs: (next(scores), (1, 1), 1.0),
            ),
        ):
            thread.run()

        alerts = [event for event in watcher._drain_queue(events) if "id" in event]
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["monitor"], 1)
        self.assertAlmostEqual(alerts[0]["score"], 0.92)

    def test_disabled_templates_are_left_out_of_watcher_snapshots(self):
        enabled = self._template_item(1, "enabled")
        disabled = self._template_item(2, "disabled")
        disabled["enabled"] = False

        manager = Mock()
        manager.snapshot.return_value = [enabled, disabled]
        thread = watcher.WatcherThread(manager, queue.Queue(), queue.Queue())

        self.assertEqual(thread._snapshot_items(), [enabled])
        self.assertEqual(thread._snapshot_items(use_grayscale=True), [enabled])
        self.assertTrue(thread.templates_changed() is None)
        self.assertTrue(thread._wake_flag.is_set())

    def test_empty_window_target_without_region_scans_the_selected_screen(self):
        provider = Mock(side_effect=AssertionError("empty title must not be queried"))
        thread = watcher.WatcherThread(
            Mock(),
            queue.Queue(),
            queue.Queue(),
            scan_region_mode="window",
            target_window_title="",
            window_rect_provider=provider,
        )

        self.assertEqual(thread._resolve_scan_context(), (None, None, None))
        provider.assert_not_called()

    def test_each_monitor_prepares_templates_for_its_own_resolution(self):
        item = self._template_item()
        snapshot_sizes = []

        class FakeManager:
            def snapshot(self, use_grayscale=None, current_window_size=None):
                snapshot_sizes.append(current_window_size)
                return [item]

        class FakeCapture:
            monitors = [
                {"left": 0, "top": 0, "width": 4480, "height": 1440},
                {"left": 0, "top": 0, "width": 1920, "height": 1080},
                {"left": 1920, "top": 0, "width": 2560, "height": 1440},
            ]

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def grab(self, monitor):
                return np.zeros(
                    (monitor["height"], monitor["width"], 4),
                    dtype=np.uint8,
                )

        thread = watcher.WatcherThread(
            FakeManager(),
            queue.Queue(),
            queue.Queue(),
        )
        thread._wait_for_next_cycle = thread.stop

        with (
            patch.object(watcher.mss, "MSS", return_value=FakeCapture()),
            patch.object(
                watcher,
                "match_template_multiscale",
                return_value=(0.0, None, 1.0),
            ),
        ):
            thread.run()

        self.assertEqual(
            snapshot_sizes,
            [None, (1920, 1080), (2560, 1440)],
        )

    def test_each_scan_cycle_refreshes_monitor_geometry_and_capture_context(self):
        item = self._template_item()
        snapshot_sizes = []

        class FakeManager:
            def snapshot(self, use_grayscale=None, current_window_size=None):
                snapshot_sizes.append(current_window_size)
                return [item]

        class FakeCapture:
            def __init__(self, monitors):
                self.monitors = monitors
                self.requests = []
                self.enter_count = 0
                self.exit_count = 0

            def __enter__(self):
                self.enter_count += 1
                return self

            def __exit__(self, *_args):
                self.exit_count += 1
                return False

            def grab(self, monitor):
                self.requests.append(monitor)
                return np.zeros(
                    (monitor["height"], monitor["width"], 4),
                    dtype=np.uint8,
                )

        first = FakeCapture(
            [
                {"left": 0, "top": 0, "width": 100, "height": 80},
                {"left": 0, "top": 0, "width": 100, "height": 80},
            ]
        )
        second = FakeCapture(
            [
                {"left": 0, "top": 0, "width": 500, "height": 200},
                {"left": 0, "top": 0, "width": 200, "height": 160},
                {"left": 200, "top": 0, "width": 300, "height": 200},
            ]
        )
        capture_factory = Mock(side_effect=[first, second])
        logs = queue.Queue()
        thread = watcher.WatcherThread(FakeManager(), queue.Queue(), logs)
        completed_cycles = 0

        def finish_after_two_cycles():
            nonlocal completed_cycles
            completed_cycles += 1
            if completed_cycles == 2:
                thread.stop()

        thread._wait_for_next_cycle = finish_after_two_cycles
        with (
            patch.object(watcher.mss, "MSS", capture_factory),
            patch.object(
                watcher,
                "match_template_multiscale",
                return_value=(0.0, None, 1.0),
            ),
        ):
            thread.run()

        self.assertEqual(capture_factory.call_count, 2)
        self.assertEqual(first.enter_count, 1)
        self.assertEqual(first.exit_count, 1)
        self.assertEqual(second.enter_count, 1)
        self.assertEqual(second.exit_count, 1)
        self.assertEqual(first.requests, [first.monitors[1]])
        self.assertEqual(second.requests, second.monitors[1:])
        self.assertEqual(
            snapshot_sizes,
            [None, (100, 80), None, (200, 160), (300, 200)],
        )
        messages = watcher._drain_queue(logs)
        self.assertIn("Watching 1 monitor(s).", messages)
        self.assertIn("Watching 2 monitor(s).", messages)

    def test_capture_refresh_failures_are_cleaned_up_and_retried(self):
        item = self._template_item()

        class FakeManager:
            def snapshot(self, *_args, **_kwargs):
                return [item]

        class EnterFailure:
            def __init__(self):
                self.close_count = 0

            def __enter__(self):
                raise OSError("capture initialization blocked")

            def close(self):
                self.close_count += 1

        class TopologyFailure:
            def __init__(self):
                self.exit_count = 0

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                self.exit_count += 1
                return False

            @property
            def monitors(self):
                raise OSError("display topology changing")

        class WorkingCapture:
            monitors = [
                {"left": 0, "top": 0, "width": 10, "height": 10},
                {"left": 0, "top": 0, "width": 10, "height": 10},
            ]

            def __init__(self):
                self.exit_count = 0

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                self.exit_count += 1
                return False

            def grab(self, _monitor):
                return np.zeros((10, 10, 4), dtype=np.uint8)

        enter_failure = EnterFailure()
        topology_failure = TopologyFailure()
        working = WorkingCapture()
        capture_factory = Mock(side_effect=[enter_failure, topology_failure, working])
        events, logs = queue.Queue(), queue.Queue()
        thread = watcher.WatcherThread(FakeManager(), events, logs)
        completed_cycles = 0

        def finish_after_three_cycles():
            nonlocal completed_cycles
            completed_cycles += 1
            if completed_cycles == 3:
                thread.stop()

        thread._wait_for_next_cycle = finish_after_three_cycles
        with (
            patch.object(watcher.mss, "MSS", capture_factory),
            patch.object(
                watcher,
                "match_template_multiscale",
                return_value=(0.0, None, 1.0),
            ) as matcher,
        ):
            thread.run()

        self.assertEqual(capture_factory.call_count, 3)
        self.assertEqual(enter_failure.close_count, 1)
        self.assertEqual(topology_failure.exit_count, 1)
        self.assertEqual(working.exit_count, 1)
        matcher.assert_called_once()
        messages = watcher._drain_queue(logs)
        self.assertEqual(
            sum("Screen capture refresh failed" in message for message in messages),
            1,
        )
        self.assertFalse(
            any(
                event.get("type") == "watcher_error"
                for event in watcher._drain_queue(events)
            )
        )

    def test_mixed_monitor_identity_inventory_does_not_use_old_item_ordinal(self):
        item = self._template_item()
        item.update(
            {
                "monitor_index": 1,
                "monitor_unique_id": "DISPLAY-A",
            }
        )

        class FakeManager:
            def snapshot(self, *_args, **_kwargs):
                return [item]

        class FakeCapture:
            monitors = [
                {"left": 0, "top": 0, "width": 20, "height": 10},
                {"left": 0, "top": 0, "width": 10, "height": 10},
                {
                    "left": 10,
                    "top": 0,
                    "width": 10,
                    "height": 10,
                    "unique_id": "DISPLAY-B",
                },
            ]

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def grab(self, _monitor):
                return np.zeros((10, 10, 4), dtype=np.uint8)

        thread = watcher.WatcherThread(
            FakeManager(),
            queue.Queue(),
            queue.Queue(),
        )
        thread._wait_for_next_cycle = thread.stop
        with (
            patch.object(watcher.mss, "MSS", return_value=FakeCapture()),
            patch.object(
                watcher,
                "match_template_multiscale",
                return_value=(0.0, None, 1.0),
            ) as matcher,
        ):
            thread.run()

        matcher.assert_not_called()
        self.assertTrue(
            watcher.WatcherThread._item_matches_monitor(
                item,
                1,
                FakeCapture.monitors[1],
                unique_ids_available=False,
            )
        )

    def test_global_monitor_uid_survives_reordering_at_runtime(self):
        item = self._template_item()

        class FakeManager:
            def snapshot(self, *_args, **_kwargs):
                return [item]

        class FakeCapture:
            monitors = [
                {"left": 0, "top": 0, "width": 20, "height": 10},
                {
                    "left": 0,
                    "top": 0,
                    "width": 10,
                    "height": 10,
                    "unique_id": "DISPLAY-B",
                },
                {
                    "left": 10,
                    "top": 0,
                    "width": 10,
                    "height": 10,
                    "unique_id": "DISPLAY-A",
                },
            ]

            def __init__(self):
                self.requests = []

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def grab(self, monitor):
                self.requests.append(monitor)
                return np.zeros((10, 10, 4), dtype=np.uint8)

        capture = FakeCapture()
        thread = watcher.WatcherThread(
            FakeManager(),
            queue.Queue(),
            queue.Queue(),
            monitor_filter=2,
            monitor_unique_id="DISPLAY-B",
        )
        thread._wait_for_next_cycle = thread.stop
        with (
            patch.object(watcher.mss, "MSS", return_value=capture),
            patch.object(
                watcher,
                "match_template_multiscale",
                return_value=(0.0, None, 1.0),
            ),
        ):
            thread.run()

        self.assertEqual(capture.requests, [capture.monitors[1]])

    def test_template_added_mid_cycle_is_deferred_without_stopping_watcher(self):
        first = self._template_item(1, "first")
        added = self._template_item(2, "added")
        snapshots = 0

        class FakeManager:
            def snapshot(self, use_grayscale=None, current_window_size=None):
                nonlocal snapshots
                snapshots += 1
                return [first] if snapshots == 1 else [first, added]

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

        events, logs = queue.Queue(), queue.Queue()
        thread = watcher.WatcherThread(FakeManager(), events, logs)
        thread._wait_for_next_cycle = thread.stop

        with (
            patch.object(watcher.mss, "MSS", return_value=FakeCapture()),
            patch.object(
                watcher,
                "match_template_multiscale",
                return_value=(0.0, None, 1.0),
            ) as matcher,
        ):
            thread.run()

        self.assertEqual(matcher.call_count, 1)
        self.assertFalse(
            any("Watcher error" in item for item in watcher._drain_queue(logs))
        )

    def test_target_window_automatically_follows_its_physical_monitor(self):
        item = self._template_item()

        class FakeManager:
            def snapshot(self, use_grayscale=None, current_window_size=None):
                return [item]

        class FakeCapture:
            monitors = [
                {"left": 0, "top": 0, "width": 20, "height": 10},
                {"left": 0, "top": 0, "width": 10, "height": 10},
                {"left": 10, "top": 0, "width": 10, "height": 10},
            ]

            def __init__(self):
                self.requests = []

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def grab(self, monitor):
                self.requests.append(monitor)
                return np.zeros(
                    (monitor["height"], monitor["width"], 4), dtype=np.uint8
                )

        capture = FakeCapture()
        thread = watcher.WatcherThread(
            FakeManager(),
            queue.Queue(),
            queue.Queue(),
            monitor_filter=1,
            target_window_title="Game",
            window_rect_provider=lambda _title: (10, 0, 10, 10),
        )
        thread._wait_for_next_cycle = thread.stop

        with (
            patch.object(watcher.mss, "MSS", return_value=capture),
            patch.object(
                watcher,
                "match_template_multiscale",
                return_value=(0.0, None, 1.0),
            ),
        ):
            thread.run()

        self.assertEqual(capture.requests, [capture.monitors[2]])

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

    def test_alert_event_keeps_run_and_physical_monitor_identity(self):
        item = self._template_item()
        events = queue.Queue()
        run_token = object()
        thread = watcher.WatcherThread(
            Mock(),
            events,
            queue.Queue(),
            cooldown_sec=0.0,
            run_token=run_token,
        )
        thread._sync_states([item], cooldown_sec=0.0)
        monitor_rect = (-1920, 0, 1920, 1080)

        thread._emit_aggregated_matches(
            [item],
            {1: (0.91, 2, "DISPLAY-B", monitor_rect)},
            now=10.0,
            complete_ids={1},
        )

        event = events.get_nowait()
        self.assertIs(event["run_token"], run_token)
        self.assertIs(event["watcher"], thread)
        self.assertEqual(event["monitor_unique_id"], "DISPLAY-B")
        self.assertEqual(event["monitor_rect"], monitor_rect)

    def test_run_stamps_alert_cooldown_after_scan_finishes(self):
        item = self._template_item()

        class FakeManager:
            def snapshot(self, *_args, **_kwargs):
                return [item]

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
        thread._wait_for_next_cycle = thread.stop
        thread._emit_aggregated_matches = Mock()

        with (
            patch.object(watcher.mss, "MSS", return_value=FakeCapture()),
            patch.object(
                thread,
                "_match_entry",
                return_value=(0.91, (1, 1), 1.0),
            ),
            patch.object(watcher.time, "monotonic", side_effect=[10.0, 25.0]),
        ):
            thread.run()

        self.assertEqual(thread._emit_aggregated_matches.call_args.args[2], 25.0)

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

        with (
            patch.object(watcher.mss, "MSS", return_value=FakeCapture()),
            patch.object(
                watcher,
                "match_template_multiscale",
                side_effect=stop_during_first_match,
            ),
        ):
            thread.run()

        self.assertEqual(len(calls), 1)

    def test_live_config_update_wakes_watcher_and_changes_runtime_snapshot(self):
        thread = watcher.WatcherThread(Mock(), queue.Queue(), queue.Queue())

        thread.update_config(
            monitor_filter=2,
            monitor_unique_id="DISPLAY-B",
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
        self.assertEqual(config["monitor_unique_id"], "DISPLAY-B")
        self.assertEqual(config["scan_region"], (1, 2, 30, 40))
        self.assertEqual(config["target_window_title"], "Game")
        self.assertFalse(config["use_grayscale"])
        self.assertTrue(config["debug"])
        self.assertEqual(config["cooldown_sec"], 2.5)
        self.assertTrue(thread._wake_flag.is_set())

    def test_text_candidate_uses_fast_region_only_confirmation(self):
        class FakeCapture:
            def __init__(self):
                self.requests = []

            def grab(self, request):
                self.requests.append(request)
                return np.zeros((10, 30, 4), dtype=np.uint8)

        thread = watcher.WatcherThread(Mock(), queue.Queue(), queue.Queue())
        entry = self._template_item()
        entry.update(
            {
                "match_mode": watcher.MATCH_MODE_TEXT,
                "threshold": 0.9,
                "region": (110, 220, 30, 10),
            }
        )
        monitor = {"left": 100, "top": 200, "width": 80, "height": 60}
        capture = FakeCapture()
        config = thread._config_snapshot()

        with (
            patch.object(watcher, "TEXT_CONFIRMATION_DELAY_SEC", 0.0),
            patch.object(thread, "_match_entry", return_value=(0.94, (1, 1), 1.0)),
        ):
            result = thread._confirm_text_candidate(
                capture,
                monitor,
                entry,
                config,
                (0.92, (12, 23), 1.0),
                None,
            )

        self.assertEqual(result, (0.92, (12, 23), 1.0))
        self.assertEqual(
            capture.requests,
            [{"left": 110, "top": 220, "width": 30, "height": 10}],
        )

    def test_near_exact_text_candidate_alerts_without_confirmation_delay(self):
        thread = watcher.WatcherThread(Mock(), queue.Queue(), queue.Queue())
        entry = self._template_item()
        entry.update({"match_mode": watcher.MATCH_MODE_TEXT, "threshold": 0.9})
        capture = Mock()

        result = thread._confirm_text_candidate(
            capture,
            {"left": 0, "top": 0, "width": 10, "height": 10},
            entry,
            thread._config_snapshot(),
            (0.98, (1, 1), 1.0),
            None,
        )

        self.assertEqual(result, (0.98, (1, 1), 1.0))
        capture.grab.assert_not_called()


class WatcherFrameLifecycleTests(unittest.TestCase):
    def test_variable_size_popup_packing_avoids_existing_rectangles(self):
        occupied = [
            (1500, 40, 360, 220),
            (1620, 272, 240, 120),
        ]

        x, y = watcher.AlertPopup._choose_popup_position(
            (0, 0, 1920, 1080),
            (180, 100),
            occupied,
        )
        candidate = (x, y, 180, 100)

        self.assertTrue(all(value >= 0 for value in (x, y)))
        self.assertFalse(
            any(
                watcher.AlertPopup._rectangles_overlap(
                    candidate,
                    existing,
                    gap=12,
                )
                for existing in occupied
            )
        )

    class FakeControl:
        def __init__(self):
            self.options = {}

        def config(self, **kwargs):
            self.options.update(kwargs)

    def test_stop_is_nonblocking_and_retains_live_watcher_reference(self):
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
        self.assertIsNone(slow.join_timeout)
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

        frame._append_log.assert_called_once_with(
            "Watcher is already running or still stopping."
        )

    def test_watcher_thread_start_failure_restores_idle_state(self):
        class FailedWatcher:
            def __init__(self, *_args, **_kwargs):
                pass

            def start(self):
                raise RuntimeError("thread unavailable")

        frame = object.__new__(watcher.AlertWatcherFrame)
        frame._shutting_down = False
        frame._settings_load_errors = ()
        frame._refresh_monitor_choices = Mock()
        frame.tm = Mock()
        frame.tm.snapshot.return_value = [
            {
                "id": 1,
                "name": "icon",
                "enabled": True,
                "match_mode": watcher.MATCH_MODE_STATIC,
                "region": (1, 2, 3, 4),
            }
        ]
        frame.event_queue = queue.Queue()
        frame.log_queue = queue.Queue()
        frame.watcher = None
        frame.scan_region = None
        frame.scan_region_mode = "screen"
        frame.scan_region_ratio = None
        frame.scan_region_window_size = None
        frame.target_window_var = Mock()
        frame.target_window_var.get.return_value = ""
        frame.grayscale_var = Mock()
        frame.grayscale_var.get.return_value = True
        frame.debug_var = Mock()
        frame.debug_var.get.return_value = False
        frame.monitor_unique_id = None
        frame._selected_monitor_filter = Mock(return_value=1)
        frame._cooldown_seconds = Mock(return_value=0.5)
        frame._save_settings = Mock()
        frame._append_log = Mock()
        frame._watcher_status_pulse = Mock()
        frame.start_btn = self.FakeControl()
        frame.stop_btn = self.FakeControl()
        frame.status_label = self.FakeControl()
        frame.ui_preferences = Mock(animations_enabled=False)

        with (
            patch.object(watcher, "WatcherThread", FailedWatcher),
            patch.object(watcher.messagebox, "showerror") as showerror,
        ):
            frame._start_watching()

        self.assertIsNone(frame.watcher)
        self.assertIsNone(frame._active_alert_run_token)
        self.assertEqual(frame.start_btn.options["state"], "normal")
        self.assertEqual(frame.stop_btn.options["state"], "disabled")
        self.assertEqual(frame.status_label.options["text"], "Idle")
        self.assertTrue(
            any(
                "Could not start watcher" in call.args[0]
                for call in frame._append_log.call_args_list
            )
        )
        showerror.assert_called_once()

    def test_window_relative_icon_requires_target_before_watching(self):
        frame = object.__new__(watcher.AlertWatcherFrame)
        frame._shutting_down = False
        frame._settings_load_errors = ()
        frame._refresh_monitor_choices = Mock()
        frame.tm = Mock()
        frame.tm.snapshot.return_value = [
            {
                "id": 1,
                "name": "window icon",
                "enabled": True,
                "match_mode": watcher.MATCH_MODE_STATIC,
                "region": (1, 2, 30, 40),
                "region_mode": "window",
            }
        ]
        frame.watcher = None
        frame.scan_region = None
        frame.scan_region_mode = "screen"
        frame.target_window_var = Mock()
        frame.target_window_var.get.return_value = ""

        with patch.object(watcher.messagebox, "showerror") as showerror:
            frame._start_watching()

        self.assertIn("Choose a target window", showerror.call_args.args[1])
        self.assertIn("window icon", showerror.call_args.args[1])

    def test_shutdown_guards_start_and_test_callbacks(self):
        frame = object.__new__(watcher.AlertWatcherFrame)
        frame._shutting_down = True
        frame._selected_id = Mock()
        frame._screenshot_test_running = False
        frame.watcher = None

        with (
            patch.object(watcher, "AlertPopup") as popup,
            patch.object(watcher, "play_alert_sound") as sound,
        ):
            frame._start_watching()
            frame._toggle_watching()
            frame._test_alert()
            frame._test_screenshot()

        frame._selected_id.assert_not_called()
        popup.assert_not_called()
        sound.assert_not_called()

    def test_shutdown_discards_queued_commands_and_alert_events(self):
        frame = object.__new__(watcher.AlertWatcherFrame)
        frame._shutting_down = True
        frame.log_queue = queue.Queue()
        frame.event_queue = queue.Queue()
        frame.event_queue.put({"type": "ui_command", "command": "toggle"})
        frame.event_queue.put({"type": "ui_command", "command": "test_alert"})
        frame.event_queue.put(
            {
                "id": 1,
                "name": "late alert",
                "monitor": 1,
                "score": 0.99,
            }
        )
        frame._show_from_tray = Mock()
        frame._toggle_watching = Mock()
        frame._test_alert = Mock()
        frame._quit_from_tray = Mock()
        frame._append_log = Mock()
        frame.tm = Mock()
        frame.after = Mock()

        with (
            patch.object(watcher, "AlertPopup") as popup,
            patch.object(watcher, "play_alert_sound") as sound,
        ):
            frame._poll_queues()

        frame._toggle_watching.assert_not_called()
        frame._test_alert.assert_not_called()
        frame.tm.get.assert_not_called()
        popup.assert_not_called()
        sound.assert_not_called()

    def test_stale_alert_from_previous_run_is_discarded(self):
        frame = object.__new__(watcher.AlertWatcherFrame)
        frame._shutting_down = False
        frame.log_queue = queue.Queue()
        frame.event_queue = queue.Queue()
        frame.event_queue.put(
            {
                "id": 1,
                "name": "stale alert",
                "monitor": 1,
                "score": 0.99,
                "run_token": object(),
            }
        )
        frame._active_alert_run_token = object()
        frame.tm = Mock()
        frame._append_log = Mock()
        frame.after = Mock()

        with (
            patch.object(watcher, "AlertPopup") as popup,
            patch.object(watcher, "play_alert_sound") as sound,
        ):
            frame._poll_queues()

        frame.tm.get.assert_not_called()
        popup.assert_not_called()
        sound.assert_not_called()
        frame.after.assert_called_once_with(150, frame._poll_queues)

    def test_hotkey_callback_queues_ui_work_without_calling_tk(self):
        frame = object.__new__(watcher.AlertWatcherFrame)
        frame.event_queue = queue.Queue()
        frame.after = Mock(
            side_effect=AssertionError("Tk must not be called from hotkey thread")
        )

        frame._toggle_watching_from_hotkey()

        self.assertEqual(
            frame.event_queue.get_nowait(),
            {"type": "ui_command", "command": "toggle"},
        )

    def test_alert_hotkey_alias_conflict_registers_only_first_binding(self):
        frame = object.__new__(watcher.AlertWatcherFrame)
        frame.settings = Mock(
            start_stop_hotkey="ctrl+shift+f8",
            test_alert_hotkey="shift+control+f8",
        )
        frame.hotkey_handles = []
        frame._toggle_watching_from_hotkey = Mock()
        frame._test_alert_from_hotkey = Mock()
        frame._append_log = Mock()

        with (
            patch.object(watcher, "HAVE_KEYBOARD", True),
            patch.object(
                watcher.keyboard,
                "add_hotkey",
                return_value=sentinel.start_handle,
            ) as add_hotkey,
        ):
            frame._setup_hotkeys()

        add_hotkey.assert_called_once_with(
            "ctrl+shift+f8",
            frame._toggle_watching_from_hotkey,
        )
        self.assertEqual(frame.hotkey_handles, [sentinel.start_handle])
        self.assertTrue(
            any(
                "Hotkey conflict" in call.args[0]
                for call in frame._append_log.call_args_list
            )
        )

    def test_invalid_alert_hotkey_is_not_registered(self):
        frame = object.__new__(watcher.AlertWatcherFrame)
        frame.settings = Mock(
            start_stop_hotkey="f12+f12",
            test_alert_hotkey="ctrl+shift+f9",
        )
        frame.hotkey_handles = []
        frame._toggle_watching_from_hotkey = Mock()
        frame._test_alert_from_hotkey = Mock()
        frame._append_log = Mock()

        with (
            patch.object(watcher, "HAVE_KEYBOARD", True),
            patch.object(
                watcher.keyboard,
                "add_hotkey",
                return_value=sentinel.test_handle,
            ) as add_hotkey,
        ):
            frame._setup_hotkeys()

        add_hotkey.assert_called_once_with(
            "ctrl+shift+f9",
            frame._test_alert_from_hotkey,
        )
        self.assertEqual(frame.hotkey_handles, [sentinel.test_handle])
        self.assertTrue(
            any(
                "f12+f12" in call.args[0] and "not registered" in call.args[0]
                for call in frame._append_log.call_args_list
            )
        )

    def test_global_animation_preference_applies_to_live_watcher(self):
        from macro_clicker.ui_preferences import UiPreferences

        frame = object.__new__(watcher.AlertWatcherFrame)
        frame.watcher = Mock()
        frame.watcher.is_alive.return_value = True
        frame._watcher_status_pulse = Mock()

        frame.apply_ui_preferences(UiPreferences(animations_enabled=False))
        frame._watcher_status_pulse.stop.assert_called_once_with(
            "Watching.Status.TLabel"
        )

        frame._watcher_status_pulse.reset_mock()
        frame.apply_ui_preferences(UiPreferences(animations_enabled=True))
        frame._watcher_status_pulse.start.assert_called_once_with()

    def test_finish_app_quit_destroys_toplevel_not_only_frame(self):
        frame = object.__new__(watcher.AlertWatcherFrame)
        root = Mock()
        frame.watcher = None
        frame._destroy_scheduled = False
        frame.winfo_toplevel = Mock(return_value=root)
        frame.after_idle = lambda callback: callback()

        frame._finish_app_quit()

        root.destroy.assert_called_once_with()

    def test_screenshot_variant_preparation_starts_inside_worker(self):
        workers = []

        class DeferredThread:
            def __init__(self, target, daemon):
                self.target = target
                self.daemon = daemon
                workers.append(self)

            def start(self):
                pass

        class Manager:
            def __init__(self):
                self.calls = []

            def snapshot(self, **kwargs):
                self.calls.append(dict(kwargs))
                return [
                    {
                        "id": 1,
                        "name": "test",
                        "enabled": True,
                        "threshold": 0.8,
                        "image": np.zeros((4, 4, 3), dtype=np.uint8),
                    }
                ]

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp:
            path = temp.name
        cv2.imwrite(path, np.zeros((20, 20, 3), dtype=np.uint8))
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))

        frame = object.__new__(watcher.AlertWatcherFrame)
        frame._screenshot_test_running = False
        frame.tm = Manager()
        frame._resolve_global_scan_region_for_display = Mock(return_value=None)
        frame.target_window_var = Mock()
        frame.target_window_var.get.return_value = ""
        frame._selected_monitor_box = Mock(return_value=(0, 0, 20, 20))
        frame._selected_monitor_filter = Mock(return_value=1)
        frame.grayscale_var = Mock()
        frame.grayscale_var.get.return_value = True
        frame.test_screenshot_btn = Mock()
        frame._append_log = Mock()
        frame.event_queue = queue.Queue()

        with (
            patch.object(watcher.filedialog, "askopenfilename", return_value=path),
            patch.object(watcher.threading, "Thread", DeferredThread),
            patch.object(
                watcher,
                "test_detection_on_screenshot",
                return_value=[],
            ),
        ):
            frame._test_screenshot()
            self.assertEqual(len(frame.tm.calls), 1)
            workers[0].target()

        self.assertEqual(len(frame.tm.calls), 2)
        self.assertNotIn("use_grayscale", frame.tm.calls[0])
        self.assertTrue(frame.tm.calls[1]["use_grayscale"])

    def test_screenshot_worker_start_failure_restores_button(self):
        class Manager:
            def snapshot(self, **_kwargs):
                return [
                    {
                        "id": 1,
                        "name": "test",
                        "enabled": True,
                        "threshold": 0.8,
                        "image": np.zeros((4, 4, 3), dtype=np.uint8),
                    }
                ]

        class FailedThread:
            def __init__(self, target, daemon):
                self.target = target
                self.daemon = daemon

            def start(self):
                raise RuntimeError("thread unavailable")

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp:
            path = temp.name
        cv2.imwrite(path, np.zeros((20, 20, 3), dtype=np.uint8))
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        frame = object.__new__(watcher.AlertWatcherFrame)
        frame._screenshot_test_running = False
        frame.tm = Manager()
        frame._resolve_global_scan_region_for_display = Mock(return_value=None)
        frame.target_window_var = Mock()
        frame.target_window_var.get.return_value = ""
        frame._selected_monitor_box = Mock(return_value=(0, 0, 20, 20))
        frame._selected_monitor_filter = Mock(return_value=1)
        frame.grayscale_var = Mock()
        frame.grayscale_var.get.return_value = True
        frame.test_screenshot_btn = Mock()
        frame._append_log = Mock()
        frame.event_queue = queue.Queue()

        with (
            patch.object(watcher.filedialog, "askopenfilename", return_value=path),
            patch.object(watcher.threading, "Thread", FailedThread),
            patch.object(watcher.messagebox, "showerror") as error,
        ):
            frame._test_screenshot()

        self.assertFalse(frame._screenshot_test_running)
        frame.test_screenshot_btn.config.assert_called_with(
            state="normal",
            text="Test screenshot",
        )
        error.assert_called_once()

    def test_unavailable_saved_monitor_is_preserved_in_selector(self):
        frame = object.__new__(watcher.AlertWatcherFrame)
        frame._refresh_window_list = Mock()
        frame.monitor_combo = {}
        frame._monitor_choices = Mock(return_value=["All monitors", "Monitor 1"])
        frame.monitor_var = Mock()
        frame.monitor_var.get.return_value = "Monitor 2"
        frame._update_region_label = Mock()
        frame._append_log = Mock()

        with (
            patch.object(watcher, "HAVE_KEYBOARD", True),
            patch.object(watcher, "HAVE_PYSTRAY", True),
        ):
            frame._apply_loaded_settings()

        frame.monitor_var.set.assert_not_called()
        self.assertIn("Monitor 2", frame.monitor_combo["values"])

    def test_monitor_choices_refresh_after_reconnect_without_changing_selection(self):
        frame = object.__new__(watcher.AlertWatcherFrame)
        frame.monitor_combo = {}
        frame.monitor_var = Mock()
        frame.monitor_var.get.return_value = "Monitor 2"
        frame._monitor_choices = Mock(
            side_effect=[
                ["All monitors", "Monitor 1"],
                ["All monitors", "Monitor 1", "Monitor 2"],
            ]
        )

        self.assertFalse(frame._refresh_monitor_choices())
        self.assertIn("Monitor 2", frame.monitor_combo["values"])
        self.assertTrue(frame._refresh_monitor_choices())
        self.assertEqual(
            frame.monitor_combo["values"],
            ("All monitors", "Monitor 1", "Monitor 2"),
        )
        frame.monitor_var.set.assert_not_called()

    def test_global_monitor_selection_follows_saved_uid_after_reordering(self):
        class Capture:
            monitors = [
                {"left": 0, "top": 0, "width": 300, "height": 100},
                {
                    "left": 0,
                    "top": 0,
                    "width": 100,
                    "height": 100,
                    "unique_id": "DISPLAY-B",
                },
                {
                    "left": 100,
                    "top": 0,
                    "width": 200,
                    "height": 100,
                    "unique_id": "DISPLAY-A",
                },
            ]

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        frame = object.__new__(watcher.AlertWatcherFrame)
        frame.monitor_combo = {}
        frame.monitor_var = Mock()
        frame.monitor_var.get.return_value = "Monitor 2"
        frame.monitor_unique_id = "DISPLAY-B"
        frame._monitor_choices = Mock(
            return_value=["All monitors", "Monitor 1", "Monitor 2"]
        )

        with patch.object(watcher.mss, "MSS", return_value=Capture()):
            self.assertTrue(frame._refresh_monitor_choices())

        frame.monitor_var.set.assert_called_once_with("Monitor 1")
        self.assertEqual(frame.monitor_unique_id, "DISPLAY-B")

    def test_global_monitor_selection_fails_closed_when_uid_is_absent(self):
        monitors = [
            {"left": 0, "top": 0, "width": 300, "height": 100},
            {
                "left": 0,
                "top": 0,
                "width": 100,
                "height": 100,
                "unique_id": "DISPLAY-A",
            },
            {"left": 100, "top": 0, "width": 200, "height": 100},
        ]

        self.assertIsNone(
            watcher._resolve_monitor_binding(
                monitors,
                monitor_index=2,
                monitor_unique_id="DISPLAY-B",
            )
        )
        for monitor in monitors[1:]:
            monitor.pop("unique_id", None)
        self.assertEqual(
            watcher._resolve_monitor_binding(
                monitors,
                monitor_index=2,
                monitor_unique_id="DISPLAY-B",
            ),
            (2, monitors[2]),
        )

    def test_legacy_global_monitor_selection_acquires_available_uid(self):
        class Capture:
            monitors = [
                {"left": 0, "top": 0, "width": 300, "height": 100},
                {
                    "left": 0,
                    "top": 0,
                    "width": 100,
                    "height": 100,
                    "unique_id": "DISPLAY-A",
                },
                {
                    "left": 100,
                    "top": 0,
                    "width": 200,
                    "height": 100,
                    "unique_id": "DISPLAY-B",
                },
            ]

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        frame = object.__new__(watcher.AlertWatcherFrame)
        frame.monitor_combo = {}
        frame.monitor_var = Mock()
        frame.monitor_var.get.return_value = "Monitor 2"
        frame.monitor_unique_id = None
        frame._monitor_choices = Mock(
            return_value=["All monitors", "Monitor 1", "Monitor 2"]
        )

        with patch.object(watcher.mss, "MSS", return_value=Capture()):
            self.assertTrue(frame._refresh_monitor_choices())

        self.assertEqual(frame.monitor_unique_id, "DISPLAY-B")
        frame.monitor_var.set.assert_not_called()

    def test_icon_preview_uses_its_persisted_monitor_identity(self):
        class Capture:
            monitors = [
                {"left": 0, "top": 0, "width": 300, "height": 100},
                {
                    "left": 200,
                    "top": 0,
                    "width": 100,
                    "height": 100,
                    "unique_id": "DISPLAY-B",
                },
                {
                    "left": 0,
                    "top": 0,
                    "width": 200,
                    "height": 100,
                    "unique_id": "DISPLAY-A",
                },
            ]

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        frame = object.__new__(watcher.AlertWatcherFrame)
        frame._selected_monitor_box = Mock(return_value=(0, 0, 200, 100))
        entry = {
            "region_mode": "monitor",
            "monitor_index": 2,
            "monitor_unique_id": "DISPLAY-B",
        }

        with patch.object(watcher.mss, "MSS", return_value=Capture()):
            box = frame._entry_monitor_box(entry)

        self.assertEqual(box, (200, 0, 100, 100))
        frame._selected_monitor_box.assert_not_called()

    def test_icon_preview_does_not_redirect_missing_uid_to_old_index(self):
        class Capture:
            monitors = [
                {"left": 0, "top": 0, "width": 300, "height": 100},
                {
                    "left": 0,
                    "top": 0,
                    "width": 100,
                    "height": 100,
                    "unique_id": "DISPLAY-A",
                },
                {
                    "left": 100,
                    "top": 0,
                    "width": 200,
                    "height": 100,
                    "unique_id": "DISPLAY-C",
                },
            ]

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        frame = object.__new__(watcher.AlertWatcherFrame)
        entry = {
            "region_mode": "monitor",
            "monitor_index": 2,
            "monitor_unique_id": "DISPLAY-B",
        }

        with patch.object(watcher.mss, "MSS", return_value=Capture()):
            self.assertIsNone(frame._entry_monitor_box(entry))

    def test_icon_preview_uses_index_when_backend_has_no_unique_ids(self):
        class Capture:
            monitors = [
                {"left": 0, "top": 0, "width": 300, "height": 100},
                {"left": 0, "top": 0, "width": 100, "height": 100},
                {"left": 100, "top": 0, "width": 200, "height": 100},
            ]

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        frame = object.__new__(watcher.AlertWatcherFrame)
        entry = {
            "region_mode": "monitor",
            "monitor_index": 2,
            "monitor_unique_id": "DISPLAY-B",
        }

        with patch.object(watcher.mss, "MSS", return_value=Capture()):
            self.assertEqual(
                frame._entry_monitor_box(entry),
                (100, 0, 200, 100),
            )

    def test_monitor_region_pick_updates_scan_source_to_dragged_monitor(self):
        frame = object.__new__(watcher.AlertWatcherFrame)
        frame.deiconify = Mock()
        frame._region_metadata_from_abs_box = Mock(
            return_value={
                "region": (10, 20, 30, 40),
                "region_mode": "monitor",
                "region_ratio": (0.1, 0.2, 0.3, 0.4),
                "region_window_size": (300, 100),
                "monitor_index": 2,
            }
        )
        frame.monitor_var = Mock()
        frame._update_region_label = Mock()
        frame._append_log = Mock()
        frame._save_settings = Mock()

        frame._on_scan_region_picked(None, (100, 200, 30, 40))

        frame.monitor_var.set.assert_called_once_with("Monitor 2")
        frame._save_settings.assert_called_once_with()

    def test_monitor_icon_region_pick_updates_source_to_dragged_monitor(self):
        frame = object.__new__(watcher.AlertWatcherFrame)
        frame.deiconify = Mock()
        frame._region_metadata_from_abs_box = Mock(
            return_value={
                "region": (10, 20, 30, 40),
                "region_mode": "monitor",
                "region_ratio": (0.1, 0.2, 0.3, 0.4),
                "region_window_size": (300, 100),
                "monitor_index": 2,
            }
        )
        frame.tm = Mock()
        frame.tm.get.return_value = {"name": "Watched icon"}
        frame.monitor_var = Mock()
        frame._refresh_list = Mock()
        frame._update_icon_region_label = Mock()
        frame._append_log = Mock()

        frame._on_icon_region_picked(None, (100, 200, 30, 40), 7)

        frame.tm.set_region.assert_called_once_with(
            7,
            (10, 20, 30, 40),
            "monitor",
            (0.1, 0.2, 0.3, 0.4),
            (300, 100),
            monitor_index=2,
            monitor_unique_id=None,
        )
        frame.monitor_var.set.assert_not_called()

    def test_cross_monitor_region_pick_is_rejected(self):
        class Capture:
            monitors = [
                {"left": 0, "top": 0, "width": 200, "height": 100},
                {"left": 0, "top": 0, "width": 100, "height": 100},
                {"left": 100, "top": 0, "width": 100, "height": 100},
            ]

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        frame = object.__new__(watcher.AlertWatcherFrame)
        frame.target_window_var = Mock()
        frame.target_window_var.get.return_value = ""

        with (
            patch.object(watcher.mss, "MSS", return_value=Capture()),
            self.assertRaisesRegex(ValueError, "inside one monitor"),
        ):
            frame._region_metadata_from_abs_box((90, 10, 20, 20))

    def test_close_to_tray_keeps_window_visible_when_tray_thread_died(self):
        frame = object.__new__(watcher.AlertWatcherFrame)
        frame._settings_save_after_id = None
        frame._template_save_after_id = None
        frame._save_settings = Mock()
        frame.embedded = False
        frame.tray_var = Mock()
        frame.tray_var.get.return_value = True
        frame._tray_is_alive = Mock(return_value=False)
        frame.withdraw = Mock()
        frame._append_log = Mock()

        with (
            patch.object(watcher, "HAVE_PYSTRAY", True),
            patch.object(watcher.messagebox, "showwarning") as warning,
        ):
            frame.on_close()

        frame.withdraw.assert_not_called()
        frame.tray_var.set.assert_called_once_with(False)
        warning.assert_called_once()


class SettingsTests(unittest.TestCase):
    def test_unknown_settings_fail_closed_without_rewriting_source(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "settings.json")
            source = {
                "target_window_titel": "Game",
                "moniter_choice": "Monitor 2",
                "scan_regoin": [1, 2, 30, 40],
            }
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(source, handle)

            loaded = watcher.load_settings(path)

            self.assertEqual(loaded.target_window_title, "")
            self.assertEqual(loaded.monitor_choice, "All monitors")
            self.assertIsNone(loaded.scan_region)
            self.assertTrue(watcher.settings_load_errors(loaded))
            with open(path, encoding="utf-8") as handle:
                self.assertEqual(json.load(handle), source)

    def test_negative_relative_setting_fails_closed_but_negative_screen_is_valid(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "settings.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "scan_region": [-100, 20, 30, 40],
                        "scan_region_mode": "screen",
                    },
                    handle,
                )
            screen = watcher.load_settings(path)
            self.assertEqual(screen.scan_region, (-100, 20, 30, 40))
            self.assertFalse(watcher.settings_load_errors(screen))

            with open(path, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "scan_region": [-1, 20, 30, 40],
                        "scan_region_mode": "monitor",
                        "scan_region_ratio": [0.0, 0.1, 0.3, 0.4],
                        "scan_region_window_size": [100, 100],
                    },
                    handle,
                )
            relative = watcher.load_settings(path)

        self.assertIsNone(relative.scan_region)
        self.assertTrue(watcher.settings_load_errors(relative))

    def test_settings_round_trip_preserves_user_options(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "settings.json")
            settings = watcher.AppSettings(
                monitor_choice="Monitor 2",
                monitor_unique_id="DISPLAY-B",
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

    def test_monitor_relative_settings_round_trip(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "settings.json")
            settings = watcher.AppSettings(
                monitor_choice="All monitors",
                scan_region=(100, 50, 200, 100),
                scan_region_mode="monitor",
                scan_region_ratio=(100 / 1920, 50 / 1080, 200 / 1920, 100 / 1080),
                scan_region_window_size=(1920, 1080),
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
            with (
                self.subTest(invalid_data=invalid_data),
                tempfile.TemporaryDirectory() as temp_dir,
            ):
                path = os.path.join(temp_dir, "settings.json")
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(invalid_data, f)

                loaded = watcher.load_settings(path)

            self.assertEqual(loaded, watcher.AppSettings())

    def test_nonfinite_and_wrong_type_settings_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "settings.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "cooldown_sec": float("nan"),
                        "alert_volume": float("inf"),
                        "grayscale": "yes",
                        "scan_region": [1, 2, -3, 4],
                        "target_window_title": 123,
                    },
                    f,
                )

            loaded = watcher.load_settings(path)

        self.assertEqual(loaded.cooldown_sec, watcher.DEFAULT_COOLDOWN_SEC)
        self.assertEqual(loaded.alert_volume, watcher.DEFAULT_ALERT_VOLUME)
        self.assertTrue(loaded.grayscale)
        self.assertIsNone(loaded.scan_region)
        self.assertEqual(loaded.target_window_title, "")

    def test_container_scan_region_mode_uses_safe_default(self):
        for malformed in ([], {}):
            with (
                self.subTest(malformed=malformed),
                tempfile.TemporaryDirectory() as temp_dir,
            ):
                path = os.path.join(temp_dir, "settings.json")
                with open(path, "w", encoding="utf-8") as file:
                    json.dump({"scan_region_mode": malformed}, file)

                loaded = watcher.load_settings(path)

            self.assertEqual(loaded.scan_region_mode, "screen")

    def test_fractional_pixel_coordinates_are_not_silently_truncated(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "settings.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "scan_region": [10.9, 20, 300, 100],
                        "scan_region_mode": "monitor",
                        "scan_region_ratio": [0.1, 0.2, 0.3, 0.4],
                        "scan_region_window_size": [1920.5, 1080],
                    },
                    handle,
                )

            loaded = watcher.load_settings(path)

        self.assertIsNone(loaded.scan_region)
        self.assertIsNone(loaded.scan_region_ratio)
        self.assertIsNone(loaded.scan_region_window_size)

    def test_boolean_numeric_settings_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "settings.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"cooldown_sec": True, "alert_volume": False}, handle)

            loaded = watcher.load_settings(path)

        self.assertEqual(loaded.cooldown_sec, watcher.DEFAULT_COOLDOWN_SEC)
        self.assertEqual(loaded.alert_volume, watcher.DEFAULT_ALERT_VOLUME)
        errors = watcher.settings_load_errors(loaded)
        self.assertTrue(any("cooldown_sec" in error for error in errors))
        self.assertTrue(any("alert_volume" in error for error in errors))

    def test_orphan_relative_resize_metadata_is_reported(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "settings.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "scan_region": None,
                        "scan_region_mode": "monitor",
                        "scan_region_ratio": [0.1, 0.2, 0.3, 0.4],
                        "scan_region_window_size": [1920, 1080],
                    },
                    handle,
                )

            loaded = watcher.load_settings(path)

        self.assertIsNone(loaded.scan_region_ratio)
        self.assertIsNone(loaded.scan_region_window_size)
        self.assertTrue(
            any(
                "relative resize metadata requires a scan region" in error
                for error in watcher.settings_load_errors(loaded)
            )
        )

    def test_empty_window_target_without_region_normalizes_to_screen(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "settings.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "scan_region": None,
                        "scan_region_mode": "window",
                        "target_window_title": "",
                    },
                    handle,
                )

            loaded = watcher.load_settings(path)

        self.assertEqual(loaded.scan_region_mode, "screen")
        self.assertFalse(watcher.settings_load_errors(loaded))

    def test_empty_window_target_preserves_region_for_ui_repair(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "settings.json")
            source = {
                "scan_region": [10, 20, 300, 100],
                "scan_region_mode": "window",
                "scan_region_ratio": [0.1, 0.2, 0.3, 0.4],
                "scan_region_window_size": [1920, 1080],
                "target_window_title": "",
            }
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(source, handle)

            loaded = watcher.load_settings(path)
            self.assertFalse(watcher.settings_load_errors(loaded))
            self.assertEqual(loaded.scan_region, (10, 20, 300, 100))
            self.assertEqual(loaded.scan_region_mode, "window")
            self.assertEqual(loaded.scan_region_ratio, (0.1, 0.2, 0.3, 0.4))
            loaded.target_window_title = "Game"
            watcher.save_settings(path, loaded)
            repaired = watcher.load_settings(path)

        self.assertFalse(watcher.settings_load_errors(repaired))
        self.assertEqual(repaired.target_window_title, "Game")
        self.assertEqual(repaired.scan_region, (10, 20, 300, 100))


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

        with (
            patch.object(watcher, "HAVE_PYGAME", True),
            patch.object(watcher, "pygame", fake_pygame),
            patch.object(watcher, "threading") as threading_module,
        ):
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
        with (
            patch.object(watcher.threading, "Thread", DeferredThread),
            patch.object(watcher, "_play_alert_once") as play_once,
        ):
            watcher.play_alert_sound(0.2)
            watcher.play_alert_sound(0.3)
            watcher.play_alert_sound(0.4)

            self.assertEqual(len(workers), 1)
            workers[0].target()

        play_once.assert_called_once_with(0.4)
        self.assertIsNone(watcher._SOUND_THREAD)
        self.assertIsNone(watcher._PENDING_SOUND_VOLUME)

    def test_thread_start_failure_does_not_wedge_future_sound_requests(self):
        class FailedThread:
            def __init__(self, **_kwargs):
                pass

            def start(self):
                raise RuntimeError("cannot start")

        watcher._SOUND_THREAD = None
        watcher._PENDING_SOUND_VOLUME = None
        self.addCleanup(setattr, watcher, "_SOUND_THREAD", None)
        self.addCleanup(setattr, watcher, "_PENDING_SOUND_VOLUME", None)

        with patch.object(watcher.threading, "Thread", FailedThread):
            watcher.play_alert_sound(0.2)
            watcher.play_alert_sound(0.3)

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
                FakeWindow(
                    "Last War-Survival Game", left=10, top=20, width=800, height=600
                ),
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

        self.assertIs(
            wt._resolve_item_scan_region(item, None), watcher.REGION_UNAVAILABLE
        )
        self.assertIn("Target window not found", logs.get_nowait())

    def test_monitor_relative_item_region_moves_and_scales(self):
        item = {
            "region": (100, 50, 200, 100),
            "region_mode": "monitor",
            "region_ratio": (100 / 1920, 50 / 1080, 200 / 1920, 100 / 1080),
            "region_window_size": (1920, 1080),
        }

        region = watcher.resolve_item_absolute_region(
            item,
            None,
            monitor_box=(1920, 0, 2560, 1440),
        )

        self.assertEqual(region, (2053, 67, 267, 133))


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

    def test_single_instance_lock_propagates_post_lock_write_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "app.lock")
            lock = watcher.SingleInstanceLock(path)

            with (
                patch.object(watcher.os, "fsync", side_effect=OSError("disk full")),
                self.assertRaisesRegex(OSError, "disk full"),
            ):
                lock.acquire()

            self.assertIsNone(lock.fd)

    def test_standalone_lock_access_error_is_reported_without_traceback(self):
        lock = Mock()
        lock.acquire.side_effect = PermissionError("lock directory denied")
        notice = Mock()

        with (
            patch.object(watcher, "SingleInstanceLock", return_value=lock),
            patch.object(watcher.tk, "Tk", return_value=notice),
            patch.object(watcher.messagebox, "showwarning") as warning,
            patch.object(watcher.messagebox, "showerror") as error,
            patch.object(watcher, "App") as app,
        ):
            result = watcher.main()

        self.assertEqual(result, 1)
        warning.assert_not_called()
        app.assert_not_called()
        self.assertEqual(error.call_args.args[0], "Icon Alert Watcher could not start")
        self.assertIn("PermissionError: lock directory denied", error.call_args.args[1])
        notice.destroy.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
