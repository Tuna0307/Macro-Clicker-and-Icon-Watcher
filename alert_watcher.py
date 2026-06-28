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

Windows only (uses winsound for the alert tone). Tested for Python 3.9+.

Run:
    pip install opencv-python mss pillow
    python icon_alert_watcher.py
"""
import ctypes
import json
import os
import queue
import sys
import threading
import time
import tkinter as tk
from dataclasses import asdict, dataclass
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Optional, Tuple

import cv2
import mss
import numpy as np
from PIL import Image, ImageTk

from window_locator import (
    find_window_rect,
    proportional_region_from_window,
    relative_region_from_window,
    resolve_window_region,
    visible_window_titles,
)

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

try:
    import winsound
    HAVE_WINSOUND = True
except ImportError:
    HAVE_WINSOUND = False  # non-Windows: alerts will be popup-only

# --- Make screen capture coordinates match real pixels on Windows ---------
if sys.platform == "win32":
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass

APP_DIR = os.path.dirname(os.path.abspath(__file__))
ALERTS_DIR = os.path.join(APP_DIR, "alerts")
TEMPLATES_DIR = os.path.join(ALERTS_DIR, "templates")
MANIFEST_PATH = os.path.join(TEMPLATES_DIR, "manifest.json")
SETTINGS_PATH = os.path.join(ALERTS_DIR, "settings.json")
LOCK_PATH = os.path.join(ALERTS_DIR, "icon_alert_watcher.lock")
os.makedirs(TEMPLATES_DIR, exist_ok=True)

DEFAULT_SCALES = [
    0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95,
    1.00, 1.05, 1.10, 1.15, 1.20, 1.30, 1.40, 1.50,
]
DEFAULT_ROTATIONS = [0, -5, 5, -8, 8]
POLL_INTERVAL_SEC = 0.6
DEFAULT_THRESHOLD = 0.85
DEFAULT_COOLDOWN_SEC = 5.0
DEFAULT_START_STOP_HOTKEY = "ctrl+shift+f8"
DEFAULT_TEST_ALERT_HOTKEY = "ctrl+shift+f9"
REGION_UNAVAILABLE = object()


def _drain_queue(q):
    while True:
        try:
            yield q.get_nowait()
        except queue.Empty:
            break


@dataclass(eq=True)
class AppSettings:
    monitor_choice: str = "All monitors"
    grayscale: bool = True
    debug: bool = False
    cooldown_sec: float = DEFAULT_COOLDOWN_SEC
    scan_region: Optional[Tuple[int, int, int, int]] = None
    scan_region_mode: str = "screen"
    scan_region_ratio: Optional[Tuple[float, float, float, float]] = None
    scan_region_window_size: Optional[Tuple[int, int]] = None
    target_window_title: str = ""
    start_stop_hotkey: str = DEFAULT_START_STOP_HOTKEY
    test_alert_hotkey: str = DEFAULT_TEST_ALERT_HOTKEY
    minimize_to_tray: bool = False


def load_settings(path=SETTINGS_PATH):
    if not os.path.exists(path):
        return AppSettings()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return AppSettings()

    defaults = AppSettings()
    values = asdict(defaults)
    values.update({key: data[key] for key in values if key in data})
    if values["scan_region"] is not None:
        try:
            values["scan_region"] = tuple(int(v) for v in values["scan_region"])
            if len(values["scan_region"]) != 4:
                values["scan_region"] = None
        except (TypeError, ValueError):
            values["scan_region"] = None
    if values["scan_region_mode"] not in ("screen", "window"):
        values["scan_region_mode"] = "screen"
    if values["scan_region_ratio"] is not None:
        try:
            values["scan_region_ratio"] = tuple(float(v) for v in values["scan_region_ratio"])
            if len(values["scan_region_ratio"]) != 4:
                values["scan_region_ratio"] = None
        except (TypeError, ValueError):
            values["scan_region_ratio"] = None
    if values["scan_region_window_size"] is not None:
        try:
            values["scan_region_window_size"] = tuple(int(v) for v in values["scan_region_window_size"])
            if len(values["scan_region_window_size"]) != 2:
                values["scan_region_window_size"] = None
        except (TypeError, ValueError):
            values["scan_region_window_size"] = None
    try:
        values["cooldown_sec"] = max(0.0, float(values["cooldown_sec"]))
    except (TypeError, ValueError):
        values["cooldown_sec"] = defaults.cooldown_sec
    return AppSettings(**values)


def save_settings(path, settings):
    data = asdict(settings)
    if data["scan_region"] is not None:
        data["scan_region"] = list(data["scan_region"])
    if data["scan_region_ratio"] is not None:
        data["scan_region_ratio"] = list(data["scan_region_ratio"])
    if data["scan_region_window_size"] is not None:
        data["scan_region_window_size"] = list(data["scan_region_window_size"])
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


class SingleInstanceLock:
    def __init__(self, path=LOCK_PATH, process_exists=None):
        self.path = path
        self.process_exists = process_exists or self._process_exists
        self.fd = None

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
        for _attempt in range(2):
            try:
                self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
                os.write(self.fd, str(os.getpid()).encode("ascii"))
                return True
            except FileExistsError:
                if self._is_stale_lock():
                    self._remove_stale_lock()
                    continue
                return False
        return False

    def release(self):
        if self.fd is None:
            return
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None
        try:
            os.remove(self.path)
        except OSError:
            pass


def resolve_item_absolute_region(item, global_region, target_window_title="",
                                 window_rect_provider=find_window_rect):
    item_region = item.get("region")
    if item_region is None:
        return global_region
    if item.get("region_mode", "screen") == "window":
        rect = window_rect_provider(target_window_title)
        if not rect:
            return REGION_UNAVAILABLE
        return resolve_window_region(
            item_region,
            rect,
            item.get("region_ratio"),
            item.get("region_window_size"),
        )
    return item_region


# --------------------------------------------------------------------------
# Detection core
# --------------------------------------------------------------------------
def _crop_region(image, region):
    if region is None:
        return image, (0, 0)
    x, y, w, h = [int(v) for v in region]
    ih, iw = image.shape[:2]
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(iw, x + max(0, w)), min(ih, y + max(0, h))
    if x1 <= x0 or y1 <= y0:
        return image[:0, :0], (x0, y0)
    return image[y0:y1, x0:x1], (x0, y0)


def _prepare_match_image(image_bgr, use_grayscale):
    if not use_grayscale:
        return image_bgr
    return cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)


def _rotate_image(image, angle):
    if angle == 0:
        return image
    h, w = image.shape[:2]
    center = (w / 2, h / 2)
    matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
    return cv2.warpAffine(
        image,
        matrix,
        (w, h),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REPLICATE,
    )


def match_template_multiscale(screen_bgr, template_bgr, scales=DEFAULT_SCALES,
                              use_grayscale=False, region=None,
                              rotations=DEFAULT_ROTATIONS):
    best_score, best_loc, best_scale = -1.0, None, 1.0
    best_angle = 0
    screen_bgr, offset = _crop_region(screen_bgr, region)
    if screen_bgr.size == 0:
        return best_score, best_loc, best_scale

    screen = _prepare_match_image(screen_bgr, use_grayscale)
    template = _prepare_match_image(template_bgr, use_grayscale)
    th0, tw0 = template.shape[:2]
    sh, sw = screen.shape[:2]
    low_variance_template = float(np.std(template)) < 1e-6
    for angle in rotations:
        rotated_template = _rotate_image(template, angle)
        for scale in scales:
            tw, th = max(1, int(tw0 * scale)), max(1, int(th0 * scale))
            if tw > sw or th > sh:
                continue
            resized = cv2.resize(rotated_template, (tw, th), interpolation=cv2.INTER_AREA)
            if low_variance_template:
                result = cv2.matchTemplate(screen, resized, cv2.TM_SQDIFF)
                min_val, _, min_loc, _ = cv2.minMaxLoc(result)
                channels = resized.shape[2] if resized.ndim == 3 else 1
                worst = float(tw * th * channels * (255 ** 2))
                score, loc = max(0.0, 1.0 - (min_val / worst)), min_loc
            else:
                result = cv2.matchTemplate(screen, resized, cv2.TM_CCOEFF_NORMED)
                _, max_val, _, max_loc = cv2.minMaxLoc(result)
                score, loc = max_val, max_loc
            score_epsilon = 1e-6 if low_variance_template else 1e-9
            is_better = score > best_score + score_epsilon
            is_scale_tie = (
                abs(score - best_score) <= score_epsilon
                and abs(scale - 1.0) < abs(best_scale - 1.0)
            )
            is_angle_tie = (
                abs(score - best_score) <= score_epsilon
                and abs(scale - best_scale) <= 1e-9
                and abs(angle) < abs(best_angle)
            )
            if is_better or is_scale_tie or is_angle_tie:
                best_score = score
                best_loc = (loc[0] + offset[0], loc[1] + offset[1])
                best_scale = scale
                best_angle = angle
    return best_score, best_loc, best_scale


def test_detection_on_screenshot(path, template_items, use_grayscale=False, region=None):
    screenshot = cv2.imread(path)
    if screenshot is None:
        raise ValueError(f"Could not read screenshot: {path}")

    results = []
    for item in template_items:
        score, loc, scale = match_template_multiscale(
            screenshot,
            item["image"],
            use_grayscale=use_grayscale,
            region=region,
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
            if self.last_alert_at is not None and now - self.last_alert_at < self.cooldown_sec:
                return False
            self.active = True
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
        self._load()

    def _load(self):
        if not os.path.exists(MANIFEST_PATH):
            return
        with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        with self._lock:
            for entry in data.get("items", []):
                path = os.path.join(TEMPLATES_DIR, entry["file"])
                img = cv2.imread(path)
                if img is None:
                    continue
                tid = entry["id"]
                self.items[tid] = {
                    "name": entry["name"],
                    "file": entry["file"],
                    "threshold": entry.get("threshold", DEFAULT_THRESHOLD),
                    "region": tuple(entry["region"]) if entry.get("region") else None,
                    "region_mode": entry.get("region_mode", "screen"),
                    "region_ratio": tuple(entry["region_ratio"]) if entry.get("region_ratio") else None,
                    "region_window_size": tuple(entry["region_window_size"])
                    if entry.get("region_window_size") else None,
                    "image": img,
                }
                self._next_id = max(self._next_id, tid + 1)

    def _save(self):
        with self._lock:
            items = []
            for tid, v in self.items.items():
                item = {
                    "id": tid,
                    "name": v["name"],
                    "file": v["file"],
                    "threshold": v["threshold"],
                }
                if v.get("region") is not None:
                    item["region"] = list(v["region"])
                    item["region_mode"] = v.get("region_mode", "screen")
                if v.get("region_ratio") is not None:
                    item["region_ratio"] = list(v["region_ratio"])
                if v.get("region_window_size") is not None:
                    item["region_window_size"] = list(v["region_window_size"])
                items.append(item)
            data = {"items": items}
        with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def add(self, image_bgr, name, threshold=DEFAULT_THRESHOLD):
        with self._lock:
            tid = self._next_id
            self._next_id += 1
            filename = f"template_{tid}.png"
            cv2.imwrite(os.path.join(TEMPLATES_DIR, filename), image_bgr)
            self.items[tid] = {
                "name": name,
                "file": filename,
                "threshold": threshold,
                "region": None,
                "region_mode": "screen",
                "region_ratio": None,
                "region_window_size": None,
                "image": image_bgr.copy(),
            }
        self._save()
        return tid

    def remove(self, tid):
        with self._lock:
            entry = self.items.pop(tid, None)
        if entry:
            try:
                os.remove(os.path.join(TEMPLATES_DIR, entry["file"]))
            except OSError:
                pass
            self._save()

    def set_threshold(self, tid, threshold):
        with self._lock:
            if tid in self.items:
                self.items[tid]["threshold"] = threshold
            else:
                return
            self._save()

    def set_region(self, tid, region, region_mode="screen",
                   region_ratio=None, region_window_size=None):
        with self._lock:
            if tid not in self.items:
                return
            self.items[tid]["region"] = tuple(region) if region is not None else None
            self.items[tid]["region_mode"] = region_mode
            self.items[tid]["region_ratio"] = tuple(region_ratio) if region_ratio is not None else None
            self.items[tid]["region_window_size"] = (
                tuple(region_window_size) if region_window_size is not None else None
            )
            self._save()

    def clear_region(self, tid):
        self.set_region(tid, None, "screen", None, None)

    def get(self, tid):
        with self._lock:
            entry = self.items.get(tid)
            if entry is None:
                return None
            result = dict(entry)
            result["image"] = entry["image"].copy()
            return result

    def snapshot(self):
        with self._lock:
            return [
                {
                    "id": tid,
                    "name": entry["name"],
                    "file": entry["file"],
                    "threshold": entry["threshold"],
                    "region": entry.get("region"),
                    "region_mode": entry.get("region_mode", "screen"),
                    "region_ratio": entry.get("region_ratio"),
                    "region_window_size": entry.get("region_window_size"),
                    "image": entry["image"].copy(),
                }
                for tid, entry in self.items.items()
            ]


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
        self.states = {}  # tid -> TemplateState

    def stop(self):
        self._stop_flag.set()

    def _report_fatal_error(self, exc):
        msg = f"Watcher error: {exc}"
        self.log_queue.put(msg)
        self.event_queue.put({"type": "watcher_stopped", "error": str(exc)})

    def _sync_states(self, items):
        active_ids = {item["id"] for item in items}
        for tid in list(self.states):
            if tid not in active_ids:
                del self.states[tid]
        for item in items:
            tid = item["id"]
            if tid not in self.states or self.states[tid].threshold != item["threshold"]:
                self.states[tid] = TemplateState(item["threshold"], cooldown_sec=self.cooldown_sec)

    def _local_region_for_monitor(self, mon, absolute_region):
        if absolute_region is None:
            return None
        rx, ry, rw, rh = absolute_region
        left = max(rx, mon["left"])
        top = max(ry, mon["top"])
        right = min(rx + rw, mon["left"] + mon["width"])
        bottom = min(ry + rh, mon["top"] + mon["height"])
        if right <= left or bottom <= top:
            return None
        return (left - mon["left"], top - mon["top"], right - left, bottom - top)

    def _resolve_absolute_scan_region(self):
        if self.scan_region_mode == "window" or self.target_window_title:
            rect = self.window_rect_provider(self.target_window_title)
            if not rect:
                if not self._target_window_missing_logged:
                    self.log_queue.put(f"Target window not found: '{self.target_window_title}'")
                    self._target_window_missing_logged = True
                return REGION_UNAVAILABLE
            self._target_window_missing_logged = False
            if self.scan_region is None:
                return rect
            return resolve_window_region(
                self.scan_region,
                rect,
                self.scan_region_ratio,
                self.scan_region_window_size,
            )
        return self.scan_region

    def _resolve_item_scan_region(self, item, global_region):
        result = resolve_item_absolute_region(
            item,
            global_region,
            self.target_window_title,
            self.window_rect_provider,
        )
        if result is REGION_UNAVAILABLE:
            if not self._target_window_missing_logged:
                self.log_queue.put(f"Target window not found: '{self.target_window_title}'")
                self._target_window_missing_logged = True
        else:
            self._target_window_missing_logged = False
        return result

    def run(self):
        try:
            with mss.MSS() as sct:
                # monitors[0] is the combined virtual screen; skip it here,
                # we want each physical monitor captured separately.
                monitors = list(enumerate(sct.monitors[1:], start=1))
                if self.monitor_filter is not None:
                    monitors = [
                        (idx, mon) for idx, mon in monitors
                        if idx == self.monitor_filter
                    ]
                self.log_queue.put(f"Watching {len(monitors)} monitor(s).")
                last_debug_log = 0.0
                while not self._stop_flag.is_set():
                    items = self.tm.snapshot()
                    self._sync_states(items)
                    if not items:
                        time.sleep(POLL_INTERVAL_SEC)
                        continue
                    debug_lines = []
                    now = time.monotonic()
                    absolute_scan_region = self._resolve_absolute_scan_region()
                    if absolute_scan_region is REGION_UNAVAILABLE:
                        time.sleep(POLL_INTERVAL_SEC)
                        continue
                    for mon_index, mon in monitors:
                        if self._stop_flag.is_set():
                            break
                        try:
                            shot = sct.grab(mon)
                        except Exception as exc:
                            self.log_queue.put(
                                f"Monitor {mon_index} capture failed this cycle: {exc}"
                            )
                            continue
                        screen_bgr = cv2.cvtColor(np.array(shot), cv2.COLOR_BGRA2BGR)
                        for entry in items:
                            tid = entry["id"]
                            item_region = self._resolve_item_scan_region(entry, absolute_scan_region)
                            if item_region is REGION_UNAVAILABLE:
                                continue
                            region = self._local_region_for_monitor(mon, item_region)
                            if item_region is not None and region is None:
                                continue
                            score, loc, scale = match_template_multiscale(
                                screen_bgr,
                                entry["image"],
                                use_grayscale=self.use_grayscale,
                                region=region,
                            )
                            if self.debug:
                                debug_lines.append(
                                    f"{entry['name']} m{mon_index}: {score:.2f} "
                                    f"(th {entry['threshold']:.2f})"
                                )
                            triggered = self.states[tid].update(score, now=now)
                            if triggered:
                                self.event_queue.put({
                                    "id": tid,
                                    "name": entry["name"],
                                    "monitor": mon_index,
                                    "score": score,
                                })
                    if self.debug and debug_lines and now - last_debug_log >= 5.0:
                        self.log_queue.put("Debug scores: " + "; ".join(debug_lines))
                        last_debug_log = now
                    time.sleep(POLL_INTERVAL_SEC)
        except Exception as e:
            self._report_fatal_error(e)


def play_alert_sound():
    if HAVE_WINSOUND:
        def _beep():
            try:
                for freq in (880, 1100, 880):
                    winsound.Beep(freq, 140)
            except RuntimeError:
                winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        threading.Thread(target=_beep, daemon=True).start()
    else:
        print("\a", end="", flush=True)


# --------------------------------------------------------------------------
# GUI
# --------------------------------------------------------------------------
class ScreenRegionPicker(tk.Toplevel):
    """Fullscreen overlay (spans all monitors) for dragging a box around an icon."""
    def __init__(self, master, on_picked, on_cancel=None):
        super().__init__(master)
        self.on_picked = on_picked
        self.on_cancel = on_cancel
        self.completed = False
        self.withdraw()

        with mss.MSS() as sct:
            virtual = sct.monitors[0]
            shot = sct.grab(virtual)
            self.origin_x, self.origin_y = virtual["left"], virtual["top"]
            img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
            self.full_img = img

        self.geometry(f"{virtual['width']}x{virtual['height']}+{virtual['left']}+{virtual['top']}")
        self.overrideredirect(True)
        self.attributes("-topmost", True)

        self.tk_img = ImageTk.PhotoImage(img)
        self.canvas = tk.Canvas(self, cursor="cross", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.create_image(0, 0, image=self.tk_img, anchor="nw")
        self.hint = self.canvas.create_text(
            virtual["width"] // 2, 30,
            text="Drag a box tightly around the icon. Press Esc to cancel.",
            fill="yellow", font=("Segoe UI", 16, "bold")
        )

        self.start_x = self.start_y = None
        self.rect_id = None
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Escape>", lambda e: self._cancel())
        self.protocol("WM_DELETE_WINDOW", self._cancel)

        self.deiconify()
        self.focus_force()

    def _on_press(self, event):
        self.start_x, self.start_y = event.x, event.y
        if self.rect_id:
            self.canvas.delete(self.rect_id)
        self.rect_id = self.canvas.create_rectangle(
            event.x, event.y, event.x, event.y, outline="#00FF66", width=2
        )

    def _on_drag(self, event):
        self.canvas.coords(self.rect_id, self.start_x, self.start_y, event.x, event.y)

    def _on_release(self, event):
        x0, y0 = min(self.start_x, event.x), min(self.start_y, event.y)
        x1, y1 = max(self.start_x, event.x), max(self.start_y, event.y)
        if x1 - x0 < 4 or y1 - y0 < 4:
            self._cancel()
            return
        crop = self.full_img.crop((x0, y0, x1, y1))
        crop_bgr = cv2.cvtColor(np.array(crop), cv2.COLOR_RGB2BGR)
        abs_box = (x0 + self.origin_x, y0 + self.origin_y, x1 - x0, y1 - y0)
        self.completed = True
        self.destroy()
        self.on_picked(crop_bgr, abs_box)

    def _cancel(self):
        if self.completed:
            return
        self.completed = True
        self.destroy()
        if self.on_cancel is not None:
            self.on_cancel()


class RegionOverlay(tk.Toplevel):
    def __init__(self, master, absolute_box, label, duration_ms=4500):
        super().__init__(master)
        self.title("Scan Region Preview")

        with mss.MSS() as sct:
            virtual = sct.monitors[0]

        origin_x, origin_y = virtual["left"], virtual["top"]
        width, height = virtual["width"], virtual["height"]
        self.geometry(f"{width}x{height}+{origin_x}+{origin_y}")
        self.overrideredirect(True)
        self.attributes("-topmost", True)

        transparent = "#123456"
        self.configure(bg=transparent)
        try:
            self.attributes("-transparentcolor", transparent)
        except tk.TclError:
            self.attributes("-alpha", 0.35)

        self.canvas = tk.Canvas(
            self,
            bg=transparent,
            highlightthickness=0,
            cursor="hand2",
        )
        self.canvas.pack(fill="both", expand=True)

        x, y, w, h = absolute_box
        x0, y0 = x - origin_x, y - origin_y
        x1, y1 = x0 + w, y0 + h
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(width, x1), min(height, y1)

        if x1 > x0 and y1 > y0:
            self.canvas.create_rectangle(x0, y0, x1, y1, outline="#000000", width=7)
            self.canvas.create_rectangle(x0, y0, x1, y1, outline="#ffcc00", width=4)
            text_y = y0 - 16 if y0 >= 28 else y1 + 16
            text = f"{label}: {w}x{h} at {x},{y}"
            self.canvas.create_text(
                x0 + 2,
                text_y,
                text=text,
                fill="#000000",
                anchor="w",
                font=("Segoe UI", 12, "bold"),
            )
            self.canvas.create_text(
                x0,
                text_y - 2,
                text=text,
                fill="#ffcc00",
                anchor="w",
                font=("Segoe UI", 12, "bold"),
            )

        self.bind("<Escape>", lambda _event: self.destroy())
        self.canvas.bind("<Button-1>", lambda _event: self.destroy())
        self.after(duration_ms, self._safe_destroy)

    def _safe_destroy(self):
        try:
            self.destroy()
        except tk.TclError:
            pass


class AlertPopup(tk.Toplevel):
    def __init__(self, master, name, monitor, thumb_img):
        super().__init__(master)
        self.title("Icon Alert")
        self.attributes("-topmost", True)
        self.resizable(False, False)
        self.configure(bg="#1f1f1f")

        frame = tk.Frame(self, bg="#1f1f1f", padx=14, pady=12)
        frame.pack()

        if thumb_img is not None:
            tk_thumb = ImageTk.PhotoImage(thumb_img)
            lbl_img = tk.Label(frame, image=tk_thumb, bg="#1f1f1f")
            lbl_img.image = tk_thumb
            lbl_img.grid(row=0, column=0, rowspan=2, padx=(0, 12))

        tk.Label(frame, text=f"{name} detected!", fg="white", bg="#1f1f1f",
                 font=("Segoe UI", 13, "bold")).grid(row=0, column=1, sticky="w")
        tk.Label(frame, text=f"Monitor {monitor} - {time.strftime('%H:%M:%S')}",
                 fg="#aaaaaa", bg="#1f1f1f", font=("Segoe UI", 9)).grid(row=1, column=1, sticky="w")

        ttk.Button(frame, text="Dismiss", command=self.destroy).grid(
            row=2, column=0, columnspan=2, pady=(10, 0), sticky="ew")

        self.update_idletasks()
        sw = self.winfo_screenwidth()
        self.geometry(f"+{sw - self.winfo_width() - 40}+40")
        self.after(8000, self._safe_destroy)

    def _safe_destroy(self):
        try:
            self.destroy()
        except tk.TclError:
            pass


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

        self._build_ui()
        self._refresh_list()
        self._apply_loaded_settings()
        self._setup_hotkeys()
        if not self.embedded:
            self._setup_tray()
        self.after(150, self._poll_queues)

    def withdraw(self):
        self.winfo_toplevel().withdraw()

    def deiconify(self):
        self.winfo_toplevel().deiconify()

    def lift(self):
        self.winfo_toplevel().lift()

    def focus_force(self):
        self.winfo_toplevel().focus_force()

    # ---------------- UI construction ----------------
    def _build_ui(self):
        top = ttk.Frame(self, padding=10)
        top.pack(fill="both", expand=True)

        left = ttk.Frame(top)
        left.pack(side="left", fill="both", expand=True)

        ttk.Label(left, text="Watched icons", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        self.listbox = tk.Listbox(left, height=12)
        self.listbox.pack(fill="both", expand=True, pady=(4, 6))
        self.listbox.bind("<<ListboxSelect>>", self._on_select)

        btn_row = ttk.Frame(left)
        btn_row.pack(fill="x")
        ttk.Button(btn_row, text="Add From File", command=self._add_from_file).pack(side="left", padx=2)
        ttk.Button(btn_row, text="Capture From Screen", command=self._add_from_screen).pack(side="left", padx=2)
        ttk.Button(btn_row, text="Remove", command=self._remove_selected).pack(side="left", padx=2)

        icon_region_row = ttk.Frame(left)
        icon_region_row.pack(fill="x", pady=(4, 0))
        ttk.Button(icon_region_row, text="Set Icon Region",
                   command=self._set_selected_icon_region).pack(side="left", padx=2)
        ttk.Button(icon_region_row, text="Clear Icon Region",
                   command=self._clear_selected_icon_region).pack(side="left", padx=2)
        ttk.Button(icon_region_row, text="Show Icon Region",
                   command=self._show_selected_icon_region).pack(side="left", padx=2)
        self.icon_region_label = ttk.Label(icon_region_row, text="Icon region: global")
        self.icon_region_label.pack(side="left", padx=8)

        thresh_row = ttk.Frame(left)
        thresh_row.pack(fill="x", pady=(8, 0))
        ttk.Label(thresh_row, text="Match sensitivity:").pack(side="left")
        self.thresh_var = tk.DoubleVar(value=DEFAULT_THRESHOLD)
        self.thresh_scale = ttk.Scale(thresh_row, from_=0.6, to=0.97, variable=self.thresh_var,
                                       command=self._on_threshold_change)
        self.thresh_scale.pack(side="left", fill="x", expand=True, padx=6)
        self.thresh_label = ttk.Label(thresh_row, text=f"{DEFAULT_THRESHOLD:.2f}")
        self.thresh_label.pack(side="left")

        right = ttk.Frame(top, width=180)
        right.pack(side="left", fill="y", padx=(12, 0))
        ttk.Label(right, text="Preview", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        self.preview_label = tk.Label(right, bg="#2b2b2b", width=22, height=10)
        self.preview_label.pack(pady=4)

        ttk.Separator(right).pack(fill="x", pady=(8, 6))
        ttk.Label(right, text="Scan options", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        self.monitor_var = tk.StringVar(value=self.settings.monitor_choice)
        self.monitor_combo = ttk.Combobox(
            right,
            textvariable=self.monitor_var,
            values=self._monitor_choices(),
            state="readonly",
            width=18,
        )
        self.monitor_combo.pack(fill="x", pady=(3, 4))
        ttk.Label(right, text="Target window:").pack(anchor="w", pady=(4, 0))
        self.target_window_var = tk.StringVar(value=self.settings.target_window_title)
        self.target_window_combo = ttk.Combobox(
            right,
            textvariable=self.target_window_var,
            values=[],
            state="normal",
            width=18,
        )
        self.target_window_combo.pack(fill="x", pady=(2, 2))
        ttk.Button(right, text="Refresh Windows", command=self._refresh_window_list).pack(fill="x", pady=(0, 4))
        self.grayscale_var = tk.BooleanVar(value=self.settings.grayscale)
        ttk.Checkbutton(right, text="Grayscale", variable=self.grayscale_var).pack(anchor="w")
        self.debug_var = tk.BooleanVar(value=self.settings.debug)
        ttk.Checkbutton(right, text="Debug scores", variable=self.debug_var).pack(anchor="w")

        cooldown_row = ttk.Frame(right)
        cooldown_row.pack(fill="x", pady=(4, 4))
        ttk.Label(cooldown_row, text="Cooldown").pack(side="left")
        self.cooldown_var = tk.DoubleVar(value=self.settings.cooldown_sec)
        tk.Spinbox(cooldown_row, from_=0.0, to=60.0, increment=0.5,
                   textvariable=self.cooldown_var, width=5).pack(side="right")

        self.region_label = ttk.Label(right, text="Region: full screen")
        self.region_label.pack(anchor="w", pady=(4, 2))
        ttk.Button(right, text="Set Scan Region", command=self._set_scan_region).pack(fill="x", pady=1)
        ttk.Button(right, text="Clear Region", command=self._clear_scan_region).pack(fill="x", pady=1)
        ttk.Button(right, text="Test Screenshot", command=self._test_screenshot).pack(fill="x", pady=(6, 1))

        ttk.Separator(right).pack(fill="x", pady=(8, 6))
        self.tray_var = tk.BooleanVar(value=self.settings.minimize_to_tray)
        if not self.embedded:
            ttk.Checkbutton(right, text="Minimize to tray", variable=self.tray_var,
                            command=self._on_settings_changed).pack(anchor="w")

        control_row = ttk.Frame(self, padding=(10, 0, 10, 10))
        control_row.pack(fill="x")
        self.start_btn = ttk.Button(control_row, text="Start Monitoring", command=self._start_watching)
        self.start_btn.pack(side="left")
        self.stop_btn = ttk.Button(control_row, text="Stop Monitoring", command=self._stop_watching, state="disabled")
        self.stop_btn.pack(side="left", padx=6)
        ttk.Button(control_row, text="Test Alert", command=self._test_alert).pack(side="left", padx=6)
        self.status_label = ttk.Label(control_row, text="Idle", foreground="#888")
        self.status_label.pack(side="right")

        log_frame = ttk.Frame(self, padding=(10, 0, 10, 10))
        log_frame.pack(fill="both", expand=False)
        ttk.Label(log_frame, text="Activity log", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        self.log_text = tk.Text(log_frame, height=6, state="disabled", bg="#161616", fg="#cccccc")
        self.log_text.pack(fill="x")

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

    def _current_settings(self):
        return AppSettings(
            monitor_choice=self.monitor_var.get(),
            grayscale=bool(self.grayscale_var.get()),
            debug=bool(self.debug_var.get()),
            cooldown_sec=self._cooldown_seconds(),
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
        self.settings = self._current_settings()
        try:
            save_settings(SETTINGS_PATH, self.settings)
        except OSError as exc:
            self._append_log(f"Could not save settings: {exc}")

    def _on_settings_changed(self):
        if hasattr(self, "monitor_var") and hasattr(self, "tray_var"):
            self._save_settings()

    def _update_region_label(self):
        if self.scan_region is None:
            if self.target_window_var.get().strip():
                self.region_label.config(text="Region: target window")
            else:
                self.region_label.config(text="Region: full screen")
            return
        x, y, w, h = self.scan_region
        scope = "window" if self.scan_region_mode == "window" else "screen"
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
        self.after(0, self._toggle_watching)

    def _test_alert_from_hotkey(self):
        self.after(0, self._test_alert)

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
            self.after(0, self._show_from_tray)

        def toggle_monitoring(_icon=None, _item=None):
            self.after(0, self._toggle_watching)

        def quit_app(_icon=None, _item=None):
            self.after(0, self._quit_from_tray)

        menu = pystray.Menu(
            pystray.MenuItem("Show", show_window),
            pystray.MenuItem("Start/Stop Monitoring", toggle_monitoring),
            pystray.MenuItem("Test Alert", lambda _icon, _item: self.after(0, self._test_alert)),
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
        self.lift()
        self.focus_force()

    def _quit_from_tray(self):
        self._save_settings()
        self._cleanup_tray()
        self._cleanup_hotkeys()
        self._stop_watching()
        self.destroy()

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
            marker = " [region]" if entry.get("region") is not None else ""
            self.listbox.insert("end", f"  {entry['name']}{marker}   (th={entry['threshold']:.2f})")
        self._id_order = [entry["id"] for entry in items]
        if selected_tid in self._id_order:
            index = self._id_order.index(selected_tid)
            self.listbox.selection_set(index)
            self.listbox.see(index)

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
        self.thresh_var.set(entry["threshold"])
        self.thresh_label.config(text=f"{entry['threshold']:.2f}")
        self._show_preview(entry["image"])
        self._update_icon_region_label(entry)

    def _update_icon_region_label(self, entry=None):
        if entry is None:
            tid = self._selected_id()
            entry = self.tm.get(tid) if tid is not None else None
        if entry is None or entry.get("region") is None:
            self.icon_region_label.config(text="Icon region: global")
            return
        x, y, w, h = entry["region"]
        scope = "window" if entry.get("region_mode") == "window" else "screen"
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
        self.tm.set_threshold(tid, val)
        self._refresh_list(selected_tid=tid)

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
        self.withdraw()
        self.after(200, lambda: ScreenRegionPicker(
            self,
            self._on_region_picked,
            on_cancel=self.deiconify,
        ))

    def _on_region_picked(self, image_bgr, _abs_box):
        self.deiconify()
        self._prompt_name_and_add(image_bgr)

    def _prompt_name_and_add(self, image_bgr):
        name = simpledialog.askstring("Name this icon", "Give this icon a short name:", parent=self)
        if not name:
            name = f"icon_{len(self.tm.snapshot()) + 1}"
        self.tm.add(image_bgr, name, DEFAULT_THRESHOLD)
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
            self.tm.remove(tid)
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
        return {
            "region": abs_box,
            "region_mode": "screen",
            "region_ratio": None,
            "region_window_size": None,
        }

    def _set_selected_icon_region(self):
        tid = self._selected_id()
        if tid is None:
            messagebox.showinfo("No icon selected", "Select an icon first.")
            return
        self.withdraw()
        self.after(200, lambda: ScreenRegionPicker(
            self,
            self._on_icon_region_picked,
            on_cancel=self.deiconify,
        ))

    def _on_icon_region_picked(self, _image_bgr, abs_box):
        self.deiconify()
        tid = self._selected_id()
        if tid is None:
            return
        try:
            meta = self._region_metadata_from_abs_box(abs_box)
        except Exception as exc:
            messagebox.showerror("Window lookup failed", str(exc), parent=self)
            return
        self.tm.set_region(
            tid,
            meta["region"],
            meta["region_mode"],
            meta["region_ratio"],
            meta["region_window_size"],
        )
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
        self.tm.clear_region(tid)
        self._refresh_list(selected_tid=tid)
        self._update_icon_region_label()
        self._append_log(f"Icon region cleared for '{name}'.")

    def _selected_monitor_box(self):
        monitor_filter = self._selected_monitor_filter()
        with mss.MSS() as sct:
            if monitor_filter is not None and monitor_filter < len(sct.monitors):
                mon = sct.monitors[monitor_filter]
            else:
                mon = sct.monitors[0]
        return (mon["left"], mon["top"], mon["width"], mon["height"])

    def _resolve_global_scan_region_for_display(self):
        title = self.target_window_var.get().strip()
        if self.scan_region_mode == "window" or title:
            if not title:
                raise ValueError("Select the target window before showing this region.")
            window_rect = find_window_rect(title)
            if not window_rect:
                raise ValueError(f"No visible window title contains: {title}")
            if self.scan_region is None:
                return window_rect
            return resolve_window_region(
                self.scan_region,
                window_rect,
                self.scan_region_ratio,
                self.scan_region_window_size,
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
        self.withdraw()
        self.after(200, lambda: ScreenRegionPicker(
            self,
            self._on_scan_region_picked,
            on_cancel=self.deiconify,
        ))

    def _on_scan_region_picked(self, _image_bgr, abs_box):
        self.deiconify()
        title = self.target_window_var.get().strip()
        if title:
            try:
                meta = self._region_metadata_from_abs_box(abs_box)
            except Exception as exc:
                messagebox.showerror("Window lookup failed", str(exc), parent=self)
                return
        else:
            meta = self._region_metadata_from_abs_box(abs_box)
        self.scan_region = meta["region"]
        self.scan_region_mode = meta["region_mode"]
        self.scan_region_ratio = meta["region_ratio"]
        self.scan_region_window_size = meta["region_window_size"]
        self._update_region_label()
        x, y, w, h = self.scan_region
        scope = "window-relative" if self.scan_region_mode == "window" else "screen"
        self._append_log(f"Scan region set to {w}x{h} at {x},{y} ({scope}).")
        self._save_settings()

    def _clear_scan_region(self):
        self.scan_region = None
        self.scan_region_mode = "window" if self.target_window_var.get().strip() else "screen"
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
            return max(0.0, float(self.cooldown_var.get()))
        except (tk.TclError, ValueError):
            return DEFAULT_COOLDOWN_SEC

    # ---------------- monitoring control ----------------
    def _start_watching(self):
        if not self.tm.snapshot():
            messagebox.showinfo("No icons", "Add at least one icon to watch first.")
            return
        if self.watcher and self.watcher.is_alive():
            return
        self._save_settings()
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
        self.status_label.config(text="Watching...", foreground="#3ddc6a")

    def _stop_watching(self):
        if self.watcher:
            watcher = self.watcher
            watcher.stop()
            if watcher is not threading.current_thread():
                watcher.join(timeout=2.0)
            self.watcher = None
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.status_label.config(text="Idle", foreground="#888")

    def _test_alert(self):
        tid = self._selected_id()
        entry = self.tm.get(tid) if tid is not None else None
        name = entry["name"] if entry else "Test"
        thumb = None
        if entry is not None:
            rgb = cv2.cvtColor(entry["image"], cv2.COLOR_BGR2RGB)
            thumb = Image.fromarray(rgb)
            thumb.thumbnail((64, 64))
        play_alert_sound()
        AlertPopup(self, name, monitor="-", thumb_img=thumb)

    def _test_screenshot(self):
        if not self.tm.snapshot():
            messagebox.showinfo("No icons", "Add at least one icon to watch first.")
            return
        path = filedialog.askopenfilename(
            title="Select screenshot image",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp"), ("All files", "*.*")]
        )
        if not path:
            return
        try:
            results = test_detection_on_screenshot(
                path,
                self.tm.snapshot(),
                use_grayscale=self.grayscale_var.get(),
            )
        except ValueError as exc:
            messagebox.showerror("Error", str(exc))
            return

        lines = [
            f"{result['name']}: {result['score']:.2f} / {result['threshold']:.2f}"
            f" {'MATCH' if result['matched'] else 'no match'}"
            for result in results
        ]
        messagebox.showinfo("Screenshot test", "\n".join(lines))
        self._append_log("Screenshot test: " + "; ".join(lines))

    # ---------------- queue polling ----------------
    def _append_log(self, msg):
        self.log_text.config(state="normal")
        self.log_text.insert("end", f"[{time.strftime('%H:%M:%S')}] {msg}\n")
        line_count = int(self.log_text.index("end-1c").split(".")[0])
        if line_count > self.log_text_max_lines:
            self.log_text.delete("1.0", f"{line_count - self.log_text_max_lines}.0")
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def _poll_queues(self):
        for msg in _drain_queue(self.log_queue):
            self._append_log(msg)
        for ev in _drain_queue(self.event_queue):
            if ev.get("type") == "watcher_stopped":
                self.watcher = None
                self.start_btn.config(state="normal")
                self.stop_btn.config(state="disabled")
                self.status_label.config(text="Watcher stopped", foreground="#d9534f")
                messagebox.showwarning(
                    "Monitoring stopped",
                    f"The watcher stopped because of an error:\n{ev.get('error', 'Unknown error')}"
                )
                continue
            entry = self.tm.get(ev["id"])
            thumb = None
            if entry is not None:
                rgb = cv2.cvtColor(entry["image"], cv2.COLOR_BGR2RGB)
                thumb = Image.fromarray(rgb)
                thumb.thumbnail((64, 64))
            play_alert_sound()
            AlertPopup(self, ev["name"], ev["monitor"], thumb)
            self._append_log(f"ALERT: '{ev['name']}' seen on monitor {ev['monitor']} (score {ev['score']:.2f})")
        self.after(150, self._poll_queues)

    def shutdown(self):
        self._save_settings()
        self._cleanup_tray()
        self._cleanup_hotkeys()
        self._stop_watching()

    def on_close(self):
        self._save_settings()
        if not self.embedded and self.tray_var.get() and HAVE_PYSTRAY:
            self.withdraw()
            self._append_log("Window hidden to system tray.")
            return
        self.shutdown()
        if not self.embedded:
            self.winfo_toplevel().destroy()


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Icon Alert Watcher")
        self.geometry("780x740")
        self.minsize(740, 700)
        self.frame = AlertWatcherFrame(self, embedded=False)
        self.frame.pack(fill="both", expand=True)

    def on_close(self):
        self.frame.on_close()


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
