"""Small Tk windows used by the icon alert watcher.

Keeping screen selection and transient alert presentation here leaves
``alert_watcher`` focused on template management and detection orchestration.
The classes are re-exported by ``alert_watcher`` for backwards compatibility.
"""

import ctypes
import sys
import time
import tkinter as tk
from tkinter import ttk

import cv2
import mss
import numpy as np
from PIL import Image, ImageTk

from .detection_core import capture_bgr
from .ui_components import COLORS


class ScreenRegionPicker(tk.Toplevel):
    """Fullscreen overlay spanning all monitors for selecting an icon region."""

    def __init__(self, master, on_picked, on_cancel=None):
        super().__init__(master)
        self.on_picked = on_picked
        self.on_cancel = on_cancel
        self.completed = False
        self.withdraw()

        with mss.MSS() as sct:
            virtual = sct.monitors[0]
            frame = capture_bgr(sct, virtual)
            self.origin_x, self.origin_y = virtual["left"], virtual["top"]
            img = Image.fromarray(frame[:, :, ::-1])
            self.full_img = img

        self.geometry(
            f"{virtual['width']}x{virtual['height']}+{virtual['left']}+{virtual['top']}"
        )
        self.overrideredirect(True)
        self.attributes("-topmost", True)

        self.tk_img = ImageTk.PhotoImage(img)
        self.canvas = tk.Canvas(self, cursor="cross", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.create_image(0, 0, image=self.tk_img, anchor="nw")
        self.hint = self.canvas.create_text(
            virtual["width"] // 2,
            30,
            text="Drag a box tightly around the icon. Press Esc to cancel.",
            fill="yellow",
            font=("Segoe UI", 16, "bold"),
        )

        self.start_x = self.start_y = None
        self.rect_id = None
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Escape>", lambda _event: self._cancel())
        self.protocol("WM_DELETE_WINDOW", self._cancel)

        self.deiconify()
        self.focus_force()

    def _on_press(self, event):
        self.start_x, self.start_y = event.x, event.y
        if self.rect_id:
            self.canvas.delete(self.rect_id)
        self.rect_id = self.canvas.create_rectangle(
            event.x,
            event.y,
            event.x,
            event.y,
            outline="#00FF66",
            width=2,
        )

    def _on_drag(self, event):
        if self.rect_id is None or self.start_x is None or self.start_y is None:
            return
        self.canvas.coords(self.rect_id, self.start_x, self.start_y, event.x, event.y)

    def _on_release(self, event):
        if self.start_x is None or self.start_y is None:
            return
        width, height = self.full_img.size
        end_x = min(max(event.x, 0), width)
        end_y = min(max(event.y, 0), height)
        start_x = min(max(self.start_x, 0), width)
        start_y = min(max(self.start_y, 0), height)
        x0, y0 = min(start_x, end_x), min(start_y, end_y)
        x1, y1 = max(start_x, end_x), max(start_y, end_y)
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
    """Temporary always-on-top outline showing a configured screen region."""

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
    """Short-lived alert window displayed when a configured icon is detected."""

    def __init__(
        self,
        master,
        name,
        monitor,
        thumb_img,
        *,
        animations_enabled=True,
        monitor_unique_id=None,
        detected_monitor_rect=None,
    ):
        super().__init__(master)
        self.title("Icon Alert")
        self.attributes("-topmost", True)
        self.resizable(False, False)
        self.configure(bg=COLORS["border"])
        self._fade_after_id = None
        self._close_after_id = None
        self._closing = False
        self._animations_enabled = bool(animations_enabled)
        self._popup_owner = master

        frame = tk.Frame(self, bg=COLORS["surface"], padx=18, pady=16)
        frame.pack(padx=1, pady=1)

        if thumb_img is not None:
            tk_thumb = ImageTk.PhotoImage(thumb_img)
            lbl_img = tk.Label(frame, image=tk_thumb, bg=COLORS["surface"])
            lbl_img.image = tk_thumb
            lbl_img.grid(row=0, column=0, rowspan=2, padx=(0, 12))

        tk.Label(
            frame,
            text=f"{name} detected!",
            fg=COLORS["text"],
            bg=COLORS["surface"],
            font=("Segoe UI", 13, "bold"),
        ).grid(row=0, column=1, sticky="w")
        tk.Label(
            frame,
            text=f"Monitor {monitor} - {time.strftime('%H:%M:%S')}",
            fg=COLORS["muted"],
            bg=COLORS["surface"],
            font=("Segoe UI", 9),
        ).grid(row=1, column=1, sticky="w")

        ttk.Button(frame, text="Dismiss", command=self._begin_close).grid(
            row=2,
            column=0,
            columnspan=2,
            pady=(10, 0),
            sticky="ew",
        )

        self.update_idletasks()
        self._exclude_from_screen_capture()
        left, top, width, height = self._alert_monitor_rect(
            monitor,
            monitor_unique_id=monitor_unique_id,
            detected_monitor_rect=detected_monitor_rect,
        )
        popup_width = self.winfo_width()
        popup_height = self.winfo_height()
        active = self._active_popups(master, (left, top, width, height))
        occupied = [
            rect for popup in active if (rect := self._popup_rect(popup)) is not None
        ]
        x, y = self._choose_popup_position(
            (left, top, width, height),
            (popup_width, popup_height),
            occupied,
        )
        self._alert_popup_monitor = (left, top, width, height)
        self._alert_popup_rect = (x, y, popup_width, popup_height)
        registry = getattr(master, "_alert_popup_windows", None)
        if registry is None:
            registry = []
            setattr(master, "_alert_popup_windows", registry)
        registry.append(self)
        self.geometry(f"{x:+d}{y:+d}")
        self.protocol("WM_DELETE_WINDOW", self._begin_close)
        if self._animations_enabled:
            try:
                self.attributes("-alpha", 0.0)
                self._animate_alpha(0.0, 1.0, 0.16)
            except tk.TclError:
                pass
        self._close_after_id = self.after(8000, self._begin_close)

    def _exclude_from_screen_capture(self):
        """Best-effort Windows protection against matching our own popup."""

        if sys.platform != "win32":
            return False
        try:
            user32 = ctypes.windll.user32
            widget_hwnd = int(self.winfo_id())
            get_ancestor = user32.GetAncestor
            get_ancestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
            get_ancestor.restype = ctypes.c_void_p
            # Tk may expose an inner drawable HWND. Display affinity applies
            # only to a top-level window, so resolve the actual popup root.
            root_hwnd = get_ancestor(ctypes.c_void_p(widget_hwnd), 2)  # GA_ROOT
            hwnd = int(root_hwnd or widget_hwnd)
            set_affinity = user32.SetWindowDisplayAffinity
            set_affinity.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
            set_affinity.restype = ctypes.c_int
            # WDA_EXCLUDEFROMCAPTURE (Windows 10 2004+) makes the popup absent
            # from screen captures while keeping it visible to the user.
            return bool(
                set_affinity(
                    ctypes.c_void_p(hwnd),
                    ctypes.c_uint32(0x00000011),
                )
            )
        except Exception:
            return False

    def _alert_monitor_rect(
        self,
        requested_monitor,
        *,
        monitor_unique_id=None,
        detected_monitor_rect=None,
    ):
        try:
            requested_index = int(requested_monitor)
        except (TypeError, ValueError):
            requested_index = 1
        try:
            with mss.MSS() as sct:
                monitors = sct.monitors
                if monitor_unique_id is not None:
                    saved_unique_id = str(monitor_unique_id)
                    for candidate in monitors[1:]:
                        current_unique_id = candidate.get("unique_id")
                        if (
                            current_unique_id is not None
                            and str(current_unique_id) == saved_unique_id
                        ):
                            return (
                                int(candidate["left"]),
                                int(candidate["top"]),
                                int(candidate["width"]),
                                int(candidate["height"]),
                            )
                    if detected_monitor_rect is not None:
                        detected_rect = tuple(
                            int(value) for value in detected_monitor_rect
                        )
                        current_rects = {
                            (
                                int(candidate["left"]),
                                int(candidate["top"]),
                                int(candidate["width"]),
                                int(candidate["height"]),
                            )
                            for candidate in monitors[1:]
                        }
                        # A stable monitor identity may be temporarily
                        # unavailable, but only reuse its detection rectangle
                        # while that geometry still belongs to a connected
                        # physical monitor. Otherwise an unplugged display
                        # would leave the popup entirely off-screen.
                        if detected_rect in current_rects:
                            return detected_rect
                index = (
                    requested_index
                    if 1 <= requested_index < len(monitors)
                    else (1 if len(monitors) > 1 else 0)
                )
                monitor = monitors[index]
            return (
                int(monitor["left"]),
                int(monitor["top"]),
                int(monitor["width"]),
                int(monitor["height"]),
            )
        except Exception:
            return (0, 0, self.winfo_screenwidth(), self.winfo_screenheight())

    @staticmethod
    def _active_popups(master, monitor_rect):
        active = []
        for popup in list(getattr(master, "_alert_popup_windows", ())):
            try:
                exists = bool(popup.winfo_exists())
            except tk.TclError:
                exists = False
            if exists:
                active.append(popup)
        setattr(master, "_alert_popup_windows", active)
        return [
            popup
            for popup in active
            if getattr(popup, "_alert_popup_monitor", None) == monitor_rect
        ]

    @staticmethod
    def _popup_rect(popup):
        stored = getattr(popup, "_alert_popup_rect", None)
        if stored is not None:
            return stored
        try:
            return (
                int(popup.winfo_x()),
                int(popup.winfo_y()),
                int(popup.winfo_width()),
                int(popup.winfo_height()),
            )
        except (AttributeError, tk.TclError, TypeError, ValueError):
            return None

    @staticmethod
    def _rectangles_overlap(first, second, gap=0):
        ax, ay, aw, ah = first
        bx, by, bw, bh = second
        return not (
            ax + aw + gap <= bx
            or bx + bw + gap <= ax
            or ay + ah + gap <= by
            or by + bh + gap <= ay
        )

    @classmethod
    def _choose_popup_position(cls, monitor_rect, popup_size, occupied):
        """Pack a popup using actual active rectangles, regardless of their sizes."""
        left, top, width, height = (int(value) for value in monitor_rect)
        popup_width, popup_height = (max(1, int(value)) for value in popup_size)
        gap = 12
        min_x = left + 8
        min_y = top + 8
        max_x = left + width - popup_width - 8
        max_y = top + height - popup_height - 8
        if max_x < min_x or max_y < min_y:
            return left, top

        preferred_x = min(max_x, max(min_x, left + width - popup_width - 40))
        preferred_y = min(max_y, max(min_y, top + 40))
        x_candidates = {preferred_x}
        y_candidates = {preferred_y}
        for x, y, item_width, item_height in occupied:
            x_candidates.update((x - popup_width - gap, x + item_width + gap))
            y_candidates.update((y - popup_height - gap, y + item_height + gap))

        valid_x = sorted(
            (x for x in x_candidates if min_x <= x <= max_x),
            reverse=True,
        )
        valid_y = sorted(y for y in y_candidates if min_y <= y <= max_y)
        for x in valid_x:
            for y in valid_y:
                candidate = (x, y, popup_width, popup_height)
                if not any(
                    cls._rectangles_overlap(candidate, existing, gap=gap)
                    for existing in occupied
                ):
                    return x, y
        # If the monitor is genuinely full, keep the newest popup visible at
        # the preferred corner rather than placing it outside the display.
        return preferred_x, preferred_y

    def _animate_alpha(self, value, target, step):
        try:
            exists = self.winfo_exists()
        except tk.TclError:
            return
        if not exists:
            return
        next_value = (
            min(target, value + step) if value < target else max(target, value - step)
        )
        try:
            self.attributes("-alpha", next_value)
        except tk.TclError:
            if target <= 0.0:
                self._safe_destroy()
            return
        if next_value == target:
            self._fade_after_id = None
            if target <= 0.0:
                self._safe_destroy()
            return
        self._fade_after_id = self.after(
            24,
            lambda: self._animate_alpha(next_value, target, step),
        )

    def _begin_close(self):
        if self._closing:
            return
        self._closing = True
        if self._fade_after_id is not None:
            try:
                self.after_cancel(self._fade_after_id)
            except tk.TclError:
                pass
            self._fade_after_id = None
        if self._close_after_id is not None:
            try:
                self.after_cancel(self._close_after_id)
            except tk.TclError:
                pass
            self._close_after_id = None
        if not self._animations_enabled:
            self._safe_destroy()
            return
        try:
            current_alpha = float(self.attributes("-alpha"))
        except (tk.TclError, TypeError, ValueError):
            self._safe_destroy()
            return
        self._animate_alpha(current_alpha, 0.0, 0.2)

    def _safe_destroy(self):
        for attr in ("_fade_after_id", "_close_after_id"):
            after_id = getattr(self, attr, None)
            if after_id is not None:
                try:
                    self.after_cancel(after_id)
                except tk.TclError:
                    pass
                setattr(self, attr, None)
        registry = getattr(self._popup_owner, "_alert_popup_windows", None)
        if registry is not None:
            try:
                registry.remove(self)
            except ValueError:
                pass
        try:
            self.destroy()
        except tk.TclError:
            pass
