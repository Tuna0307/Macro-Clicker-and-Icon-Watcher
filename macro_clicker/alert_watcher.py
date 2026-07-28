"""Icon Alerts implementation for the combined PC Macro Builder application.

The watcher scans one or more monitors for user-managed icon templates and
raises a sound and on-top popup when an enabled template appears. It supports
capturing templates from the screen, per-icon enablement, saved scan regions,
and one alert per appearance.

Install the project dependencies from ``requirements.txt`` and start the
combined application with ``python -m macro_clicker`` or the Windows launcher.
"""

import ctypes
import copy
import errno
import json
import math
import os
import queue
import struct
import sys
import threading
import time
import tkinter as tk
from difflib import get_close_matches
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
    settings_load_errors,
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
                if not ctypes.windll.kernel32.GetExitCodeProcess(
                    handle, ctypes.byref(exit_code)
                ):
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
        locked = False
        try:
            if os.path.getsize(self.path) == 0:
                os.write(fd, b" ")
            os.lseek(fd, 0, os.SEEK_SET)
            try:
                if sys.platform == "win32":
                    import msvcrt

                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                if isinstance(exc, BlockingIOError) or exc.errno in {
                    errno.EACCES,
                    errno.EAGAIN,
                }:
                    os.close(fd)
                    return False
                raise
            locked = True
            os.ftruncate(fd, 0)
            os.write(fd, f"{os.getpid()}\n".encode("ascii"))
            os.fsync(fd)
        except Exception:
            if locked:
                try:
                    os.lseek(fd, 0, os.SEEK_SET)
                    if sys.platform == "win32":
                        import msvcrt

                        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
            os.close(fd)
            raise
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


def _monitor_unique_id(monitor):
    """Return the stable MSS monitor identity when the backend provides one."""
    try:
        value = monitor.get("unique_id")
    except (AttributeError, TypeError):
        return None
    if value is None or isinstance(value, bool):
        return None
    value = str(value).strip()
    return value or None


def _monitor_unique_ids_available(monitors):
    return any(_monitor_unique_id(monitor) is not None for monitor in monitors[1:])


def _resolve_monitor_binding(monitors, monitor_index=None, monitor_unique_id=None):
    """Resolve a saved physical-monitor binding without redirecting it.

    Stable identities take precedence whenever the backend exposes at least
    one of them. Ordinal fallback is retained for legacy settings and capture
    backends that expose no stable identities at all.
    """

    if monitor_unique_id is not None:
        saved_unique_id = str(monitor_unique_id)
        unique_ids_available = False
        for index, monitor in enumerate(monitors[1:], start=1):
            current_unique_id = _monitor_unique_id(monitor)
            unique_ids_available = unique_ids_available or current_unique_id is not None
            if current_unique_id == saved_unique_id:
                return index, monitor
        if unique_ids_available:
            return None
    if (
        isinstance(monitor_index, int)
        and not isinstance(monitor_index, bool)
        and 0 < monitor_index < len(monitors)
    ):
        return monitor_index, monitors[monitor_index]
    return None


def _item_matches_monitor_identity(item, monitor_index=None, monitor_unique_id=None):
    saved_unique_id = item.get("monitor_unique_id")
    if saved_unique_id is not None and monitor_unique_id is not None:
        return str(saved_unique_id) == str(monitor_unique_id)
    saved_index = item.get("monitor_index")
    if saved_index is not None and monitor_index is not None:
        return saved_index == monitor_index
    return True


# --------------------------------------------------------------------------
# Detection core
# --------------------------------------------------------------------------


def _region_relative_to_origin(region, origin):
    if region is None:
        return None
    x, y, width, height = region
    return (x - origin[0], y - origin[1], width, height)


def test_detection_on_screenshot(
    path,
    template_items,
    use_grayscale=False,
    region=None,
    region_origin=(0, 0),
    target_window_title="",
    window_rect_provider=find_window_rect,
    monitor_box=None,
    apply_saved_regions=True,
    monitor_index=None,
    monitor_unique_id=None,
):
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
        if (
            monitor_index is not None or monitor_unique_id is not None
        ) and not _item_matches_monitor_identity(
            item,
            monitor_index,
            monitor_unique_id,
        ):
            results.append(
                {
                    "id": item.get("id"),
                    "name": item["name"],
                    "threshold": item.get("threshold", DEFAULT_THRESHOLD),
                    "score": -1.0,
                    "loc": None,
                    "scale": 1.0,
                    "matched": False,
                    "unavailable": True,
                    "reason": "saved monitor does not match this screenshot",
                }
            )
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
            results.append(
                {
                    "id": item.get("id"),
                    "name": item["name"],
                    "threshold": item.get("threshold", DEFAULT_THRESHOLD),
                    "score": -1.0,
                    "loc": None,
                    "scale": 1.0,
                    "matched": False,
                    "unavailable": True,
                }
            )
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
        results.append(
            {
                "id": item.get("id"),
                "name": item["name"],
                "threshold": threshold,
                "score": score,
                "loc": loc,
                "scale": scale,
                "matched": score >= threshold,
            }
        )
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
            if (
                self.last_alert_at is not None
                and now - self.last_alert_at < self.cooldown_sec
            ):
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
    _MANIFEST_FIELDS = frozenset({"items"})
    _ITEM_FIELDS = frozenset(
        {
            "id",
            "name",
            "file",
            "enabled",
            "threshold",
            "match_mode",
            "region",
            "region_mode",
            "region_ratio",
            "region_window_size",
            "template_reference_size",
            "template_reference_space",
            "monitor_index",
            "monitor_unique_id",
        }
    )

    def __init__(self):
        self.items = {}  # id -> {"name", "file", "threshold", "image"(np.array)}
        # Keep manifest records whose image is temporarily unreadable.  They
        # are omitted from runtime matching, but an unrelated settings save
        # must not silently erase their user-authored metadata.
        self._unreadable_entries = {}
        self._lock = threading.RLock()
        self._next_id = 1
        self.load_warnings = []
        self._manifest_write_blocked = False
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
            raise ValueError(
                f"Template path escapes the template directory: {filename!r}"
            )
        return candidate

    @staticmethod
    def _valid_region(value, *, allow_negative_position=True):
        if value is None or isinstance(value, (str, bytes, dict)):
            return None
        try:
            values = tuple(value)
            if any(isinstance(v, bool) for v in values):
                return None
            if any(
                isinstance(v, float) and (not math.isfinite(v) or not v.is_integer())
                for v in values
            ):
                return None
            region = tuple(int(v) for v in values)
        except (TypeError, ValueError, OverflowError):
            return None
        if (
            len(region) != 4
            or region[2] <= 0
            or region[3] <= 0
            or (not allow_negative_position and (region[0] < 0 or region[1] < 0))
        ):
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
            values = tuple(value)
            if any(isinstance(v, bool) for v in values):
                return None
            if any(
                isinstance(v, float) and (not math.isfinite(v) or not v.is_integer())
                for v in values
            ):
                return None
            size = tuple(int(v) for v in values)
        except (TypeError, ValueError, OverflowError):
            return None
        if len(size) != 2 or size[0] <= 0 or size[1] <= 0:
            return None
        return size

    @classmethod
    def _unknown_entry_fields(cls, entry):
        return [str(key) for key in entry if key not in cls._ITEM_FIELDS]

    @classmethod
    def _unknown_field_message(cls, field):
        suggestion = get_close_matches(field, cls._ITEM_FIELDS, n=1)
        suffix = f"; did you mean '{suggestion[0]}'?" if suggestion else ""
        return f"unknown field '{field}'{suffix}"

    @staticmethod
    def _valid_monitor_index(value):
        return (
            value
            if isinstance(value, int) and not isinstance(value, bool) and value > 0
            else None
        )

    @staticmethod
    def _valid_monitor_unique_id(value):
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            return None
        value = str(value).strip()
        return value or None

    @staticmethod
    def _valid_reference_space(value):
        return (
            value if isinstance(value, str) and value in {"window", "monitor"} else None
        )

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
                tid = int(stem[len("template_") :])
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
            self._manifest_write_blocked = True
            return
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            self.load_warnings.append("Template manifest must contain an 'items' list.")
            self._manifest_write_blocked = True
            return
        unknown_manifest_fields = [
            str(key) for key in data if key not in self._MANIFEST_FIELDS
        ]
        if unknown_manifest_fields:
            fields = ", ".join(repr(field) for field in unknown_manifest_fields)
            self.load_warnings.append(
                f"Template manifest contains unknown field(s) {fields}; "
                "no templates were loaded and the source file will not be rewritten."
            )
            self._manifest_write_blocked = True
            return
        used_paths = set()
        with self._lock:
            for entry in data["items"]:
                if not isinstance(entry, dict):
                    self.load_warnings.append(
                        "Ignored a malformed template manifest entry; "
                        "the source file will not be rewritten."
                    )
                    self._manifest_write_blocked = True
                    continue
                tid = entry.get("id")
                if isinstance(tid, bool) or not isinstance(tid, int) or tid <= 0:
                    self.load_warnings.append(
                        "Ignored a template with an invalid ID; "
                        "the source file will not be rewritten."
                    )
                    self._manifest_write_blocked = True
                    continue
                self._next_id = max(self._next_id, tid + 1)
                if tid in self.items or tid in self._unreadable_entries:
                    self.load_warnings.append(
                        f"Ignored duplicate template ID {tid}; "
                        "the source file will not be rewritten."
                    )
                    self._manifest_write_blocked = True
                    continue
                entry_errors = [
                    self._unknown_field_message(field)
                    for field in self._unknown_entry_fields(entry)
                ]
                try:
                    path = self._safe_template_path(entry.get("file"))
                except ValueError as exc:
                    entry_errors.append(str(exc))
                    path = None
                if "name" in entry and (
                    not isinstance(entry["name"], str) or not entry["name"].strip()
                ):
                    entry_errors.append("name must be non-empty text")
                if "enabled" in entry and not isinstance(entry["enabled"], bool):
                    entry_errors.append("enabled must be true or false")
                try:
                    if isinstance(entry.get("threshold", DEFAULT_THRESHOLD), bool):
                        raise ValueError
                    threshold = float(entry.get("threshold", DEFAULT_THRESHOLD))
                    if not math.isfinite(threshold) or not 0.0 <= threshold <= 1.0:
                        raise ValueError
                except (TypeError, ValueError, OverflowError):
                    threshold = DEFAULT_THRESHOLD
                    entry_errors.append("threshold must be a finite number from 0 to 1")
                raw_match_mode = entry.get("match_mode", LEGACY_MATCH_MODE)
                if (
                    not isinstance(raw_match_mode, str)
                    or raw_match_mode not in MATCH_MODE_VALUES
                ):
                    entry_errors.append("match_mode is invalid")
                    match_mode = LEGACY_MATCH_MODE
                else:
                    match_mode = normalize_match_mode(raw_match_mode)
                region_mode = entry.get("region_mode", "screen")
                if not isinstance(region_mode, str) or region_mode not in {
                    "screen",
                    "window",
                    "monitor",
                }:
                    entry_errors.append("region_mode is invalid")
                    region_mode = "screen"
                raw_region = entry.get("region")
                region = self._valid_region(
                    raw_region,
                    allow_negative_position=region_mode == "screen",
                )
                if raw_region is not None and region is None:
                    entry_errors.append(
                        "region must contain whole-number coordinates with positive "
                        "size; relative offsets cannot be negative"
                    )
                raw_ratio = entry.get("region_ratio")
                region_ratio = self._valid_ratio(raw_ratio)
                if raw_ratio is not None and region_ratio is None:
                    entry_errors.append(
                        "region_ratio must describe a finite positive contained box"
                    )
                raw_region_size = entry.get("region_window_size")
                region_window_size = self._valid_window_size(raw_region_size)
                if raw_region_size is not None and region_window_size is None:
                    entry_errors.append(
                        "region_window_size must contain positive whole numbers"
                    )
                raw_reference_size = entry.get("template_reference_size")
                template_reference_size = self._valid_window_size(raw_reference_size)
                if raw_reference_size is not None and template_reference_size is None:
                    entry_errors.append(
                        "template_reference_size must contain positive whole numbers"
                    )
                raw_reference_space = entry.get("template_reference_space")
                template_reference_space = self._valid_reference_space(
                    raw_reference_space
                )
                if raw_reference_space is not None and template_reference_space is None:
                    entry_errors.append(
                        "template_reference_space must be 'window' or 'monitor'"
                    )
                raw_monitor_index = entry.get("monitor_index")
                monitor_index = self._valid_monitor_index(raw_monitor_index)
                if raw_monitor_index is not None and monitor_index is None:
                    entry_errors.append("monitor_index must be a positive whole number")
                raw_monitor_unique_id = entry.get("monitor_unique_id")
                monitor_unique_id = self._valid_monitor_unique_id(raw_monitor_unique_id)
                if raw_monitor_unique_id is not None and monitor_unique_id is None:
                    entry_errors.append(
                        "monitor_unique_id must be non-empty text or an integer"
                    )
                if region_mode == "screen" and (
                    raw_ratio is not None or raw_region_size is not None
                ):
                    entry_errors.append(
                        "screen regions cannot contain relative resize metadata"
                    )
                if region_mode in {"window", "monitor"} and region is not None:
                    if (region_ratio is None) != (region_window_size is None):
                        entry_errors.append(
                            "relative regions need both ratio and reference size"
                        )
                if region_mode != "monitor" and (
                    raw_monitor_index is not None or raw_monitor_unique_id is not None
                ):
                    entry_errors.append(
                        "monitor identity is only valid for monitor-relative regions"
                    )
                if entry_errors:
                    details = "; ".join(entry_errors)
                    self.load_warnings.append(
                        f"Template ID {tid} was disabled because {details}."
                    )
                    self._unreadable_entries[tid] = copy.deepcopy(entry)
                    continue
                assert path is not None
                normalized_path = os.path.normcase(path)
                if normalized_path in used_paths:
                    self.load_warnings.append(
                        f"Ignored template ID {tid}; its image file is already in use."
                    )
                    self._unreadable_entries[tid] = copy.deepcopy(entry)
                    continue
                used_paths.add(normalized_path)
                img = cv2.imread(path)
                if img is None:
                    self.load_warnings.append(
                        f"Could not read template image for ID {tid}: {entry.get('file')!r}"
                    )
                    self._unreadable_entries[tid] = copy.deepcopy(entry)
                    continue
                name = entry.get("name")
                if name is None:
                    name = f"icon_{tid}"
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
                    "template_reference_space": template_reference_space,
                    "monitor_index": monitor_index,
                    "monitor_unique_id": monitor_unique_id,
                    "image": img,
                    "variant_cache": {},
                }

    def _save(self):
        with self._lock:
            if self._manifest_write_blocked:
                raise ValueError(
                    "The template manifest contains malformed data. "
                    "Fix the reported fields before changing templates."
                )
            items_by_id = {
                tid: copy.deepcopy(entry)
                for tid, entry in self._unreadable_entries.items()
            }
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
                if v.get("template_reference_space") is not None:
                    item["template_reference_space"] = v["template_reference_space"]
                if v.get("monitor_index") is not None:
                    item["monitor_index"] = v["monitor_index"]
                if v.get("monitor_unique_id") is not None:
                    item["monitor_unique_id"] = v["monitor_unique_id"]
                items_by_id[tid] = item
            items = [items_by_id[tid] for tid in sorted(items_by_id)]
            _atomic_write_json(MANIFEST_PATH, {"items": items})

    def add(
        self,
        image_bgr,
        name,
        threshold=DEFAULT_THRESHOLD,
        match_mode=DEFAULT_NEW_MATCH_MODE,
        template_reference_size=None,
        template_reference_space=None,
    ):
        with self._lock:
            tid = self._next_id
            filename = f"template_{tid}.png"
            path = self._safe_template_path(filename)
            while (
                tid in self.items
                or tid in self._unreadable_entries
                or os.path.exists(path)
            ):
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
            parsed_match_mode = (
                normalize_match_mode(match_mode, default="")
                if isinstance(match_mode, str)
                else ""
            )
            if parsed_match_mode not in MATCH_MODE_VALUES:
                raise ValueError("Unknown template detection type")
            parsed_reference_size = self._valid_window_size(template_reference_size)
            if template_reference_size is not None and parsed_reference_size is None:
                raise ValueError(
                    "Template reference size must contain a positive width and height"
                )
            parsed_reference_space = self._valid_reference_space(
                template_reference_space
            )
            if template_reference_space is not None and parsed_reference_space is None:
                raise ValueError(
                    "Template reference space must be 'window' or 'monitor'"
                )
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
                "template_reference_space": parsed_reference_space,
                "monitor_index": None,
                "monitor_unique_id": None,
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
        if not isinstance(match_mode, str):
            raise ValueError("Unknown template detection type")
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

    def set_region(
        self,
        tid,
        region,
        region_mode="screen",
        region_ratio=None,
        region_window_size=None,
        monitor_index=None,
        monitor_unique_id=None,
    ):
        if region_mode not in ("screen", "window", "monitor"):
            raise ValueError("Region mode must be 'screen', 'window', or 'monitor'.")
        parsed_region = self._valid_region(
            region,
            allow_negative_position=region_mode == "screen",
        )
        if region is not None and parsed_region is None:
            raise ValueError(
                "Region must contain four whole numbers with positive size; "
                "window/monitor-relative offsets cannot be negative."
            )
        parsed_ratio = self._valid_ratio(region_ratio)
        parsed_window_size = self._valid_window_size(region_window_size)
        if region_mode in ("window", "monitor") and parsed_region is not None:
            if (parsed_ratio is None) != (parsed_window_size is None):
                raise ValueError("Relative regions need both ratio and base size.")
        elif region_mode == "screen" and (
            region_ratio is not None or region_window_size is not None
        ):
            raise ValueError("Screen regions cannot contain window resize metadata.")
        parsed_monitor_index = self._valid_monitor_index(monitor_index)
        parsed_monitor_unique_id = self._valid_monitor_unique_id(monitor_unique_id)
        if monitor_index is not None and parsed_monitor_index is None:
            raise ValueError("Monitor index must be a positive whole number.")
        if monitor_unique_id is not None and parsed_monitor_unique_id is None:
            raise ValueError("Monitor identity must be non-empty text or an integer.")
        if region_mode != "monitor" and (
            monitor_index is not None or monitor_unique_id is not None
        ):
            raise ValueError(
                "Monitor identity is only valid for monitor-relative regions."
            )
        with self._lock:
            if tid not in self.items:
                return
            previous = {
                key: self.items[tid].get(key)
                for key in (
                    "region",
                    "region_mode",
                    "region_ratio",
                    "region_window_size",
                    "monitor_index",
                    "monitor_unique_id",
                )
            }
            self.items[tid]["region"] = parsed_region
            self.items[tid]["region_mode"] = region_mode
            self.items[tid]["region_ratio"] = parsed_ratio
            self.items[tid]["region_window_size"] = parsed_window_size
            self.items[tid]["monitor_index"] = parsed_monitor_index
            self.items[tid]["monitor_unique_id"] = parsed_monitor_unique_id
            try:
                self._save()
            except Exception:
                self.items[tid].update(previous)
                raise

    def clear_region(self, tid):
        self.set_region(tid, None, "screen", None, None, None, None)

    def get(self, tid):
        with self._lock:
            entry = self.items.get(tid)
            if entry is None:
                return None
            result = dict(entry)
            result["image"] = entry["image"].copy()
            result.pop("variant_cache", None)
            return result

    def _variant_context(
        self,
        entry,
        use_grayscale,
        current_window_size=None,
        current_monitor_size=None,
    ):
        match_mode = entry.get("match_mode", LEGACY_MATCH_MODE)
        grayscale_key = bool(use_grayscale) if match_mode != MATCH_MODE_TEXT else False
        reference_size = entry.get("template_reference_size") or entry.get(
            "region_window_size"
        )
        reference_space = entry.get("template_reference_space")
        if reference_space is None and entry.get("region_mode") == "monitor":
            reference_space = "monitor"
        parsed_window_size = self._valid_window_size(current_window_size)
        parsed_monitor_size = self._valid_window_size(current_monitor_size)
        if reference_space == "monitor":
            parsed_current_size = parsed_monitor_size
        elif reference_space == "window":
            parsed_current_size = parsed_window_size
        else:
            # Legacy templates did not record where their reference size came
            # from. Retain the old window-first heuristic for those entries.
            parsed_current_size = parsed_window_size or parsed_monitor_size
        key = (
            grayscale_key,
            match_mode,
            tuple(reference_size) if reference_size else None,
            parsed_current_size,
        )
        return key, grayscale_key, match_mode, reference_size, parsed_current_size

    def _variants_for_snapshot(
        self,
        tid,
        entry,
        use_grayscale,
        current_window_size=None,
        current_monitor_size=None,
        cancel_event=None,
    ):
        context = self._variant_context(
            entry,
            use_grayscale,
            current_window_size,
            current_monitor_size,
        )
        key, grayscale_key, match_mode, reference_size, parsed_current_size = context
        with self._lock:
            live_entry = self.items.get(tid)
            if live_entry is not None:
                cached = live_entry.setdefault("variant_cache", {}).get(key)
                if cached is not None:
                    return cached

        # Variant preparation can resize and rotate many images. Do that work
        # outside the manager lock so Tk selection/edit operations stay live.
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

        with self._lock:
            live_entry = self.items.get(tid)
            if live_entry is None or live_entry.get("image") is not entry["image"]:
                return variants
            live_context = self._variant_context(
                live_entry,
                use_grayscale,
                current_window_size,
                current_monitor_size,
            )
            if live_context[0] != key:
                return variants
            cache = live_entry.setdefault("variant_cache", {})
            existing = cache.get(key)
            if existing is not None:
                return existing
            if len(cache) >= 8:
                cache.pop(next(iter(cache)))
            cache[key] = variants
        return variants

    def snapshot(
        self,
        use_grayscale=None,
        current_window_size=None,
        current_monitor_size=None,
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
                    "template_reference_space": entry.get("template_reference_space"),
                    "monitor_index": entry.get("monitor_index"),
                    "monitor_unique_id": entry.get("monitor_unique_id"),
                    "image": entry["image"],
                }
                items.append(item)
        if use_grayscale is not None:
            for item in items:
                item["variants"] = self._variants_for_snapshot(
                    item["id"],
                    item,
                    use_grayscale,
                    current_window_size,
                    current_monitor_size,
                    cancel_event,
                )
        return items


# --------------------------------------------------------------------------
# Background watcher thread
# --------------------------------------------------------------------------
class _RefreshingCaptureSession:
    """Own one MSS context at a time and replace it between scan cycles."""

    def __init__(self, factory):
        self._factory = factory
        self._context = None
        self._capture = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return self._close_current(exc_type, exc, traceback)

    def _close_current(self, exc_type=None, exc=None, traceback=None):
        context = self._context
        capture = self._capture
        self._context = None
        self._capture = None
        if context is None:
            return False
        exit_method = getattr(context, "__exit__", None)
        if callable(exit_method):
            return bool(exit_method(exc_type, exc, traceback))
        close = getattr(capture, "close", None)
        if not callable(close):
            close = getattr(context, "close", None)
        if callable(close):
            close()
        return False

    @staticmethod
    def _close_unentered(context, exc_info):
        """Best-effort cleanup when a context manager fails during ``__enter__``."""

        close = getattr(context, "close", None)
        if callable(close):
            close()
            return
        exit_method = getattr(context, "__exit__", None)
        if callable(exit_method):
            exit_method(*exc_info)

    def refresh(self):
        self._close_current()
        context = self._factory()
        enter_method = getattr(context, "__enter__", None)
        try:
            capture = enter_method() if callable(enter_method) else context
        except BaseException:
            exc_info = sys.exc_info()
            try:
                self._close_unentered(context, exc_info)
            except Exception:
                # Preserve the original initialization failure. A cleanup
                # failure is secondary and the next scan cycle will retry with
                # a new context.
                pass
            raise
        self._context = context
        self._capture = capture
        return capture


class WatcherThread(threading.Thread):
    def __init__(
        self,
        template_manager,
        event_queue,
        log_queue,
        monitor_filter=None,
        monitor_unique_id=None,
        scan_region=None,
        use_grayscale=True,
        debug=False,
        cooldown_sec=DEFAULT_COOLDOWN_SEC,
        scan_region_mode="screen",
        scan_region_ratio=None,
        scan_region_window_size=None,
        target_window_title="",
        window_rect_provider=find_window_rect,
    ):
        super().__init__(daemon=True)
        self.tm = template_manager
        self.event_queue = event_queue
        self.log_queue = log_queue
        self.monitor_filter = monitor_filter
        self.monitor_unique_id = monitor_unique_id
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

    def update_config(
        self,
        *,
        monitor_filter=None,
        monitor_unique_id=None,
        scan_region=None,
        scan_region_mode="screen",
        scan_region_ratio=None,
        scan_region_window_size=None,
        target_window_title="",
        use_grayscale=True,
        debug=False,
        cooldown_sec=DEFAULT_COOLDOWN_SEC,
    ):
        with self._config_lock:
            self.monitor_filter = monitor_filter
            self.monitor_unique_id = monitor_unique_id
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
                "monitor_unique_id": self.monitor_unique_id,
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
        self.event_queue.put(
            {"type": "watcher_error", "error": str(exc), "watcher": self}
        )

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

    def _snapshot_items(
        self,
        use_grayscale=None,
        current_window_size=None,
        current_monitor_size=None,
    ):
        try:
            items = self.tm.snapshot(
                use_grayscale=use_grayscale,
                current_window_size=current_window_size,
                current_monitor_size=current_monitor_size,
                enabled_only=True,
                cancel_event=(self._stop_flag if use_grayscale is not None else None),
            )
        except TypeError as exc:
            if not any(
                name in str(exc)
                for name in (
                    "current_window_size",
                    "current_monitor_size",
                    "cancel_event",
                    "enabled_only",
                )
            ):
                raise
            try:
                items = self.tm.snapshot(
                    use_grayscale=use_grayscale,
                    current_window_size=(current_window_size or current_monitor_size),
                )
            except TypeError as fallback_exc:
                if "current_window_size" not in str(fallback_exc):
                    raise
                items = self.tm.snapshot(use_grayscale=use_grayscale)
        return [item for item in items if item.get("enabled", True)]

    @staticmethod
    def _item_matches_monitor(
        item,
        monitor_index,
        monitor,
        *,
        unique_ids_available=False,
    ):
        saved_unique_id = item.get("monitor_unique_id")
        current_unique_id = _monitor_unique_id(monitor)
        if saved_unique_id is not None and current_unique_id is not None:
            return str(saved_unique_id) == current_unique_id
        if saved_unique_id is not None and unique_ids_available:
            return False
        saved_index = item.get("monitor_index")
        if saved_index is not None:
            return saved_index == monitor_index
        # Entries saved before monitor identity support retain their legacy
        # behavior and are eligible on every monitor in the selected scope.
        return True

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
                self.event_queue.put(
                    {
                        "id": tid,
                        "name": entry["name"],
                        "monitor": monitor,
                        "score": score,
                    }
                )

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

    def _confirm_text_candidate(
        self,
        sct,
        mon,
        entry,
        config,
        initial_result,
        absolute_scan_region,
        window_rect=_WINDOW_CONTEXT_UNSET,
    ):
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
        if (
            config["scan_region_mode"] == "monitor"
            and config["scan_region"] is not None
        ):
            window_size = (window_rect[2], window_rect[3]) if window_rect else None
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
            with _RefreshingCaptureSession(mss.MSS) as capture_session:
                # monitors[0] is the combined virtual screen; skip it here,
                # we want each physical monitor captured separately.
                last_monitor_status = None
                last_capture_error = {}
                last_refresh_error_at = None
                last_debug_log = 0.0
                while not self._stop_flag.is_set():
                    config = self._config_snapshot()
                    monitor_filter = config["monitor_filter"]
                    monitor_unique_id = config["monitor_unique_id"]
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
                    # MSS caches monitor geometry on each instance. Opening a
                    # fresh context for every active scan makes hot-plug and
                    # resolution changes visible without restarting watching.
                    try:
                        sct = capture_session.refresh()
                        capture_monitors = sct.monitors
                        all_monitors = list(enumerate(capture_monitors[1:], start=1))
                        unique_ids_available = _monitor_unique_ids_available(
                            capture_monitors
                        )
                        monitor_scope: tuple[Any, ...]
                        if window_rect is not None:
                            followed = set(
                                monitor_indices_for_rect(
                                    capture_monitors,
                                    window_rect,
                                )
                            )
                            monitors = [
                                (idx, mon)
                                for idx, mon in all_monitors
                                if idx in followed
                            ]
                            monitor_scope = ("target", tuple(sorted(followed)))
                        else:
                            if monitor_filter is None:
                                monitors = all_monitors
                            else:
                                selected_monitor = _resolve_monitor_binding(
                                    capture_monitors,
                                    monitor_filter,
                                    monitor_unique_id,
                                )
                                monitors = (
                                    [selected_monitor]
                                    if selected_monitor is not None
                                    else []
                                )
                            monitor_scope = (
                                "selected",
                                monitor_filter,
                                monitor_unique_id,
                                tuple(idx for idx, _monitor in monitors),
                            )
                        signature = tuple(
                            (
                                idx,
                                mon["left"],
                                mon["top"],
                                mon["width"],
                                mon["height"],
                                _monitor_unique_id(mon),
                            )
                            for idx, mon in all_monitors
                        )
                    except Exception as exc:
                        if (
                            last_refresh_error_at is None
                            or now - last_refresh_error_at >= 10.0
                        ):
                            self.log_queue.put(
                                f"Screen capture refresh failed; will retry: {exc}"
                            )
                            last_refresh_error_at = now
                        self._wait_for_next_cycle()
                        continue
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
                                f"Monitor {monitor_filter} is unavailable or no "
                                "longer matches the saved display; "
                                "no screen will be scanned."
                            )
                        else:
                            self.log_queue.put(f"Watching {len(monitors)} monitor(s).")
                        last_monitor_status = monitor_status
                    best_scores: dict[int, tuple[float, Optional[int]]] = {
                        item["id"]: (-1.0, None) for item in items
                    }
                    complete_ids = {
                        item["id"]
                        for item in items
                        if any(
                            self._item_matches_monitor(
                                item,
                                idx,
                                mon,
                                unique_ids_available=unique_ids_available,
                            )
                            for idx, mon in monitors
                        )
                    }
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
                            for item in items:
                                if self._item_matches_monitor(
                                    item,
                                    mon_index,
                                    mon,
                                    unique_ids_available=unique_ids_available,
                                ):
                                    complete_ids.discard(item["id"])
                            continue
                        last_capture_error.pop(mon_index, None)
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
                            current_window_size=window_size,
                            current_monitor_size=(
                                int(mon["width"]),
                                int(mon["height"]),
                            ),
                        )
                        for entry in scan_items:
                            if self._stop_flag.is_set():
                                break
                            tid = entry["id"]
                            # A template added after the cycle's state snapshot is
                            # picked up safely on the next cycle.
                            if tid not in best_scores:
                                continue
                            if not self._item_matches_monitor(
                                entry,
                                mon_index,
                                mon,
                                unique_ids_available=unique_ids_available,
                            ):
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
                        items,
                        best_scores,
                        time.monotonic(),
                        complete_ids=complete_ids,
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
        struct.pack(
            "<h", int(amplitude * math.sin(2.0 * math.pi * freq * i / sample_rate))
        )
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
    try:
        worker.start()
    except Exception:
        # A rare interpreter/threading failure must not leave a never-started
        # object blocking every later alert sound request.
        with _SOUND_QUEUE_LOCK:
            if _SOUND_THREAD is worker:
                _SOUND_THREAD = None
                _PENDING_SOUND_VOLUME = None


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
        self._settings_load_errors = settings_load_errors(self.settings)
        self._settings_save_blocked_logged = False
        self.monitor_unique_id = self.settings.monitor_unique_id
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
        for error in self._settings_load_errors:
            self._append_log(
                f"Alert settings were not applied safely: {error}. "
                "The source file will not be rewritten."
            )
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
        ttk.Label(toolbar, text="Icon Alerts", style="Title.TLabel").grid(
            row=0, column=0, sticky="w"
        )
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
        test_alert_btn = ttk.Button(
            toolbar, text="Test alert", command=self._test_alert
        )
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
        add_file_btn = ttk.Button(
            btn_row, text="Add from file", command=self._add_from_file
        )
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
        ttk.Label(selected_header, text="Selected icon", style="Section.TLabel").pack(
            side="left"
        )
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
        self.icon_region_label = ttk.Label(
            selected_header, text="Region: global", style="Muted.TLabel"
        )
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
        ttk.Label(mode_row, text="Detection type", style="Surface.TLabel").pack(
            side="left"
        )
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
        ttk.Label(thresh_row, text="Match sensitivity", style="Surface.TLabel").pack(
            side="left"
        )
        self.thresh_var = tk.DoubleVar(value=DEFAULT_THRESHOLD)
        self.thresh_scale = ttk.Scale(
            thresh_row,
            from_=0.6,
            to=0.97,
            variable=self.thresh_var,
            command=self._on_threshold_change,
        )
        self.thresh_scale.pack(side="left", fill="x", expand=True, padx=6)
        self.thresh_label = ttk.Label(
            thresh_row, text=f"{DEFAULT_THRESHOLD:.2f}", style="Surface.TLabel"
        )
        self.thresh_label.pack(side="left")

        ttk.Label(right, text="Detection settings", style="Title.TLabel").pack(
            anchor="w"
        )
        ttk.Label(right, text="Preview", style="Section.TLabel").pack(
            anchor="w", pady=(12, 4)
        )
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
        self.monitor_combo.bind(
            "<<ComboboxSelected>>",
            self._on_monitor_selected,
        )
        ttk.Label(right, text="Target window", style="Surface.TLabel").pack(
            anchor="w", pady=(8, 0)
        )
        self.target_window_var = tk.StringVar(value=self.settings.target_window_title)
        self.target_window_combo = ttk.Combobox(
            right,
            textvariable=self.target_window_var,
            values=[],
            state="normal",
            width=18,
        )
        self.target_window_combo.pack(fill="x", pady=(2, 2))
        ttk.Button(
            right, text="Refresh windows", command=self._refresh_window_list
        ).pack(fill="x", pady=(2, 6))

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
        ttk.Checkbutton(advanced, text="Debug scores", variable=self.debug_var).pack(
            anchor="w"
        )

        cooldown_row = ttk.Frame(advanced, style="Surface.TFrame")
        cooldown_row.pack(fill="x", pady=(4, 4))
        ttk.Label(cooldown_row, text="Cooldown", style="Surface.TLabel").pack(
            side="left"
        )
        self.cooldown_var = tk.DoubleVar(value=self.settings.cooldown_sec)
        ttk.Spinbox(
            cooldown_row,
            from_=0.0,
            to=60.0,
            increment=0.5,
            textvariable=self.cooldown_var,
            width=6,
        ).pack(side="right")

        volume_row = ttk.Frame(advanced, style="Surface.TFrame")
        volume_row.pack(fill="x", pady=(4, 4))
        ttk.Label(volume_row, text="Alert volume", style="Surface.TLabel").pack(
            side="left"
        )
        self.volume_var = tk.DoubleVar(value=self.settings.alert_volume * 100.0)
        self.volume_label = ttk.Label(
            volume_row, text=f"{int(round(self.settings.alert_volume * 100))}%"
        )
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
        self.region_label = ttk.Label(
            right, text="Region: full screen", style="Muted.TLabel"
        )
        self.region_label.pack(anchor="w", pady=(3, 5))
        ttk.Button(right, text="Set region", command=self._set_scan_region).pack(
            fill="x", pady=2
        )
        ttk.Button(right, text="Clear region", command=self._clear_scan_region).pack(
            fill="x", pady=2
        )
        self.test_screenshot_btn = ttk.Button(
            right,
            text="Test screenshot",
            command=self._test_screenshot,
        )
        self.test_screenshot_btn.pack(fill="x", pady=(8, 2))

        ttk.Separator(right).pack(fill="x", pady=(8, 6))
        self.tray_var = tk.BooleanVar(value=self.settings.minimize_to_tray)
        if not self.embedded:
            ttk.Checkbutton(
                right,
                text="Minimize to tray",
                variable=self.tray_var,
                command=self._on_settings_changed,
            ).pack(anchor="w")

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
        log_scroll = ttk.Scrollbar(
            log_body, orient="vertical", command=self.log_text.yview
        )
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

    def _refresh_monitor_choices(self):
        if not hasattr(self, "monitor_combo"):
            return True
        current = self.monitor_var.get()
        available = self._monitor_choices()
        saved_unique_id = getattr(self, "monitor_unique_id", None)
        if current == "All monitors":
            if hasattr(self, "monitor_unique_id"):
                self.monitor_unique_id = None
            is_available = True
        elif saved_unique_id is not None:
            monitor_index = self._monitor_index_from_choice(current)
            try:
                with mss.MSS() as capture:
                    selected = _resolve_monitor_binding(
                        capture.monitors,
                        monitor_index,
                        saved_unique_id,
                    )
            except Exception:
                selected = None
            if selected is None:
                is_available = False
            else:
                resolved_choice = f"Monitor {selected[0]}"
                is_available = resolved_choice in available
                if resolved_choice != current:
                    self.monitor_var.set(resolved_choice)
                    current = resolved_choice
        else:
            is_available = current in available
            if is_available and hasattr(self, "monitor_unique_id"):
                self.monitor_unique_id = self._monitor_unique_id_for_choice(current)
        if not is_available and current and current not in available:
            available.append(current)
        self.monitor_combo["values"] = tuple(available)
        return is_available

    @staticmethod
    def _monitor_index_from_choice(value):
        if value == "All monitors":
            return None
        try:
            index = int(value.split()[-1])
        except (AttributeError, ValueError, IndexError):
            return None
        return index if index > 0 else None

    def _monitor_unique_id_for_choice(self, choice):
        monitor_index = self._monitor_index_from_choice(choice)
        if monitor_index is None:
            return None
        try:
            with mss.MSS() as capture:
                selected = _resolve_monitor_binding(
                    capture.monitors,
                    monitor_index,
                )
                if selected is not None:
                    return _monitor_unique_id(selected[1])
        except Exception:
            pass
        return None

    def _on_monitor_selected(self, _event=None):
        self.monitor_unique_id = self._monitor_unique_id_for_choice(
            self.monitor_var.get()
        )
        self._on_settings_changed()

    def _apply_loaded_settings(self):
        self._refresh_window_list()
        if not self._refresh_monitor_choices():
            unavailable_choice = self.monitor_var.get()
            self._append_log(
                f"{unavailable_choice} is currently unavailable; "
                "the saved selection was preserved."
            )
        self._update_region_label()
        if not HAVE_KEYBOARD:
            self._append_log(
                "Global hotkeys disabled: install 'keyboard' to enable them."
            )
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
            monitor_unique_id=(
                getattr(self, "monitor_unique_id", None)
                if self._selected_monitor_filter() is not None
                else None
            ),
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
            minimize_to_tray=bool(self.tray_var.get())
            if hasattr(self, "tray_var")
            else False,
        )

    def _save_settings(self):
        self._settings_save_after_id = None
        if getattr(self, "_settings_load_errors", ()):
            if not getattr(self, "_settings_save_blocked_logged", False):
                self._append_log(
                    "Settings were not saved because the source file contains "
                    "unknown or malformed fields."
                )
                self._settings_save_blocked_logged = True
            return
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
                monitor_unique_id=getattr(self, "monitor_unique_id", None),
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
            if hasattr(self, "region_label"):
                self._update_region_label()
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
        self._refresh_monitor_choices()

    def _setup_hotkeys(self):
        if not HAVE_KEYBOARD:
            return
        from .hotkeys import find_hotkey_conflicts

        bindings = (
            (
                "Icon Alerts start/stop",
                self.settings.start_stop_hotkey,
                self._toggle_watching_from_hotkey,
            ),
            (
                "Icon Alerts test alert",
                self.settings.test_alert_hotkey,
                self._test_alert_from_hotkey,
            ),
        )
        blocked_labels = set()
        try:
            conflicts = find_hotkey_conflicts(
                (label, hotkey) for label, hotkey, _callback in bindings
            )
        except ValueError as exc:
            self._append_log(f"Could not validate alert hotkeys: {exc}")
        else:
            for first, second in conflicts:
                blocked_labels.add(second)
                self._append_log(
                    f"Hotkey conflict: {second} duplicates {first}; "
                    f"{second} was not registered."
                )

        for label, hotkey, callback in bindings:
            if label in blocked_labels:
                continue
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
        if getattr(self, "_shutting_down", False):
            return
        self.event_queue.put({"type": "ui_command", "command": "toggle"})

    def _test_alert_from_hotkey(self):
        if getattr(self, "_shutting_down", False):
            return
        self.event_queue.put({"type": "ui_command", "command": "test_alert"})

    def _toggle_watching(self):
        if getattr(self, "_shutting_down", False):
            return
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
        icon = pystray.Icon(
            "Icon Alert Watcher",
            self._make_tray_image(),
            "Icon Alert Watcher",
            menu,
        )
        self.tray_icon = icon

        def run_tray():
            error = None
            try:
                icon.run()
            except Exception as exc:
                error = str(exc)
            finally:
                self.event_queue.put(
                    {
                        "type": "tray_unavailable",
                        "icon": icon,
                        "error": error,
                    }
                )

        tray_thread = threading.Thread(
            target=run_tray,
            name="icon-alert-tray",
            daemon=True,
        )
        self.tray_thread = tray_thread
        try:
            tray_thread.start()
        except Exception as exc:
            self.tray_icon = None
            self.tray_thread = None
            self.tray_var.set(False)
            self._append_log(f"System tray could not start: {exc}")

    def _tray_is_alive(self):
        thread = self.tray_thread
        return self.tray_icon is not None and thread is not None and thread.is_alive()

    def _show_from_tray(self):
        self.deiconify()
        self._lift_window()
        self.focus_force()

    def _quit_from_tray(self):
        self._request_app_quit()

    def _cleanup_tray(self):
        icon = self.tray_icon
        tray_thread = self.tray_thread
        self.tray_icon = None
        self.tray_thread = None
        if icon is not None:
            try:
                icon.stop()
            except Exception:
                pass
        if (
            tray_thread is not None
            and tray_thread is not threading.current_thread()
            and tray_thread.is_alive()
        ):
            tray_thread.join(timeout=0.5)

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
        elif hasattr(self, "detect_enabled_check"):
            self._clear_selected_icon_controls()

    def _clear_selected_icon_controls(self):
        self.detect_enabled_var.set(False)
        self.detect_enabled_check.config(state="disabled")
        self.match_mode_combo.config(state="disabled")
        self.thresh_scale.config(state="disabled")
        self.thresh_var.set(DEFAULT_THRESHOLD)
        self.thresh_label.config(text=f"{DEFAULT_THRESHOLD:.2f}")
        self.icon_region_label.config(text="Icon region: global")
        self.preview_label.configure(image="", text="Select an icon")
        self.preview_label.image = None

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
        self.match_mode_combo.config(state="readonly")
        self.thresh_scale.config(state="normal")
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
        self.preview_label.configure(image=tk_img, text="")
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
            filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp"), ("All files", "*.*")],
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
        reference_size, reference_space = self._reference_context_for_capture(abs_box)
        self._prompt_name_and_add(
            image_bgr,
            template_reference_size=reference_size,
            template_reference_space=reference_space,
        )

    def _reference_size_for_capture(self, abs_box):
        return self._reference_context_for_capture(abs_box)[0]

    def _reference_context_for_capture(self, abs_box):
        title = self.target_window_var.get().strip()
        if title:
            rect = find_window_rect(title)
            if rect:
                return (rect[2], rect[3]), "window"
        try:
            with mss.MSS() as capture:
                index = monitor_index_for_rect(capture.monitors, abs_box)
                if index is not None:
                    monitor = capture.monitors[index]
                    return (
                        (int(monitor["width"]), int(monitor["height"])),
                        "monitor",
                    )
        except Exception:
            pass
        return None, None

    def _prompt_name_and_add(
        self,
        image_bgr,
        template_reference_size=None,
        template_reference_space=None,
    ):
        name = simpledialog.askstring(
            "Name this icon", "Give this icon a short name:", parent=self
        )
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
                template_reference_space=template_reference_space,
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
            if not self._rect_contains(window_rect, abs_box):
                raise ValueError(
                    "Keep the selected region completely inside the target window."
                )
            return {
                "region": relative_region_from_window(abs_box, window_rect),
                "region_mode": "window",
                "region_ratio": proportional_region_from_window(abs_box, window_rect),
                "region_window_size": (window_rect[2], window_rect[3]),
                "monitor_index": None,
            }
        with mss.MSS() as capture:
            indices = monitor_indices_for_rect(capture.monitors, abs_box)
            if not indices:
                raise ValueError("The selected region is outside every monitor.")
            if len(indices) != 1:
                raise ValueError(
                    "Keep the selected region completely inside one monitor."
                )
            index = indices[0]
            selected_monitor = monitor_rect(capture.monitors[index])
            selected_monitor_unique_id = _monitor_unique_id(capture.monitors[index])
            if not self._rect_contains(selected_monitor, abs_box):
                raise ValueError(
                    "Keep the selected region completely inside one monitor."
                )
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
            "monitor_index": index,
            "monitor_unique_id": selected_monitor_unique_id,
        }

    @staticmethod
    def _rect_contains(container, candidate):
        left, top, width, height = (int(value) for value in container)
        child_left, child_top, child_width, child_height = (
            int(value) for value in candidate
        )
        return (
            width > 0
            and height > 0
            and child_width > 0
            and child_height > 0
            and child_left >= left
            and child_top >= top
            and child_left + child_width <= left + width
            and child_top + child_height <= top + height
        )

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
                monitor_index=meta.get("monitor_index"),
                monitor_unique_id=meta.get("monitor_unique_id"),
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
            elif monitor_filter is not None:
                selected = _resolve_monitor_binding(
                    sct.monitors,
                    monitor_filter,
                    getattr(self, "monitor_unique_id", None),
                )
                if selected is None:
                    raise ValueError(
                        f"Monitor {monitor_filter} is unavailable or no longer "
                        "matches the saved display."
                    )
                mon = selected[1]
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

    def _entry_monitor_box(self, entry):
        saved_unique_id = entry.get("monitor_unique_id")
        saved_index = entry.get("monitor_index")
        if saved_unique_id is None and saved_index is None:
            return self._selected_monitor_box()
        with mss.MSS() as capture:
            monitors = capture.monitors
            if saved_unique_id is not None:
                unique_ids_available = False
                for monitor in monitors[1:]:
                    current_unique_id = _monitor_unique_id(monitor)
                    unique_ids_available = (
                        unique_ids_available or current_unique_id is not None
                    )
                    if current_unique_id == str(saved_unique_id):
                        return monitor_rect(monitor)
                if unique_ids_available:
                    # The backend can identify displays and the saved display
                    # is absent. Falling back to its old ordinal could redirect
                    # this icon to a different physical monitor.
                    return None
            if (
                isinstance(saved_index, int)
                and not isinstance(saved_index, bool)
                and 0 < saved_index < len(monitors)
            ):
                return monitor_rect(monitors[saved_index])
        return None

    @staticmethod
    def _monitor_identity_for_box(box):
        normalized_box = tuple(int(value) for value in box)
        try:
            with mss.MSS() as capture:
                for index, monitor in enumerate(capture.monitors[1:], start=1):
                    if monitor_rect(monitor) == normalized_box:
                        return index, _monitor_unique_id(monitor)
        except Exception:
            pass
        return None, None

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
            monitor_box = (
                self._entry_monitor_box(entry)
                if entry.get("region_mode") == "monitor"
                else self._selected_monitor_box()
            )
            if entry.get("region_mode") == "monitor" and monitor_box is None:
                raise ValueError(
                    "The monitor saved for this icon is currently unavailable."
                )
            region = resolve_item_absolute_region(
                entry,
                global_region,
                self.target_window_var.get().strip(),
                find_window_rect,
                monitor_box,
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
        monitor_index = meta.get("monitor_index")
        if monitor_index is not None:
            # A monitor-relative box is meaningful only on the monitor it was
            # picked from.  Keep the scan source and saved coordinates bound
            # together instead of silently applying them to the old dropdown.
            self.monitor_unique_id = meta.get("monitor_unique_id")
            self.monitor_var.set(f"Monitor {monitor_index}")
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
        return self._monitor_index_from_choice(self.monitor_var.get())

    def _cooldown_seconds(self):
        try:
            value = float(self.cooldown_var.get())
            return max(0.0, value) if math.isfinite(value) else DEFAULT_COOLDOWN_SEC
        except (tk.TclError, TypeError, ValueError, OverflowError):
            return DEFAULT_COOLDOWN_SEC

    def _alert_volume(self):
        try:
            value = float(self.volume_var.get()) / 100.0
            return (
                min(1.0, max(0.0, value))
                if math.isfinite(value)
                else DEFAULT_ALERT_VOLUME
            )
        except (tk.TclError, TypeError, ValueError, OverflowError):
            return DEFAULT_ALERT_VOLUME

    # ---------------- monitoring control ----------------
    def _start_watching(self):
        if getattr(self, "_shutting_down", False):
            return
        settings_load_errors = getattr(self, "_settings_load_errors", ())
        if settings_load_errors:
            messagebox.showerror(
                "Invalid alert settings",
                "Monitoring was not started because the alert settings file "
                "contains unknown or malformed fields:\n\n"
                + "\n".join(settings_load_errors),
                parent=self,
            )
            return
        self._refresh_monitor_choices()
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
            monitor_unique_id=getattr(self, "monitor_unique_id", None),
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
            self._append_log(
                "Stopping watcher; waiting for the current match operation to finish."
            )
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
            self.status_label.config(
                text="Watcher stopped", style="Error.Status.TLabel"
            )
        else:
            self._set_idle_controls()
        if self._close_when_stopped:
            self._finish_app_quit()

    def _test_alert(self):
        if getattr(self, "_shutting_down", False):
            return
        tid = self._selected_id()
        entry = self.tm.get(tid) if tid is not None else None
        name = entry["name"] if entry else "Test"
        thumb = None
        if entry is not None:
            rgb = cv2.cvtColor(entry["image"], cv2.COLOR_BGR2RGB)
            thumb = Image.fromarray(rgb)
            thumb.thumbnail((64, 64))
        play_alert_sound(self._alert_volume())
        AlertPopup(
            self,
            name,
            monitor="-",
            thumb_img=thumb,
            animations_enabled=self.ui_preferences.animations_enabled,
        )

    def _test_screenshot(self):
        if getattr(self, "_shutting_down", False):
            return
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
            filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            global_region = self._resolve_global_scan_region_for_display()
            target_window_title = self.target_window_var.get().strip()
            target_rect = (
                find_window_rect(target_window_title) if target_window_title else None
            )
            with Image.open(path) as screenshot_image:
                screenshot_size = screenshot_image.size
            monitor_box = self._selected_monitor_box(
                target_rect=target_rect,
                screenshot_size=screenshot_size,
            )
            screenshot_monitor_index, screenshot_monitor_unique_id = (
                self._monitor_identity_for_box(monitor_box)
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
            current_window_size = (target_rect[2], target_rect[3])
        else:
            current_window_size = None
        if is_full_monitor_screenshot or self._selected_monitor_filter() is not None:
            current_monitor_size = (monitor_box[2], monitor_box[3])
        else:
            # A crop plus "All monitors" does not contain enough information
            # to identify its source resolution. Legacy scales are safer than
            # treating the virtual desktop as one enormous monitor.
            current_monitor_size = None
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
                # Variant generation can be expensive for uncached templates
                # and resolutions.  Keep it off Tk's event thread together
                # with the actual screenshot matching.
                template_items = [
                    item
                    for item in self.tm.snapshot(
                        use_grayscale=use_grayscale,
                        current_window_size=current_window_size,
                        current_monitor_size=current_monitor_size,
                        enabled_only=True,
                    )
                    if item.get("enabled", True)
                ]
                results = test_detection_on_screenshot(
                    path,
                    template_items,
                    use_grayscale=use_grayscale,
                    region=test_region,
                    region_origin=origin,
                    target_window_title=target_window_title,
                    monitor_box=monitor_box,
                    monitor_index=screenshot_monitor_index,
                    monitor_unique_id=screenshot_monitor_unique_id,
                    apply_saved_regions=is_full_monitor_screenshot,
                )
                event = {"type": "screenshot_test_complete", "results": results}
            except Exception as exc:
                event = {"type": "screenshot_test_error", "error": str(exc)}
            self.event_queue.put(event)

        try:
            worker = threading.Thread(target=_worker, daemon=True)
            worker.start()
        except Exception as exc:
            self._screenshot_test_running = False
            self.test_screenshot_btn.config(state="normal", text="Test screenshot")
            self._append_log(f"Could not start screenshot test: {exc}")
            messagebox.showerror(
                "Screenshot test failed",
                f"Could not start the background worker:\n{exc}",
                parent=self,
            )

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
                if getattr(self, "_shutting_down", False):
                    continue
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
            if event_type == "tray_unavailable":
                if ev.get("icon") is not self.tray_icon:
                    continue
                self.tray_icon = None
                self.tray_thread = None
                if self.embedded or self._shutting_down:
                    continue
                was_enabled = bool(self.tray_var.get())
                self.tray_var.set(False)
                self.deiconify()
                self._lift_window()
                error = ev.get("error")
                detail = f": {error}" if error else ""
                self._append_log(f"System tray stopped unexpectedly{detail}")
                if was_enabled:
                    messagebox.showwarning(
                        "System tray unavailable",
                        "The system tray stopped, so the alert window was restored "
                        "instead of remaining hidden.",
                        parent=self,
                    )
                continue
            if event_type in ("watcher_error", "watcher_stopped"):
                if ev.get("watcher") not in (None, self.watcher):
                    continue
                self._errored_watcher = ev.get("watcher", self.watcher)
                status_pulse = getattr(self, "_watcher_status_pulse", None)
                if status_pulse is not None:
                    status_pulse.stop("Error.Status.TLabel")
                self.status_label.config(
                    text="Watcher stopped", style="Error.Status.TLabel"
                )
                if not self._close_when_stopped:
                    messagebox.showwarning(
                        "Monitoring stopped",
                        f"The watcher stopped because of an error:\n{ev.get('error', 'Unknown error')}",
                    )
                continue
            if event_type == "watcher_finished":
                self._watcher_finished(ev.get("watcher"))
                continue
            if event_type == "screenshot_test_error":
                if getattr(self, "_shutting_down", False):
                    continue
                self._screenshot_test_running = False
                self.test_screenshot_btn.config(state="normal", text="Test screenshot")
                messagebox.showerror(
                    "Screenshot test failed", ev.get("error", "Unknown error")
                )
                continue
            if event_type == "screenshot_test_complete":
                if getattr(self, "_shutting_down", False):
                    continue
                self._screenshot_test_running = False
                self.test_screenshot_btn.config(state="normal", text="Test screenshot")
                lines = [
                    f"{result['name']}: unavailable"
                    if result.get("unavailable")
                    else f"{result['name']}: {result['score']:.2f} / {result['threshold']:.2f}"
                    f" {'MATCH' if result['matched'] else 'no match'}"
                    for result in ev.get("results", [])
                ]
                messagebox.showinfo(
                    "Screenshot test", "\n".join(lines) or "No templates tested."
                )
                self._append_log("Screenshot test: " + "; ".join(lines))
                continue
            if getattr(self, "_shutting_down", False):
                continue
            entry = self.tm.get(ev["id"])
            thumb = None
            if entry is not None:
                rgb = cv2.cvtColor(entry["image"], cv2.COLOR_BGR2RGB)
                thumb = Image.fromarray(rgb)
                thumb.thumbnail((64, 64))
            play_alert_sound(self._alert_volume())
            AlertPopup(
                self,
                ev["name"],
                ev["monitor"],
                thumb,
                animations_enabled=self.ui_preferences.animations_enabled,
            )
            self._append_log(
                f"ALERT: '{ev['name']}' seen on monitor {ev['monitor']} (score {ev['score']:.2f})"
            )
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
            if self._tray_is_alive():
                self.withdraw()
                self._append_log("Window hidden to system tray.")
                return
            self.tray_var.set(False)
            self._append_log(
                "System tray is unavailable; keeping the alert window visible."
            )
            messagebox.showwarning(
                "System tray unavailable",
                "The system tray is not running, so the alert window was not hidden.",
                parent=self,
            )
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


def main():
    """Run the standalone Icon Alert application."""
    instance_lock = SingleInstanceLock()
    try:
        acquired = instance_lock.acquire()
    except Exception as exc:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "Icon Alert Watcher could not start",
            "The application could not acquire its single-instance lock. "
            "No second copy was started.\n\n"
            f"{type(exc).__name__}: {exc}",
            parent=root,
        )
        root.destroy()
        return 1
    if not acquired:
        root = tk.Tk()
        root.withdraw()
        messagebox.showwarning(
            "Icon Alert Watcher already running",
            "Another copy of Icon Alert Watcher is already running.",
            parent=root,
        )
        root.destroy()
        return 1

    try:
        app = App()
        app.protocol("WM_DELETE_WINDOW", app.on_close)
        app.mainloop()
    finally:
        instance_lock.release()
    return 0


if __name__ == "__main__":
    sys.exit(main())
