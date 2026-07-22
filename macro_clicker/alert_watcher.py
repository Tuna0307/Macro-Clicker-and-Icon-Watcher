"""
Icon Alert Watcher
===================
Watches your screen(s) for one or more icon templates and pops up an
alert (sound + on-top window) the moment any of them appears -- so you
can multitask without having to stare at the game.

- Works across multiple monitors (e.g. laptop screen + external monitor).
- Template list is extensible: add new icons any time via "Add From File"
  or "Capture From Screen" (drag a box around the icon live).
- Alerts once per appearance: it won't spam you while the icon stays on
  screen, and re-arms automatically once the icon disappears.

Windows only (uses pygame for volume-controlled alert tones, with winsound fallback).
Tested for Python 3.9+.

Run:
    pip install opencv-python mss pillow
    python icon_alert_watcher.py
"""
import ctypes
import json
import math
import os
import queue
import struct
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any, Optional

import cv2
import mss
import numpy as np
from PIL import Image, ImageTk

from .alert_settings import (
    DEFAULT_ALERT_VOLUME,
    DEFAULT_COOLDOWN_SEC,
    DEFAULT_START_STOP_HOTKEY,
    DEFAULT_TEST_ALERT_HOTKEY,
    SETTINGS_PATH,
    AppSettings,
    load_settings,
    save_settings,
)
from .alert_ui import AlertPopup, RegionOverlay, ScreenRegionPicker
from .atomic_io import atomic_write_json as _atomic_write_json
from .atomic_io import atomic_write_png as _atomic_write_png
from .detection_core import (
    DEFAULT_NEW_MATCH_MODE,
    DEFAULT_ROTATIONS,
    DEFAULT_SCALES,
    DETECTION_UNAVAILABLE,
    MATCH_MODE_ANIMATED,
    MATCH_MODE_BY_LABEL,
    MATCH_MODE_LABELS,
    MATCH_MODE_LIST_TAGS,
    MATCH_MODE_STATIC,
    MATCH_MODE_TEXT,
    MATCH_MODE_VALUES,
    capture_bgr,
    intersect_region_with_monitor,
    match_template_multiscale,
    monitor_index_for_rect,
    monitor_indices_for_rect,
    monitor_rect,
    normalize_match_mode,
    prepare_template_variants,
)
from .detection_core import (
    LEGACY_ALERT_MATCH_MODE as LEGACY_MATCH_MODE,
)
from .project_paths import (
    ALERT_MANIFEST_PATH,
    ALERT_TEMPLATES_DIR,
    PROJECT_ROOT,
)
from .project_paths import (
    ALERTS_DIR as ALERTS_PATH,
)
from .runtime_paths import INSTANCE_LOCK_PATH
from .ui_components import (
    COLORS,
    CollapsibleSection,
    StatusPulse,
    Tooltip,
    action_button,
    configure_theme,
)
from .ui_preferences import load_ui_preferences
from .window_locator import (
    find_window_rect,
    proportional_region_from_window,
    relative_region_from_window,
    resolve_saved_capture_region,
    resolve_window_region,
    visible_window_titles,
)

__all__ = [
    "AppSettings",
    "DEFAULT_ALERT_VOLUME",
    "DEFAULT_COOLDOWN_SEC",
    "DEFAULT_ROTATIONS",
    "DEFAULT_SCALES",
    "DEFAULT_START_STOP_HOTKEY",
    "DEFAULT_TEST_ALERT_HOTKEY",
    "MATCH_MODE_ANIMATED",
    "MATCH_MODE_STATIC",
    "MATCH_MODE_TEXT",
    "match_template_multiscale",
    "load_settings",
    "prepare_template_variants",
    "save_settings",
]

try:
    import keyboard
    HAVE_KEYBOARD = True
except ImportError:
    keyboard = None
    HAVE_KEYBOARD = False

try:
    import pystray
    HAVE_PYSTRAY = True
except ImportError:
    pystray = None
    HAVE_PYSTRAY = False

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
try:
    import pygame as _pygame
    pygame: Any = _pygame
    HAVE_PYGAME = True
except ImportError:
    pygame = None
    HAVE_PYGAME = False

try:
    import winsound
    HAVE_WINSOUND = True
except ImportError:
    HAVE_WINSOUND = False  # non-Windows: alerts will be popup-only

APP_DIR = str(PROJECT_ROOT)
ALERTS_DIR = str(ALERTS_PATH)
TEMPLATES_DIR = str(ALERT_TEMPLATES_DIR)
MANIFEST_PATH = str(ALERT_MANIFEST_PATH)
LOCK_PATH = INSTANCE_LOCK_PATH
os.makedirs(TEMPLATES_DIR, exist_ok=True)

POLL_INTERVAL_SEC = 0.6
DEFAULT_THRESHOLD = 0.85
TEXT_CONFIRMATION_DELAY_SEC = 0.10
TEXT_IMMEDIATE_SCORE = 0.97
DEFAULT_TEXT_THRESHOLD = 0.90
REGION_UNAVAILABLE = DETECTION_UNAVAILABLE
MONITOR_REGION_PENDING = object()
_WINDOW_CONTEXT_UNSET = object()
_SOUND_LOCK = threading.Lock()
_SOUND_QUEUE_LOCK = threading.Lock()
_SOUND_THREAD = None
_PENDING_SOUND_VOLUME = None


def _drain_queue(q):
    while True:
        try:
            yield q.get_nowait()
        except queue.Empty:
            break


class SingleInstanceLock:
    def __init__(self, path=LOCK_PATH, process_exists=None):
        self.path = path
        self.process_exists = process_exists or self._process_exists
        self.fd = None
        self._locked = False

    def _process_exists(self, pid):
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            return False
        if pid <= 0:
            return False
        if pid == os.getpid():
            return True
        if sys.platform == "win32":
            process_query_limited_information = 0x1000
            still_active = 259
            handle = ctypes.windll.kernel32.OpenProcess(
                process_query_limited_information,
                False,
                pid,
            )
            if not handle:
                return False
            try:
                exit_code = ctypes.c_ulong()
                if not ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return False
                return exit_code.value == still_active
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False

    def _read_lock_pid(self):
        try:
            with open(self.path, "r", encoding="ascii") as f:
                return int(f.read().strip())
        except (OSError, ValueError):
            return None

    def _is_stale_lock(self):
        pid = self._read_lock_pid()
        if pid is None:
            return True
        return not self.process_exists(pid)

    def _remove_stale_lock(self):
        try:
            os.remove(self.path)
        except FileNotFoundError:
            pass
        except OSError:
            pass

    def acquire(self):
        if self.fd is not None:
            return True
        folder = os.path.dirname(self.path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            if os.path.getsize(self.path) == 0:
                os.write(fd, b" ")
            os.lseek(fd, 0, os.SEEK_SET)
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.ftruncate(fd, 0)
            os.write(fd, f"{os.getpid()}\n".encode("ascii"))
            os.fsync(fd)
        except (OSError, BlockingIOError):
            os.close(fd)
            return False
        self.fd = fd
        self._locked = True
        return True

    def release(self):
        if self.fd is None:
            return
        try:
            os.lseek(self.fd, 0, os.SEEK_SET)
            if self._locked and sys.platform == "win32":
                import msvcrt
                msvcrt.locking(self.fd, msvcrt.LK_UNLCK, 1)
            elif self._locked:
                import fcntl
                fcntl.flock(self.fd, fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            os.close(self.fd)
        except OSError:
            pass
        self.fd = None
        self._locked = False


def resolve_item_absolute_region(
    item,
    global_region,
    target_window_title="",
    window_rect_provider=find_window_rect,
    monitor_box=None,
):
    item_region = item.get("region")
    if item_region is None:
        return global_region
    region_mode = item.get("region_mode", "screen")
    window_rect = None
    if region_mode == "window":
        rect = window_rect_provider(target_window_title)
        if not rect:
            return REGION_UNAVAILABLE
        window_rect = rect
    resolved = resolve_saved_capture_region(
        item_region,
        region_mode,
        item.get("region_ratio"),
        item.get("region_window_size"),
        window_rect=window_rect,
        monitor_rect=monitor_box,
    )
    return REGION_UNAVAILABLE if resolved is None else resolved


# --------------------------------------------------------------------------
# Detection core
# --------------------------------------------------------------------------



def _region_relative_to_origin(region, origin):
    if region is None:
        return None
    x, y, width, height = region
    return (x - origin[0], y - origin[1], width, height)


def test_detection_on_screenshot(path, template_items, use_grayscale=False, region=None,
                                 region_origin=(0, 0), target_window_title="",
                                 window_rect_provider=find_window_rect,
                                 monitor_box=None,
                                 apply_saved_regions=True):
    screenshot = cv2.imread(path)
    if screenshot is None:
        raise ValueError(f"Could not read screenshot: {path}")

    results = []
    screenshot_monitor_box = monitor_box or (
        int(region_origin[0]),
        int(region_origin[1]),
        int(screenshot.shape[1]),
        int(screenshot.shape[0]),
    )
    for item in template_items:
        if not item.get("enabled", True):
            continue
        item_region = None
        if apply_saved_regions:
            item_region = resolve_item_absolute_region(
                item,
                region,
                target_window_title,
                window_rect_provider,
                screenshot_monitor_box,
            )
        if item_region is REGION_UNAVAILABLE:
            results.append({
                "id": item.get("id"),
                "name": item["name"],
                "threshold": item.get("threshold", DEFAULT_THRESHOLD),
                "score": -1.0,
                "loc": None,
                "scale": 1.0,
                "matched": False,
                "unavailable": True,
            })
            continue
        local_region = _region_relative_to_origin(item_region, region_origin)
        score, loc, scale = match_template_multiscale(
            screenshot,
            item["image"],
            use_grayscale=use_grayscale,
            region=local_region,
            variants=item.get("variants"),
            match_mode=item.get("match_mode", LEGACY_MATCH_MODE),
        )
        threshold = item.get("threshold", DEFAULT_THRESHOLD)
        results.append({
            "id": item.get("id"),
            "name": item["name"],
            "threshold": threshold,
            "score": score,
            "loc": loc,
            "scale": scale,
            "matched": score >= threshold,
        })
    return results


class TemplateState:
    """Per-template hysteresis so we alert once per appearance."""
    def __init__(self, threshold, hysteresis=0.06, cooldown_sec=DEFAULT_COOLDOWN_SEC):
        self.threshold = threshold
        self.hysteresis = hysteresis
        self.cooldown_sec = cooldown_sec
        self.active = False
        self.last_alert_at = None

    def update(self, score, now=None):
        if now is None:
            now = time.monotonic()
        if not self.active and score >= self.threshold:
            # This is a new appearance even when its alert is suppressed by
            # cooldown.  Keep it active so the same uninterrupted appearance
            # cannot produce a delayed alert when the cooldown later expires.
            self.active = True
            if self.last_alert_at is not None and now - self.last_alert_at < self.cooldown_sec:
                return False
            self.last_alert_at = now
            return True
        if self.active and score < (self.threshold - self.hysteresis):
            self.active = False
        return False


# --------------------------------------------------------------------------
# Template (persisted) data
# --------------------------------------------------------------------------
class TemplateManager:
    def __init__(self):
        self.items = {}  # id -> {"name", "file", "threshold", "image"(np.array)}
        self._lock = threading.RLock()
        self._next_id = 1
        self.load_warnings = []
        self._load()

    @staticmethod
    def _safe_template_path(filename):
        if not isinstance(filename, str) or not filename.strip():
            raise ValueError("Template filename must be a non-empty string")
        root = os.path.realpath(TEMPLATES_DIR)
        candidate = os.path.realpath(os.path.join(root, filename))
        try:
            inside_root = os.path.commonpath((root, candidate)) == root
        except ValueError:
            inside_root = False
        if os.path.isabs(filename) or not inside_root:
            raise ValueError(f"Template path escapes the template directory: {filename!r}")
        return candidate

    @staticmethod
    def _valid_region(value):
        if value is None or isinstance(value, (str, bytes, dict)):
            return None
        try:
            if any(isinstance(v, bool) for v in value):
                return None
            region = tuple(int(v) for v in value)
        except (TypeError, ValueError, OverflowError):
            return None
        if len(region) != 4 or region[2] <= 0 or region[3] <= 0:
            return None
        return region

    @staticmethod
    def _valid_ratio(value):
        if value is None or isinstance(value, (str, bytes, dict)):
            return None
        try:
            if any(isinstance(v, bool) for v in value):
                return None
            ratio = tuple(float(v) for v in value)
        except (TypeError, ValueError, OverflowError):
            return None
        if len(ratio) != 4 or not all(math.isfinite(v) for v in ratio):
            return None
        if ratio[0] < 0 or ratio[1] < 0 or ratio[2] <= 0 or ratio[3] <= 0:
            return None
        if ratio[0] + ratio[2] > 1.001 or ratio[1] + ratio[3] > 1.001:
            return None
        return ratio

    @staticmethod
    def _valid_window_size(value):
        if value is None or isinstance(value, (str, bytes, dict)):
            return None
        try:
            if any(isinstance(v, bool) for v in value):
                return None
            size = tuple(int(v) for v in value)
        except (TypeError, ValueError, OverflowError):
            return None
        if len(size) != 2 or size[0] <= 0 or size[1] <= 0:
            return None
        return size

    def _reserve_existing_template_ids(self):
        try:
            filenames = os.listdir(TEMPLATES_DIR)
        except OSError:
            return
        for filename in filenames:
            stem, extension = os.path.splitext(filename)
            if extension.lower() != ".png" or not stem.startswith("template_"):
                continue
            try:
                tid = int(stem[len("template_"):])
            except ValueError:
                continue
            if tid > 0:
                self._next_id = max(self._next_id, tid + 1)

    def _load(self):
        self._reserve_existing_template_ids()
        if not os.path.exists(MANIFEST_PATH):
            return
        try:
            with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError, UnicodeError) as exc:
            self.load_warnings.append(f"Could not load template manifest: {exc}")
            return
        if not isinstance(data, dict) or not isinstance(data.get("items", []), list):
            self.load_warnings.append("Template manifest must contain an 'items' list.")
            return
        used_paths = set()
        with self._lock:
            for entry in data.get("items", []):
                if not isinstance(entry, dict):
                    self.load_warnings.append("Ignored a malformed template manifest entry.")
                    continue
                tid = entry.get("id")
                if isinstance(tid, bool) or not isinstance(tid, int) or tid <= 0:
                    self.load_warnings.append("Ignored a template with an invalid ID.")
                    continue
                self._next_id = max(self._next_id, tid + 1)
                if tid in self.items:
                    self.load_warnings.append(f"Ignored duplicate template ID {tid}.")
                    continue
                try:
                    path = self._safe_template_path(entry.get("file"))
                except ValueError as exc:
                    self.load_warnings.append(str(exc))
                    continue
                normalized_path = os.path.normcase(path)
                if normalized_path in used_paths:
                    self.load_warnings.append(
                        f"Ignored template ID {tid}; its image file is already in use."
                    )
                    continue
                img = cv2.imread(path)
                if img is None:
                    self.load_warnings.append(
                        f"Could not read template image for ID {tid}: {entry.get('file')!r}"
                    )
                    continue
                used_paths.add(normalized_path)
                name = entry.get("name")
                if not isinstance(name, str) or not name.strip():
                    name = f"icon_{tid}"
                try:
                    threshold = float(entry.get("threshold", DEFAULT_THRESHOLD))
                    if not math.isfinite(threshold):
                        raise ValueError
                    threshold = min(1.0, max(0.0, threshold))
                except (TypeError, ValueError, OverflowError):
                    threshold = DEFAULT_THRESHOLD
                raw_match_mode = entry.get("match_mode", LEGACY_MATCH_MODE)
                match_mode = normalize_match_mode(raw_match_mode)
                if raw_match_mode not in MATCH_MODE_VALUES and "match_mode" in entry:
                    self.load_warnings.append(
                        f"Template ID {tid} has an invalid match mode; using animated picture."
                    )
                region = self._valid_region(entry.get("region"))
                region_mode = entry.get("region_mode", "screen")
                if region_mode not in ("screen", "window", "monitor"):
                    region_mode = "screen"
                region_ratio = self._valid_ratio(entry.get("region_ratio"))
                region_window_size = self._valid_window_size(
                    entry.get("region_window_size")
                )
                template_reference_size = self._valid_window_size(
                    entry.get("template_reference_size")
                )
                if (
                    region is None
                    or region_mode == "screen"
                    or (region_ratio is None) != (region_window_size is None)
                ):
                    region_ratio = None
                    region_window_size = None
                self.items[tid] = {
                    "name": name.strip(),
                    "file": entry["file"],
                    "enabled": (
                        entry.get("enabled", True)
                        if isinstance(entry.get("enabled", True), bool)
                        else True
                    ),
                    "threshold": threshold,
                    "match_mode": match_mode,
                    "region": region,
                    "region_mode": region_mode,
                    "region_ratio": region_ratio,
                    "region_window_size": region_window_size,
                    "template_reference_size": template_reference_size,
                    "image": img,
                    "variant_cache": {},
                }

    def _save(self):
        with self._lock:
            items = []
            for tid, v in sorted(self.items.items()):
                item = {
                    "id": tid,
                    "name": v["name"],
                    "file": v["file"],
                    "enabled": v.get("enabled", True),
                    "threshold": v["threshold"],
                    "match_mode": v.get("match_mode", LEGACY_MATCH_MODE),
                }
                if v.get("region") is not None:
                    item["region"] = list(v["region"])
                    item["region_mode"] = v.get("region_mode", "screen")
                if v.get("region_ratio") is not None:
                    item["region_ratio"] = list(v["region_ratio"])
                if v.get("region_window_size") is not None:
                    item["region_window_size"] = list(v["region_window_size"])
                if v.get("template_reference_size") is not None:
                    item["template_reference_size"] = list(v["template_reference_size"])
                items.append(item)
            _atomic_write_json(MANIFEST_PATH, {"items": items})

    def add(self, image_bgr, name, threshold=DEFAULT_THRESHOLD,
            match_mode=DEFAULT_NEW_MATCH_MODE, template_reference_size=None):
        with self._lock:
            tid = self._next_id
            filename = f"template_{tid}.png"
            path = self._safe_template_path(filename)
            while tid in self.items or os.path.exists(path):
                tid += 1
                filename = f"template_{tid}.png"
                path = self._safe_template_path(filename)
            self._next_id = tid + 1
            if not isinstance(image_bgr, np.ndarray) or image_bgr.size == 0:
                raise ValueError("Template image is empty or invalid")
            try:
                numeric_threshold = float(threshold)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError("Template threshold must be a finite number") from exc
            if not math.isfinite(numeric_threshold):
                raise ValueError("Template threshold must be a finite number")
            numeric_threshold = min(1.0, max(0.0, numeric_threshold))
            parsed_match_mode = normalize_match_mode(match_mode, default="")
            if parsed_match_mode not in MATCH_MODE_VALUES:
                raise ValueError("Unknown template detection type")
            parsed_reference_size = self._valid_window_size(template_reference_size)
            if template_reference_size is not None and parsed_reference_size is None:
                raise ValueError("Template reference size must contain a positive width and height")
            entry = {
                "name": str(name).strip() or f"icon_{tid}",
                "file": filename,
                "enabled": True,
                "threshold": numeric_threshold,
                "match_mode": parsed_match_mode,
                "region": None,
                "region_mode": "screen",
                "region_ratio": None,
                "region_window_size": None,
                "template_reference_size": parsed_reference_size,
                "image": image_bgr.copy(),
                "variant_cache": {},
            }
            _atomic_write_png(path, image_bgr)
            self.items[tid] = entry
            try:
                self._save()
            except Exception:
                self.items.pop(tid, None)
                try:
                    os.remove(path)
                except OSError:
                    pass
                raise
        return tid

    def remove(self, tid):
        with self._lock:
            entry = self.items.pop(tid, None)
            if entry is None:
                return
            try:
                self._save()
            except Exception:
                self.items[tid] = entry
                raise
            try:
                os.remove(self._safe_template_path(entry["file"]))
            except (OSError, ValueError):
                # The manifest is authoritative; a failed delete leaves only
                # an unreferenced backup image, never a broken live entry.
                pass

    def set_threshold(self, tid, threshold, save=True):
        try:
            threshold = float(threshold)
        except (TypeError, ValueError, OverflowError):
            return
        if not math.isfinite(threshold):
            return
        threshold = min(1.0, max(0.0, threshold))
        with self._lock:
            if tid not in self.items:
                return
            previous = self.items[tid]["threshold"]
            self.items[tid]["threshold"] = threshold
            if save:
                try:
                    self._save()
                except Exception:
                    self.items[tid]["threshold"] = previous
                    raise

    def set_enabled(self, tid, enabled, save=True):
        enabled = bool(enabled)
        with self._lock:
            if tid not in self.items:
                return
            previous = self.items[tid].get("enabled", True)
            self.items[tid]["enabled"] = enabled
            if save:
                try:
                    self._save()
                except Exception:
                    self.items[tid]["enabled"] = previous
                    raise

    def set_match_mode(self, tid, match_mode, save=True):
        parsed = normalize_match_mode(match_mode, default="")
        if parsed not in MATCH_MODE_VALUES:
            raise ValueError("Unknown template detection type")
        with self._lock:
            if tid not in self.items:
                return
            entry = self.items[tid]
            previous = entry.get("match_mode", LEGACY_MATCH_MODE)
            previous_threshold = entry["threshold"]
            previous_cache = entry.get("variant_cache", {})
            entry["match_mode"] = parsed
            if parsed == MATCH_MODE_TEXT:
                entry["threshold"] = max(entry["threshold"], DEFAULT_TEXT_THRESHOLD)
            entry["variant_cache"] = {}
            if save:
                try:
                    self._save()
                except Exception:
                    entry["match_mode"] = previous
                    entry["threshold"] = previous_threshold
                    entry["variant_cache"] = previous_cache
                    raise

    def set_region(self, tid, region, region_mode="screen",
                   region_ratio=None, region_window_size=None):
        if region_mode not in ("screen", "window", "monitor"):
            raise ValueError("Region mode must be 'screen', 'window', or 'monitor'.")
        parsed_region = self._valid_region(region)
        if region is not None and parsed_region is None:
            raise ValueError("Region must contain four whole numbers with positive size.")
        parsed_ratio = self._valid_ratio(region_ratio)
        parsed_window_size = self._valid_window_size(region_window_size)
        if region_mode in ("window", "monitor") and parsed_region is not None:
            if (parsed_ratio is None) != (parsed_window_size is None):
                raise ValueError("Relative regions need both ratio and base size.")
        elif region_mode == "screen" and (region_ratio is not None or region_window_size is not None):
            raise ValueError("Screen regions cannot contain window resize metadata.")
        with self._lock:
            if tid not in self.items:
                return
            previous = {
                key: self.items[tid].get(key)
                for key in ("region", "region_mode", "region_ratio", "region_window_size")
            }
            self.items[tid]["region"] = parsed_region
            self.items[tid]["region_mode"] = region_mode
            self.items[tid]["region_ratio"] = parsed_ratio
            self.items[tid]["region_window_size"] = parsed_window_size
            try:
                self._save()
            except Exception:
                self.items[tid].update(previous)
                raise

    def clear_region(self, tid):
        self.set_region(tid, None, "screen", None, None)

    def get(self, tid):
        with self._lock:
            entry = self.items.get(tid)
            if entry is None:
                return None
            result = dict(entry)
            result["image"] = entry["image"].copy()
            result.pop("variant_cache", None)
            return result

    def _variants_for_entry(
        self,
        entry,
        use_grayscale,
        current_window_size=None,
        cancel_event=None,
    ):
        cache = entry.setdefault("variant_cache", {})
        match_mode = entry.get("match_mode", LEGACY_MATCH_MODE)
        grayscale_key = bool(use_grayscale) if match_mode != MATCH_MODE_TEXT else False
        reference_size = (
            entry.get("template_reference_size")
            or entry.get("region_window_size")
        )
        parsed_current_size = self._valid_window_size(current_window_size)
        key = (
            grayscale_key,
            match_mode,
            tuple(reference_size) if reference_size else None,
            parsed_current_size,
        )
        if key not in cache:
            if len(cache) >= 8:
                cache.pop(next(iter(cache)))
            variants = prepare_template_variants(
                entry["image"],
                use_grayscale=grayscale_key,
                match_mode=match_mode,
                reference_size=reference_size,
                current_size=parsed_current_size,
                cancel_event=cancel_event,
            )
            if cancel_event is not None and cancel_event.is_set():
                return variants
            cache[key] = variants
        return cache[key]

    def snapshot(
        self,
        use_grayscale=None,
        current_window_size=None,
        cancel_event=None,
        enabled_only=False,
    ):
        with self._lock:
            items = []
            for tid, entry in self.items.items():
                if enabled_only and not entry.get("enabled", True):
                    continue
                item = {
                    "id": tid,
                    "name": entry["name"],
                    "file": entry["file"],
                    "enabled": entry.get("enabled", True),
                    "threshold": entry["threshold"],
                    "match_mode": entry.get("match_mode", LEGACY_MATCH_MODE),
                    "region": entry.get("region"),
                    "region_mode": entry.get("region_mode", "screen"),
                    "region_ratio": entry.get("region_ratio"),
                    "region_window_size": entry.get("region_window_size"),
                    "template_reference_size": entry.get("template_reference_size"),
                    "image": entry["image"],
                }
                if use_grayscale is not None:
                    item["variants"] = self._variants_for_entry(
                        entry,
                        use_grayscale,
                        current_window_size,
                        cancel_event,
                    )
                items.append(item)
            return items


# --------------------------------------------------------------------------
# Background watcher thread
# --------------------------------------------------------------------------
class WatcherThread(threading.Thread):
    def __init__(self, template_manager, event_queue, log_queue, monitor_filter=None,
                 scan_region=None, use_grayscale=True, debug=False,
                 cooldown_sec=DEFAULT_COOLDOWN_SEC, scan_region_mode="screen",
                 scan_region_ratio=None, scan_region_window_size=None,
                 target_window_title="", window_rect_provider=find_window_rect):
        super().__init__(daemon=True)
        self.tm = template_manager
        self.event_queue = event_queue
        self.log_queue = log_queue
        self.monitor_filter = monitor_filter
        self.scan_region = scan_region
        self.scan_region_mode = scan_region_mode
        self.scan_region_ratio = scan_region_ratio
        self.scan_region_window_size = scan_region_window_size
        self.target_window_title = target_window_title.strip()
        self.window_rect_provider = window_rect_provider
        self._target_window_missing_logged = False
        self.use_grayscale = use_grayscale
        self.debug = debug
        self.cooldown_sec = cooldown_sec
        self._stop_flag = threading.Event()
        self._wake_flag = threading.Event()
        self._config_lock = threading.RLock()
        self.states = {}  # tid -> TemplateState

    def stop(self):
        self._stop_flag.set()
        self._wake_flag.set()

    def templates_changed(self):
        """Wake the watcher so an enable/disable choice is noticed promptly."""
        self._wake_flag.set()

    def update_config(self, *, monitor_filter=None, scan_region=None,
                      scan_region_mode="screen", scan_region_ratio=None,
                      scan_region_window_size=None, target_window_title="",
                      use_grayscale=True, debug=False,
                      cooldown_sec=DEFAULT_COOLDOWN_SEC):
        with self._config_lock:
            self.monitor_filter = monitor_filter
            self.scan_region = scan_region
            self.scan_region_mode = scan_region_mode
            self.scan_region_ratio = scan_region_ratio
            self.scan_region_window_size = scan_region_window_size
            self.target_window_title = target_window_title.strip()
            self.use_grayscale = bool(use_grayscale)
            self.debug = bool(debug)
            cooldown = float(cooldown_sec)
            if not math.isfinite(cooldown):
                cooldown = DEFAULT_COOLDOWN_SEC
            self.cooldown_sec = max(0.0, cooldown)
            self._target_window_missing_logged = False
        self._wake_flag.set()

    def _config_snapshot(self):
        with self._config_lock:
            return {
                "monitor_filter": self.monitor_filter,
                "scan_region": self.scan_region,
                "scan_region_mode": self.scan_region_mode,
                "scan_region_ratio": self.scan_region_ratio,
                "scan_region_window_size": self.scan_region_window_size,
                "target_window_title": self.target_window_title,
                "use_grayscale": self.use_grayscale,
                "debug": self.debug,
                "cooldown_sec": self.cooldown_sec,
            }

    def _wait_for_next_cycle(self):
        self._wake_flag.wait(POLL_INTERVAL_SEC)
        self._wake_flag.clear()

    def _report_fatal_error(self, exc):
        msg = f"Watcher error: {exc}"
        self.log_queue.put(msg)
        self.event_queue.put({"type": "watcher_error", "error": str(exc), "watcher": self})

    def _sync_states(self, items, cooldown_sec=None):
        if cooldown_sec is None:
            cooldown_sec = self.cooldown_sec
        active_ids = {item["id"] for item in items}
        for tid in list(self.states):
            if tid not in active_ids:
                del self.states[tid]
        for item in items:
            tid = item["id"]
            if tid not in self.states:
                self.states[tid] = TemplateState(
                    item["threshold"],
                    cooldown_sec=cooldown_sec,
                )
            else:
                self.states[tid].threshold = item["threshold"]
                self.states[tid].cooldown_sec = cooldown_sec

    def _snapshot_items(self, use_grayscale=None, current_window_size=None):
        try:
            items = self.tm.snapshot(
                use_grayscale=use_grayscale,
                current_window_size=current_window_size,
                enabled_only=True,
                cancel_event=(
                    self._stop_flag if use_grayscale is not None else None
                ),
            )
        except TypeError as exc:
            if not any(
                name in str(exc)
                for name in ("current_window_size", "cancel_event", "enabled_only")
            ):
                raise
            try:
                items = self.tm.snapshot(
                    use_grayscale=use_grayscale,
                    current_window_size=current_window_size,
                )
            except TypeError as fallback_exc:
                if "current_window_size" not in str(fallback_exc):
                    raise
                items = self.tm.snapshot(use_grayscale=use_grayscale)
        return [item for item in items if item.get("enabled", True)]

    def _emit_aggregated_matches(self, items, best_scores, now, complete_ids=None):
        if complete_ids is None:
            complete_ids = {item["id"] for item in items}
        for entry in items:
            tid = entry["id"]
            score, monitor = best_scores.get(tid, (-1.0, None))
            # A partial scan may safely activate a positive detection, but it
            # must never disarm a template based on monitors that were not read.
            if tid not in complete_ids and score < self.states[tid].threshold:
                continue
            if self.states[tid].update(score, now=now) and monitor is not None:
                self.event_queue.put({
                    "id": tid,
                    "name": entry["name"],
                    "monitor": monitor,
                    "score": score,
                })

    def _local_region_for_monitor(self, mon, absolute_region):
        return intersect_region_with_monitor(mon, absolute_region)

    def _match_entry(self, screen_bgr, entry, config, region=None):
        match_mode = entry.get("match_mode", LEGACY_MATCH_MODE)
        early_exit_score = (
            max(TEXT_IMMEDIATE_SCORE, entry["threshold"])
            if match_mode == MATCH_MODE_TEXT
            else entry["threshold"]
        )
        return match_template_multiscale(
            screen_bgr,
            entry["image"],
            use_grayscale=config["use_grayscale"],
            region=region,
            variants=entry.get("variants"),
            early_exit_score=early_exit_score,
            cancel_event=self._stop_flag,
            match_mode=match_mode,
        )

    def _confirm_text_candidate(self, sct, mon, entry, config, initial_result,
                                absolute_scan_region, window_rect=_WINDOW_CONTEXT_UNSET):
        score, loc, scale = initial_result
        if entry.get("match_mode") != MATCH_MODE_TEXT or score < entry["threshold"]:
            return initial_result
        if score >= max(TEXT_IMMEDIATE_SCORE, entry["threshold"]):
            return initial_result
        if self._stop_flag.wait(TEXT_CONFIRMATION_DELAY_SEC):
            return None

        item_region = self._resolve_item_scan_region(
            entry,
            absolute_scan_region,
            config,
            window_rect=window_rect,
            monitor_box=monitor_rect(mon),
        )
        if item_region is REGION_UNAVAILABLE:
            return None
        local_region = self._local_region_for_monitor(mon, item_region)
        if item_region is not None and local_region is None:
            return None
        try:
            if local_region is None:
                capture_target = mon
            else:
                x, y, width, height = local_region
                capture_target = {
                    "left": mon["left"] + x,
                    "top": mon["top"] + y,
                    "width": width,
                    "height": height,
                }
            confirmation_bgr = capture_bgr(sct, capture_target)
        except Exception:
            return None

        confirmed_score, _confirmed_loc, _confirmed_scale = self._match_entry(
            confirmation_bgr,
            entry,
            config,
            region=None,
        )
        if confirmed_score < entry["threshold"]:
            return confirmed_score, None, scale
        return min(score, confirmed_score), loc, scale

    def _resolve_scan_context(self, config=None):
        if config is None:
            config = self._config_snapshot()
        window_rect = None
        if config["scan_region_mode"] == "window" or config["target_window_title"]:
            rect = self.window_rect_provider(config["target_window_title"])
            if not rect:
                if not self._target_window_missing_logged:
                    self.log_queue.put(
                        f"Target window not found: '{config['target_window_title']}'"
                    )
                    self._target_window_missing_logged = True
                return None, None, REGION_UNAVAILABLE
            self._target_window_missing_logged = False
            window_rect = rect
            if config["scan_region"] is None:
                return rect, (rect[2], rect[3]), rect
        if config["scan_region_mode"] == "window":
            assert window_rect is not None
            region = resolve_window_region(
                config["scan_region"],
                window_rect,
                config["scan_region_ratio"],
                config["scan_region_window_size"],
            )
            return window_rect, (window_rect[2], window_rect[3]), region
        if config["scan_region_mode"] == "monitor" and config["scan_region"] is not None:
            window_size = (
                (window_rect[2], window_rect[3]) if window_rect else None
            )
            return window_rect, window_size, MONITOR_REGION_PENDING
        window_size = (window_rect[2], window_rect[3]) if window_rect else None
        return window_rect, window_size, config["scan_region"]

    def _resolve_absolute_scan_region(self, config=None):
        return self._resolve_scan_context(config)[2]

    def _resolve_item_scan_region(
        self,
        item,
        global_region,
        config=None,
        window_rect=_WINDOW_CONTEXT_UNSET,
        monitor_box=None,
    ):
        if config is None:
            config = self._config_snapshot()
        provider = self.window_rect_provider
        if window_rect is not _WINDOW_CONTEXT_UNSET:
            def provider(_title):
                return window_rect
        result = resolve_item_absolute_region(
            item,
            global_region,
            config["target_window_title"],
            provider,
            monitor_box,
        )
        if result is REGION_UNAVAILABLE:
            if not self._target_window_missing_logged:
                self.log_queue.put(
                    f"Target window not found: '{config['target_window_title']}'"
                )
                self._target_window_missing_logged = True
        else:
            self._target_window_missing_logged = False
        return result

    def run(self):
        try:
            with mss.MSS() as sct:
                # monitors[0] is the combined virtual screen; skip it here,
                # we want each physical monitor captured separately.
                last_monitor_status = None
                last_capture_error = {}
                last_debug_log = 0.0
                while not self._stop_flag.is_set():
                    config = self._config_snapshot()
                    monitor_filter = config["monitor_filter"]
                    all_monitors = list(enumerate(sct.monitors[1:], start=1))
                    items = self._snapshot_items()
                    self._sync_states(items, config["cooldown_sec"])
                    if not items:
                        self._wait_for_next_cycle()
                        continue
                    debug_lines = []
                    now = time.monotonic()
                    window_rect, window_size, absolute_scan_region = (
                        self._resolve_scan_context(config)
                    )
                    if absolute_scan_region is REGION_UNAVAILABLE:
                        self._wait_for_next_cycle()
                        continue
                    if window_rect is not None:
                        followed = set(
                            monitor_indices_for_rect(sct.monitors, window_rect)
                        )
                        monitors = [
                            (idx, mon)
                            for idx, mon in all_monitors
                            if idx in followed
                        ]
                        monitor_scope = ("target", tuple(sorted(followed)))
                    else:
                        monitors = all_monitors
                        if monitor_filter is not None:
                            monitors = [
                                (idx, mon) for idx, mon in all_monitors
                                if idx == monitor_filter
                            ]
                        monitor_scope = ("selected", monitor_filter)
                    signature = tuple(
                        (
                            idx,
                            mon["left"],
                            mon["top"],
                            mon["width"],
                            mon["height"],
                        )
                        for idx, mon in all_monitors
                    )
                    monitor_status = (monitor_scope, signature)
                    if monitor_status != last_monitor_status:
                        if window_rect is not None and monitors:
                            labels = ", ".join(str(idx) for idx, _mon in monitors)
                            self.log_queue.put(
                                f"Following target window on monitor(s): {labels}."
                            )
                        elif window_rect is not None:
                            self.log_queue.put(
                                "Target window does not overlap an available monitor."
                            )
                        elif monitor_filter is not None and not monitors:
                            self.log_queue.put(
                                f"Monitor {monitor_filter} is unavailable; "
                                "no screen will be scanned."
                            )
                        else:
                            self.log_queue.put(f"Watching {len(monitors)} monitor(s).")
                        last_monitor_status = monitor_status
                    best_scores: dict[int, tuple[float, Optional[int]]] = {
                        item["id"]: (-1.0, None) for item in items
                    }
                    complete_ids = {item["id"] for item in items} if monitors else set()
                    for mon_index, mon in monitors:
                        if self._stop_flag.is_set():
                            break
                        try:
                            screen_bgr = capture_bgr(sct, mon)
                        except Exception as exc:
                            last_error_at = last_capture_error.get(mon_index)
                            if last_error_at is None or now - last_error_at >= 10.0:
                                self.log_queue.put(
                                    f"Monitor {mon_index} capture failed: {exc}"
                                )
                                last_capture_error[mon_index] = now
                            complete_ids.clear()
                            continue
                        last_capture_error.pop(mon_index, None)
                        current_size = window_size or (
                            int(mon["width"]), int(mon["height"])
                        )
                        monitor_box = monitor_rect(mon)
                        monitor_scan_region = absolute_scan_region
                        if absolute_scan_region is MONITOR_REGION_PENDING:
                            monitor_scan_region = resolve_saved_capture_region(
                                config["scan_region"],
                                "monitor",
                                config["scan_region_ratio"],
                                config["scan_region_window_size"],
                                monitor_rect=monitor_box,
                            )
                        scan_items = self._snapshot_items(
                            use_grayscale=config["use_grayscale"],
                            current_window_size=current_size,
                        )
                        for entry in scan_items:
                            if self._stop_flag.is_set():
                                break
                            tid = entry["id"]
                            # A template added after the cycle's state snapshot is
                            # picked up safely on the next cycle.
                            if tid not in best_scores:
                                continue
                            item_region = self._resolve_item_scan_region(
                                entry,
                                monitor_scan_region,
                                config,
                                window_rect=window_rect,
                                monitor_box=monitor_box,
                            )
                            if item_region is REGION_UNAVAILABLE:
                                complete_ids.discard(tid)
                                continue
                            region = self._local_region_for_monitor(mon, item_region)
                            if item_region is not None and region is None:
                                continue
                            result = self._match_entry(
                                screen_bgr, entry, config, region=region
                            )
                            confirmed = self._confirm_text_candidate(
                                sct,
                                mon,
                                entry,
                                config,
                                result,
                                monitor_scan_region,
                                window_rect,
                            )
                            if confirmed is None:
                                complete_ids.discard(tid)
                                continue
                            score, loc, scale = confirmed
                            if self._stop_flag.is_set():
                                break
                            if config["debug"]:
                                debug_lines.append(
                                    f"{entry['name']} m{mon_index}: {score:.2f} "
                                    f"(th {entry['threshold']:.2f})"
                                )
                            if score > best_scores[tid][0]:
                                best_scores[tid] = (score, mon_index)
                    if self._stop_flag.is_set():
                        break
                    self._emit_aggregated_matches(
                        items, best_scores, now, complete_ids=complete_ids
                    )
                    if config["debug"] and debug_lines and now - last_debug_log >= 5.0:
                        self.log_queue.put("Debug scores: " + "; ".join(debug_lines))
                        last_debug_log = now
                    self._wait_for_next_cycle()
        except Exception as e:
            self._report_fatal_error(e)
        finally:
            self.event_queue.put({"type": "watcher_finished", "watcher": self})


def _clamp_alert_volume(volume):
    try:
        value = float(volume)
        if not math.isfinite(value):
            return DEFAULT_ALERT_VOLUME
        return min(1.0, max(0.0, value))
    except (TypeError, ValueError, OverflowError):
        return DEFAULT_ALERT_VOLUME


def _tone_buffer(freq, duration_ms, sample_rate=44100):
    sample_count = int(sample_rate * duration_ms / 1000)
    amplitude = 24000
    return b"".join(
        struct.pack("<h", int(amplitude * math.sin(2.0 * math.pi * freq * i / sample_rate)))
        for i in range(sample_count)
    )


def _play_pygame_alert(volume):
    with _SOUND_LOCK:
        if not pygame.mixer.get_init():
            pygame.mixer.init(frequency=44100, size=-16, channels=1)
        for freq in (880, 1100, 880):
            sound = pygame.mixer.Sound(buffer=_tone_buffer(freq, 140))
            sound.set_volume(volume)
            sound.play()
            time.sleep(0.14)


def _play_winsound_alert():
    try:
        for freq in (880, 1100, 880):
            winsound.Beep(freq, 140)
    except RuntimeError:
        winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)


def _play_alert_once(volume):
    if HAVE_PYGAME:
        try:
            _play_pygame_alert(volume)
            return
        except Exception:
            pass
    if HAVE_WINSOUND:
        _play_winsound_alert()
    else:
        print("\a", end="", flush=True)


def _sound_worker():
    global _PENDING_SOUND_VOLUME, _SOUND_THREAD
    while True:
        with _SOUND_QUEUE_LOCK:
            volume = _PENDING_SOUND_VOLUME
            _PENDING_SOUND_VOLUME = None
            if volume is None:
                _SOUND_THREAD = None
                return
        try:
            _play_alert_once(volume)
        except Exception:
            # A failed audio backend must not permanently wedge the single worker.
            pass


def play_alert_sound(volume=DEFAULT_ALERT_VOLUME):
    """Play on one worker, keeping at most one coalesced follow-up alert."""
    global _PENDING_SOUND_VOLUME, _SOUND_THREAD
    volume = _clamp_alert_volume(volume)
    if volume <= 0.0:
        return
    with _SOUND_QUEUE_LOCK:
        _PENDING_SOUND_VOLUME = volume
        if _SOUND_THREAD is not None:
            return
        _SOUND_THREAD = threading.Thread(target=_sound_worker, daemon=True)
        worker = _SOUND_THREAD
    worker.start()


# --------------------------------------------------------------------------
# GUI
# --------------------------------------------------------------------------
class AlertWatcherFrame(ttk.Frame):
    def __init__(self, master, embedded=True):
        super().__init__(master)
        self.embedded = embedded

        self.tm = TemplateManager()
        self.event_queue = queue.Queue()
        self.log_queue = queue.Queue()
        self.watcher = None
        self.settings = load_settings()
        self.scan_region = self.settings.scan_region
        self.scan_region_mode = self.settings.scan_region_mode
        self.scan_region_ratio = self.settings.scan_region_ratio
        self.scan_region_window_size = self.settings.scan_region_window_size
        self.tray_icon = None
        self.tray_thread = None
        self.hotkey_handles = []
        self.log_text_max_lines = 1000
        self._log_line_count = 0
        self._settings_save_after_id = None
        self._template_save_after_id = None
        self._screenshot_test_running = False
        self._close_when_stopped = False
        self._destroy_scheduled = False
        self._shutting_down = False
        self._errored_watcher = None
        self.ui_preferences = load_ui_preferences()
        self._watcher_status_pulse = None

        self._build_ui()
        self._refresh_list()
        self._apply_loaded_settings()
        for warning in self.tm.load_warnings:
            self._append_log(warning)
        self._setup_hotkeys()
        if not self.embedded:
            self._setup_tray()
        self.after(150, self._poll_queues)

    def withdraw(self):
        self.winfo_toplevel().withdraw()

    def deiconify(self):
        self.winfo_toplevel().deiconify()

    def _lift_window(self) -> None:
        self.winfo_toplevel().lift()

    def focus_force(self):
        self.winfo_toplevel().focus_force()

    # ---------------- UI construction ----------------
    def _build_ui(self):
        toolbar = ttk.Frame(self, style="Card.TFrame", padding=(18, 14))
        toolbar.pack(fill="x", padx=12, pady=(12, 8))
        toolbar.columnconfigure(0, weight=1)
        ttk.Label(toolbar, text="Icon Alerts", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        self.status_label = ttk.Label(toolbar, text="Idle", style="Idle.Status.TLabel")
        self.status_label.grid(row=0, column=1, padx=(8, 10))
        self._watcher_status_pulse = StatusPulse(
            self.status_label,
            ("Watching.Status.TLabel", "WatchingPulse.Status.TLabel"),
            interval_ms=850,
        )
        self.start_btn = action_button(
            toolbar,
            text="Start monitoring",
            command=self._start_watching,
            width=142,
        )
        self.start_btn.grid(row=0, column=2, padx=3)
        self.stop_btn = action_button(
            toolbar,
            text="Stop",
            command=self._stop_watching,
            kind="danger",
            state="disabled",
            width=96,
        )
        self.stop_btn.grid(row=0, column=3, padx=3)
        test_alert_btn = ttk.Button(toolbar, text="Test alert", command=self._test_alert)
        test_alert_btn.grid(row=0, column=4, padx=(8, 0))
        Tooltip(self.start_btn, "Start or stop with the configured global hotkey")
        Tooltip(test_alert_btn, "Play the current alert sound and popup")

        workspace = ttk.PanedWindow(self, orient="horizontal")
        workspace.pack(fill="both", expand=True, padx=12, pady=(0, 8))

        left = ttk.Frame(workspace, style="Card.TFrame", padding=18, width=650)
        right = ttk.Frame(workspace, style="Card.TFrame", padding=18, width=300)
        workspace.add(left, weight=3)
        workspace.add(right, weight=1)

        ttk.Label(left, text="Watched icons", style="Title.TLabel").pack(anchor="w")
        list_frame = ttk.Frame(left, style="Surface.TFrame")
        list_frame.pack(fill="both", expand=True, pady=(10, 8))
        self.listbox = tk.Listbox(
            list_frame,
            height=10,
            bg=COLORS["surface"],
            fg=COLORS["text"],
            selectbackground=COLORS["accent_soft"],
            selectforeground=COLORS["text"],
            highlightbackground=COLORS["border"],
            highlightcolor=COLORS["accent"],
            relief="flat",
            borderwidth=0,
            font=("Segoe UI", 10),
            exportselection=False,
        )
        list_scroll = ttk.Scrollbar(
            list_frame,
            orient="vertical",
            command=self.listbox.yview,
        )
        self.listbox.configure(yscrollcommand=list_scroll.set)
        self.listbox.pack(side="left", fill="both", expand=True)
        list_scroll.pack(side="right", fill="y")
        self.listbox.bind("<<ListboxSelect>>", self._on_select)
        self.listbox.bind("<space>", self._toggle_selected_enabled)

        btn_row = ttk.Frame(left, style="Surface.TFrame")
        btn_row.pack(fill="x")
        add_file_btn = ttk.Button(btn_row, text="Add from file", command=self._add_from_file)
        add_file_btn.pack(side="left", padx=(0, 4))
        capture_btn = ttk.Button(btn_row, text="Capture", command=self._add_from_screen)
        capture_btn.pack(side="left", padx=4)
        remove_btn = ttk.Button(btn_row, text="Remove", command=self._remove_selected)
        remove_btn.pack(side="left", padx=4)
        Tooltip(add_file_btn, "Add an existing icon image")
        Tooltip(capture_btn, "Capture an icon from the screen")

        ttk.Separator(left).pack(fill="x", pady=12)
        selected_header = ttk.Frame(left, style="Surface.TFrame")
        selected_header.pack(fill="x")
        ttk.Label(selected_header, text="Selected icon", style="Section.TLabel").pack(side="left")
        self.detect_enabled_var = tk.BooleanVar(value=True)
        self.detect_enabled_check = ttk.Checkbutton(
            selected_header,
            text="Detect this icon",
            variable=self.detect_enabled_var,
            command=self._on_enabled_change,
            state="disabled",
        )
        self.detect_enabled_check.pack(side="left", padx=(16, 0))
        Tooltip(
            self.detect_enabled_check,
            "Only checked icons are scanned. Select an icon and press Space to toggle it.",
        )
        self.icon_region_label = ttk.Label(selected_header, text="Region: global", style="Muted.TLabel")
        self.icon_region_label.pack(side="right")

        icon_region_row = ttk.Frame(left, style="Surface.TFrame")
        icon_region_row.pack(fill="x", pady=(6, 0))
        ttk.Button(
            icon_region_row,
            text="Set region",
            command=self._set_selected_icon_region,
        ).pack(side="left", padx=(0, 4))
        ttk.Button(
            icon_region_row,
            text="Show region",
            command=self._show_selected_icon_region,
        ).pack(side="left", padx=4)
        ttk.Button(
            icon_region_row,
            text="Clear",
            command=self._clear_selected_icon_region,
        ).pack(side="left", padx=4)

        mode_row = ttk.Frame(left, style="Surface.TFrame")
        mode_row.pack(fill="x", pady=(12, 0))
        ttk.Label(mode_row, text="Detection type", style="Surface.TLabel").pack(side="left")
        self.match_mode_var = tk.StringVar(
            value=MATCH_MODE_LABELS[DEFAULT_NEW_MATCH_MODE]
        )
        self.match_mode_combo = ttk.Combobox(
            mode_row,
            textvariable=self.match_mode_var,
            values=list(MATCH_MODE_LABELS.values()),
            state="readonly",
            width=25,
        )
        self.match_mode_combo.pack(side="right", fill="x", expand=True, padx=(8, 0))
        self.match_mode_combo.bind("<<ComboboxSelected>>", self._on_match_mode_change)
        Tooltip(
            self.match_mode_combo,
            "Text ignores translucent backgrounds; static pictures skip rotation; "
            "animated pictures test small rotations.",
        )

        thresh_row = ttk.Frame(left, style="Surface.TFrame")
        thresh_row.pack(fill="x", pady=(12, 0))
        ttk.Label(thresh_row, text="Match sensitivity", style="Surface.TLabel").pack(side="left")
        self.thresh_var = tk.DoubleVar(value=DEFAULT_THRESHOLD)
        self.thresh_scale = ttk.Scale(thresh_row, from_=0.6, to=0.97, variable=self.thresh_var,
                                       command=self._on_threshold_change)
        self.thresh_scale.pack(side="left", fill="x", expand=True, padx=6)
        self.thresh_label = ttk.Label(thresh_row, text=f"{DEFAULT_THRESHOLD:.2f}", style="Surface.TLabel")
        self.thresh_label.pack(side="left")

        ttk.Label(right, text="Detection settings", style="Title.TLabel").pack(anchor="w")
        ttk.Label(right, text="Preview", style="Section.TLabel").pack(anchor="w", pady=(12, 4))
        self.preview_label = tk.Label(
            right,
            bg=COLORS["surface_alt"],
            width=24,
            height=4,
            relief="flat",
            borderwidth=0,
        )
        self.preview_label.pack(fill="x", pady=(0, 8))

        ttk.Separator(right).pack(fill="x", pady=(4, 10))
        ttk.Label(right, text="Scan source", style="Section.TLabel").pack(anchor="w")
        self.monitor_var = tk.StringVar(value=self.settings.monitor_choice)
        self.monitor_combo = ttk.Combobox(
            right,
            textvariable=self.monitor_var,
            values=self._monitor_choices(),
            state="readonly",
            width=18,
        )
        self.monitor_combo.pack(fill="x", pady=(3, 4))
        ttk.Label(right, text="Target window", style="Surface.TLabel").pack(anchor="w", pady=(8, 0))
        self.target_window_var = tk.StringVar(value=self.settings.target_window_title)
        self.target_window_combo = ttk.Combobox(
            right,
            textvariable=self.target_window_var,
            values=[],
            state="normal",
            width=18,
        )
        self.target_window_combo.pack(fill="x", pady=(2, 2))
        ttk.Button(right, text="Refresh windows", command=self._refresh_window_list).pack(fill="x", pady=(2, 6))

        advanced_configured = (
            not self.settings.grayscale
            or self.settings.debug
            or self.settings.cooldown_sec != DEFAULT_COOLDOWN_SEC
            or self.settings.alert_volume != DEFAULT_ALERT_VOLUME
        )
        advanced_detection = CollapsibleSection(
            right,
            "Advanced detection" + (" (configured)" if advanced_configured else ""),
            expanded=False,
        )
        advanced_detection.pack(fill="x", pady=(2, 4))
        advanced = advanced_detection.content

        self.grayscale_var = tk.BooleanVar(value=self.settings.grayscale)
        grayscale_check = ttk.Checkbutton(
            advanced,
            text="Grayscale pictures",
            variable=self.grayscale_var,
        )
        grayscale_check.pack(anchor="w")
        Tooltip(
            grayscale_check,
            "Applies to picture modes only. Colored-text mode always preserves color.",
        )
        self.debug_var = tk.BooleanVar(value=self.settings.debug)
        ttk.Checkbutton(advanced, text="Debug scores", variable=self.debug_var).pack(anchor="w")

        cooldown_row = ttk.Frame(advanced, style="Surface.TFrame")
        cooldown_row.pack(fill="x", pady=(4, 4))
        ttk.Label(cooldown_row, text="Cooldown", style="Surface.TLabel").pack(side="left")
        self.cooldown_var = tk.DoubleVar(value=self.settings.cooldown_sec)
        ttk.Spinbox(cooldown_row, from_=0.0, to=60.0, increment=0.5,
                    textvariable=self.cooldown_var, width=6).pack(side="right")

        volume_row = ttk.Frame(advanced, style="Surface.TFrame")
        volume_row.pack(fill="x", pady=(4, 4))
        ttk.Label(volume_row, text="Alert volume", style="Surface.TLabel").pack(side="left")
        self.volume_var = tk.DoubleVar(value=self.settings.alert_volume * 100.0)
        self.volume_label = ttk.Label(volume_row, text=f"{int(round(self.settings.alert_volume * 100))}%")
        self.volume_label.pack(side="right")
        ttk.Scale(
            advanced,
            from_=0,
            to=100,
            variable=self.volume_var,
            command=self._on_volume_change,
        ).pack(fill="x", pady=(0, 4))

        ttk.Separator(right).pack(fill="x", pady=(8, 10))
        ttk.Label(right, text="Scan region", style="Section.TLabel").pack(anchor="w")
        self.region_label = ttk.Label(right, text="Region: full screen", style="Muted.TLabel")
        self.region_label.pack(anchor="w", pady=(3, 5))
        ttk.Button(right, text="Set region", command=self._set_scan_region).pack(fill="x", pady=2)
        ttk.Button(right, text="Clear region", command=self._clear_scan_region).pack(fill="x", pady=2)
        self.test_screenshot_btn = ttk.Button(
            right,
            text="Test screenshot",
            command=self._test_screenshot,
        )
        self.test_screenshot_btn.pack(fill="x", pady=(8, 2))

        ttk.Separator(right).pack(fill="x", pady=(8, 6))
        self.tray_var = tk.BooleanVar(value=self.settings.minimize_to_tray)
        if not self.embedded:
            ttk.Checkbutton(right, text="Minimize to tray", variable=self.tray_var,
                            command=self._on_settings_changed).pack(anchor="w")

        log_frame = ttk.Frame(self, style="Surface.TFrame", padding=(12, 8))
        # Reserve the activity area before the expanding workspace is sized.
        log_frame.pack(fill="x", side="bottom", before=workspace, padx=10, pady=(0, 10))
        ttk.Label(log_frame, text="Activity", style="Section.TLabel").pack(anchor="w")
        log_body = ttk.Frame(log_frame, style="Surface.TFrame")
        log_body.pack(fill="x", pady=(6, 0))
        self.log_text = tk.Text(
            log_body,
            height=5,
            state="disabled",
            bg=COLORS["surface"],
            fg=COLORS["text"],
            selectbackground=COLORS["accent_soft"],
            relief="flat",
            borderwidth=0,
            font=("Cascadia Mono", 9),
            wrap="none",
        )
        log_scroll = ttk.Scrollbar(log_body, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.pack(side="left", fill="x", expand=True)
        log_scroll.pack(side="right", fill="y")

        for var in (
            self.monitor_var,
            self.target_window_var,
            self.grayscale_var,
            self.debug_var,
            self.cooldown_var,
        ):
            var.trace_add("write", lambda *_args: self._on_settings_changed())

    def _monitor_choices(self):
        try:
            with mss.MSS() as sct:
                count = len(sct.monitors[1:])
        except Exception:
            count = 0
        return ["All monitors"] + [f"Monitor {i}" for i in range(1, count + 1)]

    def _apply_loaded_settings(self):
        self._refresh_window_list()
        choices = list(self.monitor_combo["values"])
        if self.monitor_var.get() not in choices:
            self.monitor_var.set("All monitors")
        self._update_region_label()
        if not HAVE_KEYBOARD:
            self._append_log("Global hotkeys disabled: install 'keyboard' to enable them.")
        if not HAVE_PYSTRAY:
            self._append_log("System tray disabled: install 'pystray' to enable it.")

    def apply_ui_preferences(self, preferences):
        """Apply global presentation preferences immediately in embedded mode."""
        self.ui_preferences = preferences
        pulse = getattr(self, "_watcher_status_pulse", None)
        watcher = getattr(self, "watcher", None)
        watching = watcher is not None and watcher.is_alive()
        if pulse is None:
            return
        if preferences.animations_enabled and watching:
            pulse.start()
        else:
            final_style = "Watching.Status.TLabel" if watching else "Idle.Status.TLabel"
            pulse.stop(final_style)

    def _current_settings(self):
        return AppSettings(
            monitor_choice=self.monitor_var.get(),
            grayscale=bool(self.grayscale_var.get()),
            debug=bool(self.debug_var.get()),
            cooldown_sec=self._cooldown_seconds(),
            alert_volume=self._alert_volume(),
            scan_region=self.scan_region,
            scan_region_mode=self.scan_region_mode,
            scan_region_ratio=self.scan_region_ratio,
            scan_region_window_size=self.scan_region_window_size,
            target_window_title=self.target_window_var.get().strip(),
            start_stop_hotkey=self.settings.start_stop_hotkey,
            test_alert_hotkey=self.settings.test_alert_hotkey,
            minimize_to_tray=bool(self.tray_var.get()) if hasattr(self, "tray_var") else False,
        )

    def _save_settings(self):
        self._settings_save_after_id = None
        self.settings = self._current_settings()
        try:
            save_settings(SETTINGS_PATH, self.settings)
        except (OSError, TypeError, ValueError) as exc:
            self._append_log(f"Could not save settings: {exc}")
            self._schedule_failed_settings_retry()
        watcher = self.watcher
        if watcher is not None and watcher.is_alive():
            watcher.update_config(
                monitor_filter=self._selected_monitor_filter(),
                scan_region=self.scan_region,
                scan_region_mode=self.scan_region_mode,
                scan_region_ratio=self.scan_region_ratio,
                scan_region_window_size=self.scan_region_window_size,
                target_window_title=self.target_window_var.get().strip(),
                use_grayscale=self.grayscale_var.get(),
                debug=self.debug_var.get(),
                cooldown_sec=self._cooldown_seconds(),
            )

    def _schedule_settings_save(self):
        if self._settings_save_after_id is not None:
            self.after_cancel(self._settings_save_after_id)
        self._settings_save_after_id = self.after(300, self._save_settings)

    def _schedule_failed_settings_retry(self):
        if (
            self._settings_save_after_id is not None
            or getattr(self, "_close_when_stopped", False)
            or getattr(self, "_destroy_scheduled", False)
            or getattr(self, "_shutting_down", False)
        ):
            return
        try:
            self._settings_save_after_id = self.after(2000, self._save_settings)
        except tk.TclError:
            pass

    def _on_settings_changed(self):
        if hasattr(self, "monitor_var") and hasattr(self, "tray_var"):
            self._schedule_settings_save()

    def _on_volume_change(self, _value):
        if not hasattr(self, "volume_label"):
            return
        self.volume_label.config(text=f"{int(round(self._alert_volume() * 100))}%")
        self._schedule_settings_save()

    def _update_region_label(self):
        if self.scan_region is None:
            if self.target_window_var.get().strip():
                self.region_label.config(text="Region: target window")
            else:
                self.region_label.config(text="Region: full screen")
            return
        x, y, w, h = self.scan_region
        scope = {
            "window": "window",
            "monitor": "monitor",
        }.get(self.scan_region_mode, "screen")
        self.region_label.config(text=f"Region: {w}x{h} at {x},{y} ({scope})")

    def _refresh_window_list(self):
        if not hasattr(self, "target_window_combo"):
            return
        try:
            self.target_window_combo["values"] = visible_window_titles()
        except Exception as exc:
            self._append_log(f"Could not list windows: {exc}")

    def _setup_hotkeys(self):
        if not HAVE_KEYBOARD:
            return
        for hotkey, callback in (
            (self.settings.start_stop_hotkey, self._toggle_watching_from_hotkey),
            (self.settings.test_alert_hotkey, self._test_alert_from_hotkey),
        ):
            try:
                handle = keyboard.add_hotkey(hotkey, callback)
                self.hotkey_handles.append(handle)
                self._append_log(f"Hotkey registered: {hotkey}")
            except Exception as exc:
                self._append_log(f"Could not register hotkey '{hotkey}': {exc}")

    def _cleanup_hotkeys(self):
        if not HAVE_KEYBOARD:
            return
        for handle in self.hotkey_handles:
            try:
                keyboard.remove_hotkey(handle)
            except Exception:
                pass
        self.hotkey_handles = []

    def _toggle_watching_from_hotkey(self):
        self.event_queue.put({"type": "ui_command", "command": "toggle"})

    def _test_alert_from_hotkey(self):
        self.event_queue.put({"type": "ui_command", "command": "test_alert"})

    def _toggle_watching(self):
        if self.watcher and self.watcher.is_alive():
            self._stop_watching()
        else:
            self._start_watching()

    def _make_tray_image(self):
        img = Image.new("RGB", (64, 64), "#1f1f1f")
        arr = np.array(img)
        cv2.circle(arr, (32, 32), 22, (80, 220, 120), -1)
        cv2.circle(arr, (32, 32), 12, (31, 31, 31), -1)
        return Image.fromarray(arr)

    def _setup_tray(self):
        if not HAVE_PYSTRAY:
            return

        def show_window(_icon=None, _item=None):
            self.event_queue.put({"type": "ui_command", "command": "show"})

        def toggle_monitoring(_icon=None, _item=None):
            self.event_queue.put({"type": "ui_command", "command": "toggle"})

        def quit_app(_icon=None, _item=None):
            self.event_queue.put({"type": "ui_command", "command": "quit"})

        menu = pystray.Menu(
            pystray.MenuItem("Show", show_window),
            pystray.MenuItem("Start/Stop Monitoring", toggle_monitoring),
            pystray.MenuItem(
                "Test Alert",
                lambda _icon, _item: self.event_queue.put(
                    {"type": "ui_command", "command": "test_alert"}
                ),
            ),
            pystray.MenuItem("Quit", quit_app),
        )
        self.tray_icon = pystray.Icon(
            "Icon Alert Watcher",
            self._make_tray_image(),
            "Icon Alert Watcher",
            menu,
        )
        self.tray_thread = threading.Thread(target=self.tray_icon.run, daemon=True)
        self.tray_thread.start()

    def _show_from_tray(self):
        self.deiconify()
        self._lift_window()
        self.focus_force()

    def _quit_from_tray(self):
        self._request_app_quit()

    def _cleanup_tray(self):
        if self.tray_icon is not None:
            try:
                self.tray_icon.stop()
            except Exception:
                pass
            self.tray_icon = None

    # ---------------- template list helpers ----------------
    def _refresh_list(self, selected_tid=None):
        if selected_tid is None and hasattr(self, "_id_order"):
            selected_tid = self._selected_id()
        self.listbox.delete(0, "end")
        items = self.tm.snapshot()
        for entry in items:
            enabled = entry.get("enabled", True)
            check = "[x]" if enabled else "[ ]"
            marker = " [region]" if entry.get("region") is not None else ""
            mode = entry.get("match_mode", LEGACY_MATCH_MODE)
            mode_tag = MATCH_MODE_LIST_TAGS.get(mode, "Animated")
            self.listbox.insert(
                "end",
                f"{check} {entry['name']} [{mode_tag}]{marker}   (th={entry['threshold']:.2f})",
            )
            if not enabled:
                self.listbox.itemconfigure("end", foreground=COLORS["muted"])
        self._id_order = [entry["id"] for entry in items]
        if selected_tid in self._id_order:
            index = self._id_order.index(selected_tid)
            self.listbox.selection_set(index)
            self.listbox.see(index)
        elif not self._id_order and hasattr(self, "detect_enabled_check"):
            self.detect_enabled_var.set(False)
            self.detect_enabled_check.config(state="disabled")

    def _selected_id(self):
        sel = self.listbox.curselection()
        if not sel:
            return None
        return self._id_order[sel[0]]

    def _on_select(self, _event):
        tid = self._selected_id()
        if tid is None:
            return
        entry = self.tm.get(tid)
        if entry is None:
            return
        self.detect_enabled_var.set(entry.get("enabled", True))
        self.detect_enabled_check.config(state="normal")
        self.thresh_var.set(entry["threshold"])
        self.thresh_label.config(text=f"{entry['threshold']:.2f}")
        match_mode = entry.get("match_mode", LEGACY_MATCH_MODE)
        self.match_mode_var.set(MATCH_MODE_LABELS[match_mode])
        self._show_preview(entry["image"])
        self._update_icon_region_label(entry)

    def _on_enabled_change(self):
        tid = self._selected_id()
        if tid is None:
            return
        enabled = bool(self.detect_enabled_var.get())
        self.tm.set_enabled(tid, enabled, save=False)
        self._refresh_list(selected_tid=tid)
        self._schedule_template_save()
        watcher = self.watcher
        if watcher is not None and watcher.is_alive():
            watcher.templates_changed()
        state = "enabled" if enabled else "disabled"
        entry = self.tm.get(tid)
        if entry is not None:
            self._append_log(f"Detection {state} for '{entry['name']}'.")

    def _toggle_selected_enabled(self, _event=None):
        tid = self._selected_id()
        if tid is None:
            return "break"
        entry = self.tm.get(tid)
        if entry is None:
            return "break"
        self.detect_enabled_var.set(not entry.get("enabled", True))
        self._on_enabled_change()
        return "break"

    def _update_icon_region_label(self, entry=None):
        if entry is None:
            tid = self._selected_id()
            entry = self.tm.get(tid) if tid is not None else None
        if entry is None or entry.get("region") is None:
            self.icon_region_label.config(text="Icon region: global")
            return
        x, y, w, h = entry["region"]
        scope = {
            "window": "window",
            "monitor": "monitor",
        }.get(entry.get("region_mode"), "screen")
        self.icon_region_label.config(text=f"Icon region: {w}x{h} ({scope})")

    def _show_preview(self, image_bgr):
        rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
        img = Image.fromarray(rgb)
        img.thumbnail((120, 120))
        tk_img = ImageTk.PhotoImage(img)
        self.preview_label.configure(image=tk_img)
        self.preview_label.image = tk_img

    def _on_threshold_change(self, _value):
        tid = self._selected_id()
        if tid is None:
            return
        val = round(self.thresh_var.get(), 2)
        self.thresh_label.config(text=f"{val:.2f}")
        self.tm.set_threshold(tid, val, save=False)
        self._refresh_list(selected_tid=tid)
        self._schedule_template_save()

    def _on_match_mode_change(self, _event=None):
        tid = self._selected_id()
        if tid is None:
            return
        match_mode = MATCH_MODE_BY_LABEL.get(self.match_mode_var.get())
        if match_mode is None:
            return
        try:
            self.tm.set_match_mode(tid, match_mode, save=False)
        except ValueError as exc:
            messagebox.showerror("Invalid detection type", str(exc), parent=self)
            return
        entry = self.tm.get(tid)
        if entry is not None:
            self.thresh_var.set(entry["threshold"])
            self.thresh_label.config(text=f"{entry['threshold']:.2f}")
        self._refresh_list(selected_tid=tid)
        self._schedule_template_save()

    def _schedule_template_save(self):
        if self._template_save_after_id is not None:
            self.after_cancel(self._template_save_after_id)
        self._template_save_after_id = self.after(300, self._flush_template_save)

    def _flush_template_save(self):
        self._template_save_after_id = None
        try:
            self.tm._save()
        except (OSError, TypeError, ValueError) as exc:
            self._append_log(f"Could not save template settings: {exc}")
            self._schedule_failed_template_retry()

    def _schedule_failed_template_retry(self):
        if (
            self._template_save_after_id is not None
            or getattr(self, "_close_when_stopped", False)
            or getattr(self, "_destroy_scheduled", False)
            or getattr(self, "_shutting_down", False)
        ):
            return
        try:
            self._template_save_after_id = self.after(
                2000,
                self._flush_template_save,
            )
        except tk.TclError:
            pass

    def _open_region_picker(self, on_picked):
        self.withdraw()

        def launch():
            try:
                ScreenRegionPicker(self, on_picked, on_cancel=self.deiconify)
            except Exception as exc:
                self.deiconify()
                messagebox.showerror("Screen capture failed", str(exc), parent=self)

        self.after(200, launch)

    # ---------------- add / remove ----------------
    def _add_from_file(self):
        path = filedialog.askopenfilename(
            title="Select icon image",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp"), ("All files", "*.*")]
        )
        if not path:
            return
        img = cv2.imread(path)
        if img is None:
            messagebox.showerror("Error", "Could not read that image file.")
            return
        self._prompt_name_and_add(img)

    def _add_from_screen(self):
        self._open_region_picker(self._on_region_picked)

    def _on_region_picked(self, image_bgr, abs_box):
        self.deiconify()
        self._prompt_name_and_add(
            image_bgr,
            template_reference_size=self._reference_size_for_capture(abs_box),
        )

    def _reference_size_for_capture(self, abs_box):
        title = self.target_window_var.get().strip()
        if title:
            rect = find_window_rect(title)
            if rect:
                return rect[2], rect[3]
        try:
            with mss.MSS() as capture:
                index = monitor_index_for_rect(capture.monitors, abs_box)
                if index is not None:
                    monitor = capture.monitors[index]
                    return int(monitor["width"]), int(monitor["height"])
        except Exception:
            pass
        return None

    def _prompt_name_and_add(self, image_bgr, template_reference_size=None):
        name = simpledialog.askstring("Name this icon", "Give this icon a short name:", parent=self)
        if name is None:
            self._append_log("Adding template cancelled.")
            return
        name = name.strip()
        if not name:
            name = f"icon_{len(self.tm.snapshot()) + 1}"
        try:
            self.tm.add(
                image_bgr,
                name,
                DEFAULT_THRESHOLD,
                template_reference_size=template_reference_size,
            )
        except (OSError, ValueError) as exc:
            messagebox.showerror("Could not add icon", str(exc), parent=self)
            return
        self._refresh_list()
        self._append_log(f"Added template '{name}'.")

    def _remove_selected(self):
        tid = self._selected_id()
        if tid is None:
            return
        entry = self.tm.get(tid)
        if entry is None:
            return
        name = entry["name"]
        if messagebox.askyesno("Remove", f"Remove '{name}' from watch list?"):
            try:
                self.tm.remove(tid)
            except (OSError, TypeError, ValueError) as exc:
                messagebox.showerror("Could not remove icon", str(exc), parent=self)
                return
            self._refresh_list()
            self._append_log(f"Removed template '{name}'.")

    def _region_metadata_from_abs_box(self, abs_box):
        title = self.target_window_var.get().strip()
        if title:
            window_rect = find_window_rect(title)
            if not window_rect:
                raise ValueError(f"No visible window title contains: {title}")
            return {
                "region": relative_region_from_window(abs_box, window_rect),
                "region_mode": "window",
                "region_ratio": proportional_region_from_window(abs_box, window_rect),
                "region_window_size": (window_rect[2], window_rect[3]),
            }
        with mss.MSS() as capture:
            index = monitor_index_for_rect(capture.monitors, abs_box)
            if index is None:
                raise ValueError("The selected region is outside every monitor.")
            selected_monitor = monitor_rect(capture.monitors[index])
        return {
            "region": relative_region_from_window(abs_box, selected_monitor),
            "region_mode": "monitor",
            "region_ratio": proportional_region_from_window(
                abs_box,
                selected_monitor,
            ),
            "region_window_size": (
                selected_monitor[2],
                selected_monitor[3],
            ),
        }

    def _set_selected_icon_region(self):
        tid = self._selected_id()
        if tid is None:
            messagebox.showinfo("No icon selected", "Select an icon first.")
            return
        self._open_region_picker(
            lambda image_bgr, abs_box, selected_tid=tid: self._on_icon_region_picked(
                image_bgr,
                abs_box,
                selected_tid,
            )
        )

    def _on_icon_region_picked(self, _image_bgr, abs_box, tid):
        self.deiconify()
        try:
            meta = self._region_metadata_from_abs_box(abs_box)
        except Exception as exc:
            messagebox.showerror("Window lookup failed", str(exc), parent=self)
            return
        try:
            self.tm.set_region(
                tid,
                meta["region"],
                meta["region_mode"],
                meta["region_ratio"],
                meta["region_window_size"],
            )
        except (OSError, TypeError, ValueError) as exc:
            messagebox.showerror("Could not save icon region", str(exc), parent=self)
            return
        entry = self.tm.get(tid)
        self._refresh_list(selected_tid=tid)
        self._update_icon_region_label(entry)
        name = entry["name"] if entry else "selected icon"
        x, y, w, h = meta["region"]
        self._append_log(f"Icon region set for '{name}' to {w}x{h} at {x},{y}.")

    def _clear_selected_icon_region(self):
        tid = self._selected_id()
        if tid is None:
            return
        entry = self.tm.get(tid)
        name = entry["name"] if entry else "selected icon"
        try:
            self.tm.clear_region(tid)
        except (OSError, TypeError, ValueError) as exc:
            messagebox.showerror("Could not clear icon region", str(exc), parent=self)
            return
        self._refresh_list(selected_tid=tid)
        self._update_icon_region_label()
        self._append_log(f"Icon region cleared for '{name}'.")

    def _selected_monitor_box(self, target_rect=None, screenshot_size=None):
        monitor_filter = self._selected_monitor_filter()
        with mss.MSS() as sct:
            target_index = (
                monitor_index_for_rect(sct.monitors, target_rect)
                if target_rect is not None
                else None
            )
            if target_index is not None:
                mon = sct.monitors[target_index]
            elif monitor_filter is not None and monitor_filter < len(sct.monitors):
                mon = sct.monitors[monitor_filter]
            elif screenshot_size is not None:
                width, height = screenshot_size
                matching = [
                    candidate
                    for candidate in sct.monitors[1:]
                    if int(candidate["width"]) == int(width)
                    and int(candidate["height"]) == int(height)
                ]
                mon = matching[0] if len(matching) == 1 else sct.monitors[0]
            else:
                mon = sct.monitors[0]
        return (mon["left"], mon["top"], mon["width"], mon["height"])

    def _resolve_global_scan_region_for_display(self):
        title = self.target_window_var.get().strip()
        window_rect = find_window_rect(title) if title else None
        if title and not window_rect:
            raise ValueError(f"No visible window title contains: {title}")
        if self.scan_region is None:
            return window_rect
        if self.scan_region_mode == "window":
            if not title or window_rect is None:
                raise ValueError("Select the target window before showing this region.")
            return resolve_window_region(
                self.scan_region,
                window_rect,
                self.scan_region_ratio,
                self.scan_region_window_size,
            )
        if self.scan_region_mode == "monitor" and self.scan_region is not None:
            monitor_box = self._selected_monitor_box(target_rect=window_rect)
            return resolve_saved_capture_region(
                self.scan_region,
                "monitor",
                self.scan_region_ratio,
                self.scan_region_window_size,
                monitor_rect=monitor_box,
            )
        return self.scan_region

    def _show_selected_icon_region(self):
        tid = self._selected_id()
        if tid is None:
            messagebox.showinfo("No icon selected", "Select an icon first.")
            return
        entry = self.tm.get(tid)
        if entry is None:
            return
        try:
            global_region = self._resolve_global_scan_region_for_display()
            region = resolve_item_absolute_region(
                entry,
                global_region,
                self.target_window_var.get().strip(),
                find_window_rect,
                self._selected_monitor_box(),
            )
            if region is REGION_UNAVAILABLE:
                raise ValueError(
                    "The selected icon region is window-relative, but the target window was not found."
                )
            if region is None:
                region = self._selected_monitor_box()
        except Exception as exc:
            messagebox.showerror("Could not show region", str(exc), parent=self)
            return

        RegionOverlay(self, region, entry["name"])
        self._append_log(f"Showing scan region for '{entry['name']}'.")

    def _set_scan_region(self):
        self._open_region_picker(self._on_scan_region_picked)

    def _on_scan_region_picked(self, _image_bgr, abs_box):
        self.deiconify()
        try:
            meta = self._region_metadata_from_abs_box(abs_box)
        except Exception as exc:
            messagebox.showerror("Window lookup failed", str(exc), parent=self)
            return
        self.scan_region = meta["region"]
        self.scan_region_mode = meta["region_mode"]
        self.scan_region_ratio = meta["region_ratio"]
        self.scan_region_window_size = meta["region_window_size"]
        self._update_region_label()
        x, y, w, h = self.scan_region
        scope = {
            "window": "window-relative",
            "monitor": "monitor-relative",
        }.get(self.scan_region_mode, "screen")
        self._append_log(f"Scan region set to {w}x{h} at {x},{y} ({scope}).")
        self._save_settings()

    def _clear_scan_region(self):
        self.scan_region = None
        self.scan_region_mode = (
            "window" if self.target_window_var.get().strip() else "monitor"
        )
        self.scan_region_ratio = None
        self.scan_region_window_size = None
        self._update_region_label()
        self._append_log("Scan region cleared.")
        self._save_settings()

    def _selected_monitor_filter(self):
        value = self.monitor_var.get()
        if value == "All monitors":
            return None
        try:
            return int(value.split()[-1])
        except (ValueError, IndexError):
            return None

    def _cooldown_seconds(self):
        try:
            value = float(self.cooldown_var.get())
            return max(0.0, value) if math.isfinite(value) else DEFAULT_COOLDOWN_SEC
        except (tk.TclError, TypeError, ValueError, OverflowError):
            return DEFAULT_COOLDOWN_SEC

    def _alert_volume(self):
        try:
            value = float(self.volume_var.get()) / 100.0
            return min(1.0, max(0.0, value)) if math.isfinite(value) else DEFAULT_ALERT_VOLUME
        except (tk.TclError, TypeError, ValueError, OverflowError):
            return DEFAULT_ALERT_VOLUME

    # ---------------- monitoring control ----------------
    def _start_watching(self):
        items = self.tm.snapshot()
        if not items:
            messagebox.showinfo("No icons", "Add at least one icon to watch first.")
            return
        if not any(item.get("enabled", True) for item in items):
            messagebox.showinfo(
                "No icons selected",
                "Check 'Detect this icon' for at least one watched icon first.",
            )
            return
        if self.watcher is not None:
            if self.watcher.is_alive():
                self._append_log("Watcher is already running or still stopping.")
                return
            self.watcher = None
        self._save_settings()
        self._errored_watcher = None
        self.watcher = WatcherThread(
            self.tm,
            self.event_queue,
            self.log_queue,
            monitor_filter=self._selected_monitor_filter(),
            scan_region=self.scan_region,
            scan_region_mode=self.scan_region_mode,
            scan_region_ratio=self.scan_region_ratio,
            scan_region_window_size=self.scan_region_window_size,
            target_window_title=self.target_window_var.get().strip(),
            use_grayscale=self.grayscale_var.get(),
            debug=self.debug_var.get(),
            cooldown_sec=self._cooldown_seconds(),
        )
        self.watcher.start()
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.status_label.config(text="Watching", style="Watching.Status.TLabel")
        status_pulse = getattr(self, "_watcher_status_pulse", None)
        if self.ui_preferences.animations_enabled and status_pulse is not None:
            status_pulse.start()

    def _stop_watching(self):
        watcher = self.watcher
        if watcher is None:
            self._set_idle_controls()
            return True
        watcher.stop()
        if watcher.is_alive():
            status_pulse = getattr(self, "_watcher_status_pulse", None)
            if status_pulse is not None:
                status_pulse.stop("Idle.Status.TLabel")
            self.start_btn.config(state="disabled")
            self.stop_btn.config(state="disabled")
            self.status_label.config(text="Stopping…", style="Idle.Status.TLabel")
            self._append_log("Stopping watcher; waiting for the current match operation to finish.")
            return False
        self._watcher_finished(watcher)
        return True

    def _set_idle_controls(self):
        status_pulse = getattr(self, "_watcher_status_pulse", None)
        if status_pulse is not None:
            status_pulse.stop("Idle.Status.TLabel")
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.status_label.config(text="Idle", style="Idle.Status.TLabel")

    def _watcher_finished(self, watcher):
        if watcher is not self.watcher:
            return
        errored = watcher is self._errored_watcher
        self.watcher = None
        if errored:
            status_pulse = getattr(self, "_watcher_status_pulse", None)
            if status_pulse is not None:
                status_pulse.stop("Error.Status.TLabel")
            self.start_btn.config(state="normal")
            self.stop_btn.config(state="disabled")
            self.status_label.config(text="Watcher stopped", style="Error.Status.TLabel")
        else:
            self._set_idle_controls()
        if self._close_when_stopped:
            self._finish_app_quit()

    def _test_alert(self):
        tid = self._selected_id()
        entry = self.tm.get(tid) if tid is not None else None
        name = entry["name"] if entry else "Test"
        thumb = None
        if entry is not None:
            rgb = cv2.cvtColor(entry["image"], cv2.COLOR_BGR2RGB)
            thumb = Image.fromarray(rgb)
            thumb.thumbnail((64, 64))
        play_alert_sound(self._alert_volume())
        AlertPopup(self, name, monitor="-", thumb_img=thumb)

    def _test_screenshot(self):
        if self._screenshot_test_running:
            self._append_log("A screenshot test is already running.")
            return
        items = self.tm.snapshot()
        if not items:
            messagebox.showinfo("No icons", "Add at least one icon to watch first.")
            return
        if not any(item.get("enabled", True) for item in items):
            messagebox.showinfo(
                "No icons selected",
                "Check 'Detect this icon' for at least one watched icon first.",
            )
            return
        path = filedialog.askopenfilename(
            title="Select screenshot image",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp"), ("All files", "*.*")]
        )
        if not path:
            return
        try:
            global_region = self._resolve_global_scan_region_for_display()
            target_window_title = self.target_window_var.get().strip()
            target_rect = (
                find_window_rect(target_window_title)
                if target_window_title
                else None
            )
            with Image.open(path) as screenshot_image:
                screenshot_size = screenshot_image.size
            monitor_box = self._selected_monitor_box(
                target_rect=target_rect,
                screenshot_size=screenshot_size,
            )
        except Exception as exc:
            messagebox.showerror("Could not resolve scan region", str(exc), parent=self)
            return
        use_grayscale = bool(self.grayscale_var.get())
        is_full_monitor_screenshot = screenshot_size == (
            monitor_box[2],
            monitor_box[3],
        )
        if target_rect:
            current_size = (target_rect[2], target_rect[3])
        elif is_full_monitor_screenshot or self._selected_monitor_filter() is not None:
            current_size = (monitor_box[2], monitor_box[3])
        else:
            # A crop plus "All monitors" does not contain enough information
            # to identify its source resolution. Legacy scales are safer than
            # treating the virtual desktop as one enormous monitor.
            current_size = None
        template_items = [
            item
            for item in self.tm.snapshot(
                use_grayscale=use_grayscale,
                current_window_size=current_size,
                enabled_only=True,
            )
            if item.get("enabled", True)
        ]
        if is_full_monitor_screenshot:
            test_region = global_region
            origin = (monitor_box[0], monitor_box[1])
        else:
            test_region = None
            origin = (0, 0)
            self._append_log(
                "Cropped screenshot detected; testing the entire image "
                "without saved screen regions."
            )
        self._screenshot_test_running = True
        self.test_screenshot_btn.config(state="disabled", text="Testing…")
        self._append_log("Screenshot test started in the background.")

        def _worker():
            try:
                results = test_detection_on_screenshot(
                    path,
                    template_items,
                    use_grayscale=use_grayscale,
                    region=test_region,
                    region_origin=origin,
                    target_window_title=target_window_title,
                    monitor_box=monitor_box,
                    apply_saved_regions=is_full_monitor_screenshot,
                )
                event = {"type": "screenshot_test_complete", "results": results}
            except Exception as exc:
                event = {"type": "screenshot_test_error", "error": str(exc)}
            self.event_queue.put(event)

        threading.Thread(target=_worker, daemon=True).start()

    # ---------------- queue polling ----------------
    def _append_log(self, msg):
        self.log_text.config(state="normal")
        self.log_text.insert("end", f"[{time.strftime('%H:%M:%S')}] {msg}\n")
        self._log_line_count += 1
        extra_lines = self._log_line_count - self.log_text_max_lines
        if extra_lines > 0:
            self.log_text.delete("1.0", f"{extra_lines + 1}.0")
            self._log_line_count -= extra_lines
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def _poll_queues(self):
        for msg in _drain_queue(self.log_queue):
            self._append_log(msg)
        for ev in _drain_queue(self.event_queue):
            event_type = ev.get("type")
            if event_type == "ui_command":
                command = ev.get("command")
                callbacks = {
                    "show": self._show_from_tray,
                    "toggle": self._toggle_watching,
                    "test_alert": self._test_alert,
                    "quit": self._quit_from_tray,
                }
                callback = callbacks.get(command)
                if callback is not None:
                    callback()
                continue
            if event_type in ("watcher_error", "watcher_stopped"):
                if ev.get("watcher") not in (None, self.watcher):
                    continue
                self._errored_watcher = ev.get("watcher", self.watcher)
                status_pulse = getattr(self, "_watcher_status_pulse", None)
                if status_pulse is not None:
                    status_pulse.stop("Error.Status.TLabel")
                self.status_label.config(text="Watcher stopped", style="Error.Status.TLabel")
                if not self._close_when_stopped:
                    messagebox.showwarning(
                        "Monitoring stopped",
                        f"The watcher stopped because of an error:\n{ev.get('error', 'Unknown error')}"
                    )
                continue
            if event_type == "watcher_finished":
                self._watcher_finished(ev.get("watcher"))
                continue
            if event_type == "screenshot_test_error":
                self._screenshot_test_running = False
                self.test_screenshot_btn.config(state="normal", text="Test screenshot")
                messagebox.showerror("Screenshot test failed", ev.get("error", "Unknown error"))
                continue
            if event_type == "screenshot_test_complete":
                self._screenshot_test_running = False
                self.test_screenshot_btn.config(state="normal", text="Test screenshot")
                lines = [
                    f"{result['name']}: unavailable"
                    if result.get("unavailable") else
                    f"{result['name']}: {result['score']:.2f} / {result['threshold']:.2f}"
                    f" {'MATCH' if result['matched'] else 'no match'}"
                    for result in ev.get("results", [])
                ]
                messagebox.showinfo("Screenshot test", "\n".join(lines) or "No templates tested.")
                self._append_log("Screenshot test: " + "; ".join(lines))
                continue
            entry = self.tm.get(ev["id"])
            thumb = None
            if entry is not None:
                rgb = cv2.cvtColor(entry["image"], cv2.COLOR_BGR2RGB)
                thumb = Image.fromarray(rgb)
                thumb.thumbnail((64, 64))
            play_alert_sound(self._alert_volume())
            AlertPopup(self, ev["name"], ev["monitor"], thumb)
            self._append_log(f"ALERT: '{ev['name']}' seen on monitor {ev['monitor']} (score {ev['score']:.2f})")
        self.after(150, self._poll_queues)

    def _finish_app_quit(self):
        if self._destroy_scheduled:
            return
        if self.watcher is not None and self.watcher.is_alive():
            return
        self._destroy_scheduled = True
        self.after_idle(self.winfo_toplevel().destroy)

    def _request_app_quit(self):
        self._close_when_stopped = True
        self.shutdown()
        self._finish_app_quit()

    def shutdown(self):
        self._shutting_down = True
        status_pulse = getattr(self, "_watcher_status_pulse", None)
        if status_pulse is not None:
            status_pulse.stop()
        if self._settings_save_after_id is not None:
            self.after_cancel(self._settings_save_after_id)
            self._save_settings()
        if self._template_save_after_id is not None:
            self.after_cancel(self._template_save_after_id)
            self._flush_template_save()
        self._save_settings()
        self._cleanup_tray()
        self._cleanup_hotkeys()
        self._stop_watching()

    def on_close(self):
        if self._settings_save_after_id is not None:
            self.after_cancel(self._settings_save_after_id)
            self._save_settings()
        if self._template_save_after_id is not None:
            self.after_cancel(self._template_save_after_id)
            self._flush_template_save()
        self._save_settings()
        if not self.embedded and self.tray_var.get() and HAVE_PYSTRAY:
            self.withdraw()
            self._append_log("Window hidden to system tray.")
            return
        if not self.embedded:
            self._request_app_quit()
        else:
            self.shutdown()


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Icon Alert Watcher")
        self.geometry("1040x760")
        self.minsize(900, 680)
        configure_theme(self)
        self.content = AlertWatcherFrame(self, embedded=False)
        self.content.pack(fill="both", expand=True)

    def on_close(self):
        self.content.on_close()


if __name__ == "__main__":
    instance_lock = SingleInstanceLock()
    if not instance_lock.acquire():
        root = tk.Tk()
        root.withdraw()
        messagebox.showwarning(
            "Icon Alert Watcher already running",
            "Another copy of Icon Alert Watcher is already running."
        )
        root.destroy()
        sys.exit(1)

    try:
        app = App()
        app.protocol("WM_DELETE_WINDOW", app.on_close)
        app.mainloop()
    finally:
        instance_lock.release()
