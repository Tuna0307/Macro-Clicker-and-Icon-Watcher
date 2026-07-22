"""Small Tk windows used by the icon alert watcher.

Keeping screen selection and transient alert presentation here leaves
``alert_watcher`` focused on template management and detection orchestration.
The classes are re-exported by ``alert_watcher`` for backwards compatibility.
"""

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
            f"{virtual['width']}x{virtual['height']}"
            f"+{virtual['left']}+{virtual['top']}"
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

    def __init__(self, master, name, monitor, thumb_img):
        super().__init__(master)
        self.title("Icon Alert")
        self.attributes("-topmost", True)
        self.resizable(False, False)
        self.configure(bg=COLORS["border"])
        self._fade_after_id = None
        self._close_after_id = None
        self._closing = False

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
        try:
            with mss.MSS() as sct:
                virtual = sct.monitors[0]
            right = virtual["left"] + virtual["width"]
        except Exception:
            right = self.winfo_screenwidth()
        self.geometry(f"+{right - self.winfo_width() - 40}+40")
        self.protocol("WM_DELETE_WINDOW", self._begin_close)
        try:
            self.attributes("-alpha", 0.0)
            self._animate_alpha(0.0, 1.0, 0.16)
        except tk.TclError:
            pass
        self._close_after_id = self.after(8000, self._begin_close)

    def _animate_alpha(self, value, target, step):
        try:
            exists = self.winfo_exists()
        except tk.TclError:
            return
        if not exists:
            return
        next_value = min(target, value + step) if value < target else max(target, value - step)
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
        try:
            self.destroy()
        except tk.TclError:
            pass
