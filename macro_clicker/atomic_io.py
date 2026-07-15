"""Crash-safe writes for project JSON and image files."""

import json
import os
import tempfile

import cv2


def atomic_write_json(path, data):
    """Write JSON through a temporary file and atomically replace the target."""
    path = os.fspath(path)
    folder = os.path.dirname(path) or "."
    os.makedirs(folder, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(
        prefix=f".{os.path.basename(path)}.", suffix=".tmp", dir=folder
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except Exception:
        try:
            os.remove(temp_path)
        except OSError:
            pass
        raise


def atomic_write_png(path, image_bgr):
    """Encode a BGR image and atomically replace the target PNG."""
    path = os.fspath(path)
    try:
        ok, encoded = cv2.imencode(".png", image_bgr)
    except cv2.error as exc:
        raise OSError(f"Could not encode template image: {path}") from exc
    if not ok:
        raise OSError(f"Could not encode template image: {path}")

    folder = os.path.dirname(path) or "."
    os.makedirs(folder, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(
        prefix=f".{os.path.basename(path)}.", suffix=".png", dir=folder
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded.tobytes())
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except Exception:
        try:
            os.remove(temp_path)
        except OSError:
            pass
        raise

