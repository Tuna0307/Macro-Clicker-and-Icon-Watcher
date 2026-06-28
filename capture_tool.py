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
import time
import tkinter as tk
from tkinter import simpledialog

import mss
from PIL import Image, ImageTk


def _grab_full_screenshot(monitor_index=1):
    with mss.mss() as sct:
        monitor = sct.monitors[monitor_index]
        raw = sct.grab(monitor)
        img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
        return img, monitor["left"], monitor["top"]


def select_region(root, monitor_index=1):
    """
    Opens a fullscreen overlay showing a frozen screenshot. The user
    drags a rectangle. Returns (region, crop) where region is
    (left, top, width, height) in absolute screen coordinates and crop
    is a PIL Image of that area. Returns (None, None) if cancelled
    (Escape key).
    """
    root.withdraw()
    time.sleep(0.2)  # give the main window time to actually disappear before we screenshot
    screenshot, mon_left, mon_top = _grab_full_screenshot(monitor_index)
    root.deiconify()

    result = {"region": None}

    overlay = tk.Toplevel(root)
    overlay.attributes("-fullscreen", True)
    overlay.attributes("-topmost", True)
    overlay.configure(cursor="cross")

    photo = ImageTk.PhotoImage(screenshot)
    canvas = tk.Canvas(overlay, width=screenshot.width, height=screenshot.height,
                        highlightthickness=0, cursor="cross")
    canvas.pack(fill="both", expand=True)
    canvas.create_image(0, 0, anchor="nw", image=photo)
    canvas.image = photo  # keep a reference so it isn't garbage collected

    hint = canvas.create_text(
        screenshot.width // 2, 30,
        text="Drag a box around the icon. Esc to cancel.",
        fill="#00ff00", font=("Segoe UI", 14, "bold"),
    )

    start = {}
    rect_id = {"id": None}

    def on_press(event):
        start["x"], start["y"] = event.x, event.y
        if rect_id["id"]:
            canvas.delete(rect_id["id"])
        rect_id["id"] = canvas.create_rectangle(event.x, event.y, event.x, event.y,
                                                 outline="#00ff00", width=2)

    def on_drag(event):
        if rect_id["id"]:
            canvas.coords(rect_id["id"], start["x"], start["y"], event.x, event.y)

    def on_release(event):
        x0, y0 = start.get("x", event.x), start.get("y", event.y)
        x1, y1 = event.x, event.y
        left, right = sorted((x0, x1))
        top, bottom = sorted((y0, y1))
        if right - left > 3 and bottom - top > 3:
            result["region"] = (mon_left + left, mon_top + top, right - left, bottom - top)
        overlay.destroy()

    def on_escape(event=None):
        result["region"] = None
        overlay.destroy()

    canvas.bind("<ButtonPress-1>", on_press)
    canvas.bind("<B1-Motion>", on_drag)
    canvas.bind("<ButtonRelease-1>", on_release)
    overlay.bind("<Escape>", on_escape)

    overlay.grab_set()
    overlay.wait_window()

    if result["region"] is None:
        return None, None

    region = result["region"]
    crop = screenshot.crop((
        region[0] - mon_left, region[1] - mon_top,
        region[0] - mon_left + region[2], region[1] - mon_top + region[3],
    ))
    return region, crop


def capture_template(root, save_dir="templates", monitor_index=1):
    """
    Drag-select a region, then prompt for a filename and save the crop
    as a PNG under save_dir. Returns the saved path, or None if
    cancelled at any point.
    """
    region, crop = select_region(root, monitor_index)
    if region is None:
        return None

    name = simpledialog.askstring("Save template", "Name for this template image:", parent=root)
    if not name:
        return None
    safe_name = "".join(c for c in name if c.isalnum() or c in ("_", "-")) or "template"

    os.makedirs(save_dir, exist_ok=True)
    base_path = os.path.join(save_dir, f"{safe_name}.png")
    path = base_path
    counter = 1
    while os.path.exists(path):
        path = base_path.replace(".png", f"_{counter}.png")
        counter += 1

    crop.save(path)
    return path
