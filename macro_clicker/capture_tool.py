"""
Click-drag screen capture tool.

Freezes a screenshot, shows it fullscreen, and lets the user drag a
rectangle over it. Used for two things:
  - capture_template(): drag a box, save the crop as a template PNG
  - select_region(): drag a box, get back just the screen coordinates
                      (used for the optional "search region" on a
                      condition, to restrict matching to a small area)
"""

import os
import tempfile
import time
import tkinter as tk
from tkinter import simpledialog
from typing import Optional

import mss
from PIL import Image, ImageTk

from .detection_core import capture_bgr, monitor_rect
from .models import TEMPLATES_DIR


def _validated_monitor(monitors, monitor_index):
    if isinstance(monitor_index, bool) or not isinstance(monitor_index, int):
        raise ValueError("Monitor must be a whole number.")
    physical_count = max(0, len(monitors) - 1)
    if monitor_index < 1 or monitor_index >= len(monitors):
        raise ValueError(
            f"Monitor {monitor_index} is unavailable. Choose 1 through {physical_count}."
        )
    monitor = monitors[monitor_index]
    if int(monitor.get("width", 0)) <= 0 or int(monitor.get("height", 0)) <= 0:
        raise ValueError(f"Monitor {monitor_index} has invalid dimensions.")
    return monitor


def _grab_full_screenshot(monitor_index=1):
    with mss.MSS() as sct:
        monitor = _validated_monitor(sct.monitors, monitor_index)
        frame = capture_bgr(sct, monitor)
        img = Image.fromarray(frame[:, :, ::-1])
        left, top, _width, _height = monitor_rect(monitor)
        return img, left, top


def _absolute_overlay_geometry(width, height, left, top):
    """Return Tk geometry using absolute coordinates, including negatives."""
    # Tk interprets ``-1920`` as an offset from the right edge.  Prefixing the
    # signed coordinate with ``+`` (``+-1920``) places the window at x=-1920,
    # which is required for monitors positioned left of the primary display.
    return f"{width}x{height}+{left}+{top}"


def _hide_window(window):
    if window is None:
        return None, False
    try:
        previous_state = window.state()
    except (AttributeError, tk.TclError):
        previous_state = "normal"
    try:
        had_grab = window.grab_current() == window
    except (AttributeError, tk.TclError):
        had_grab = False
    window.withdraw()
    window.update_idletasks()
    return previous_state, had_grab


def _restore_window(window, previous_state, had_grab):
    if window is None or previous_state == "withdrawn":
        return
    try:
        window.deiconify()
        if previous_state in {"iconic", "zoomed"}:
            window.state(previous_state)
        window.lift()
        if had_grab:
            window.grab_set()
    except (AttributeError, tk.TclError):
        pass


def select_region(root, monitor_index=1):
    """
    Opens a fullscreen overlay showing a frozen screenshot. The user
    drags a rectangle. Returns (region, crop) where region is
    (left, top, width, height) in absolute screen coordinates and crop
    is a PIL Image of that area. Returns (None, None) if cancelled
    (Escape key).
    """
    # Validate before hiding anything so an invalid monitor cannot strand the
    # active dialog off-screen.
    with mss.MSS() as sct:
        _validated_monitor(sct.monitors, monitor_index)

    previous_state, had_grab = "normal", False
    overlay = None
    try:
        previous_state, had_grab = _hide_window(root)
        time.sleep(0.2)  # let the active window disappear before taking the screenshot
        screenshot, mon_left, mon_top = _grab_full_screenshot(monitor_index)

        result: dict[str, Optional[tuple[int, int, int, int]]] = {"region": None}

        overlay = tk.Toplevel(root)
        overlay.overrideredirect(True)
        overlay.geometry(
            _absolute_overlay_geometry(
                screenshot.width,
                screenshot.height,
                mon_left,
                mon_top,
            )
        )
        overlay.attributes("-topmost", True)
        overlay.configure(cursor="cross")

        photo = ImageTk.PhotoImage(screenshot)
        canvas = tk.Canvas(
            overlay,
            width=screenshot.width,
            height=screenshot.height,
            highlightthickness=0,
            cursor="cross",
        )
        canvas.pack(fill="both", expand=True)
        canvas.create_image(0, 0, anchor="nw", image=photo)
        canvas.image = photo  # keep a reference so it isn't garbage collected

        canvas.create_text(
            screenshot.width // 2,
            30,
            text="Drag a box around the icon. Esc to cancel.",
            fill="#00ff00",
            font=("Segoe UI", 14, "bold"),
        )

        start: dict[str, int] = {}
        rect_id: dict[str, Optional[int]] = {"id": None}

        def on_press(event):
            start["x"], start["y"] = event.x, event.y
            if rect_id["id"]:
                canvas.delete(rect_id["id"])
            rect_id["id"] = canvas.create_rectangle(
                event.x, event.y, event.x, event.y, outline="#00ff00", width=2
            )

        def on_drag(event):
            if rect_id["id"] and "x" in start and "y" in start:
                canvas.coords(rect_id["id"], start["x"], start["y"], event.x, event.y)

        def on_release(event):
            if "x" not in start or "y" not in start:
                return
            x0, y0 = start["x"], start["y"]
            x1 = min(max(event.x, 0), screenshot.width)
            y1 = min(max(event.y, 0), screenshot.height)
            x0 = min(max(x0, 0), screenshot.width)
            y0 = min(max(y0, 0), screenshot.height)
            left, right = sorted((x0, x1))
            top, bottom = sorted((y0, y1))
            if right - left > 3 and bottom - top > 3:
                result["region"] = (
                    mon_left + left,
                    mon_top + top,
                    right - left,
                    bottom - top,
                )
            overlay.destroy()

        def on_escape(event=None):
            result["region"] = None
            overlay.destroy()

        canvas.bind("<ButtonPress-1>", on_press)
        canvas.bind("<B1-Motion>", on_drag)
        canvas.bind("<ButtonRelease-1>", on_release)
        overlay.bind("<Escape>", on_escape)
        overlay.protocol("WM_DELETE_WINDOW", on_escape)

        overlay.grab_set()
        overlay.focus_force()
        overlay.wait_window()
    finally:
        if overlay is not None:
            try:
                if overlay.winfo_exists():
                    overlay.destroy()
            except tk.TclError:
                pass
        _restore_window(root, previous_state, had_grab)

    if result["region"] is None:
        return None, None

    region = result["region"]
    crop = screenshot.crop(
        (
            region[0] - mon_left,
            region[1] - mon_top,
            region[0] - mon_left + region[2],
            region[1] - mon_top + region[3],
        )
    )
    return region, crop


def capture_template(root, save_dir=TEMPLATES_DIR, monitor_index=1):
    """
    Drag-select a region, then prompt for a filename and save the crop
    as a PNG under save_dir. Returns the saved path, or None if
    cancelled at any point.
    """
    region, crop = select_region(root, monitor_index)
    if region is None:
        return None

    name = simpledialog.askstring(
        "Save template", "Name for this template image:", parent=root
    )
    if not name:
        return None
    safe_name = "".join(c for c in name if c.isalnum() or c in ("_", "-")) or "template"

    os.makedirs(save_dir, exist_ok=True)
    base_path = os.path.join(save_dir, f"{safe_name}.png")
    path = base_path
    counter = 1
    base_root, base_ext = os.path.splitext(base_path)
    while os.path.exists(path):
        path = f"{base_root}_{counter}{base_ext}"
        counter += 1

    fd, tmp_path = tempfile.mkstemp(
        prefix=f".{safe_name}.", suffix=".png", dir=save_dir
    )
    os.close(fd)
    try:
        crop.save(tmp_path)
        os.replace(tmp_path, path)
    except Exception:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        raise
    return path
