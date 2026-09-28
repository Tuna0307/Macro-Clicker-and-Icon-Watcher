"""Low-level input backends for the new bot path.

The new bot must not silently fall back to physical-cursor automation.  The first
candidate backend posts Win32 window messages directly to a target client window.
That is cursor-free, but it is *not* live-proven for the target game yet; games
using raw input/DirectInput may ignore these messages.
"""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from dataclasses import dataclass
from typing import Protocol

from .contracts import CapabilityStatus

WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
MK_LBUTTON = 0x0001


class InputBackendError(RuntimeError):
    pass


class InputBackend(Protocol):
    """Cursor-free input contract."""

    uses_physical_cursor: bool
    status: CapabilityStatus

    def click_client(self, x: int, y: int) -> None:
        ...

    def key_press(self, virtual_key: int) -> None:
        ...


@dataclass
class DisabledInputBackend:
    """Fail-closed backend used until an explicit input implementation is chosen."""

    reason: str = "No cursor-free input backend has been configured."
    uses_physical_cursor: bool = False
    status: CapabilityStatus = CapabilityStatus.NOT_IMPLEMENTED

    def click_client(self, x: int, y: int) -> None:
        raise InputBackendError(self.reason)

    def key_press(self, virtual_key: int) -> None:
        raise InputBackendError(self.reason)


class Win32MessageInputBackend:
    """Post input messages to a named Windows client without moving the cursor.

    Coordinates are CLIENT coordinates, not desktop coordinates.

    Status deliberately remains IMPLEMENTED_NOT_LIVE_TESTED until supervised game
    verification proves the target accepts this input path.
    """

    uses_physical_cursor = False
    status = CapabilityStatus.IMPLEMENTED_NOT_LIVE_TESTED

    def __init__(self, title_contains: str):
        title = title_contains.strip()
        if not title:
            raise ValueError("title_contains must not be empty")
        if os.name != "nt":
            raise RuntimeError("Win32MessageInputBackend is available only on Windows")
        self._title_contains = title.casefold()
        self._user32 = ctypes.windll.user32

    def _find_window(self) -> int:
        matches: list[tuple[int, str]] = []
        enum_proc_type = ctypes.WINFUNCTYPE(
            ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p
        )

        def callback(hwnd, _lparam):
            if not self._user32.IsWindowVisible(hwnd):
                return True
            length = self._user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            self._user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value.strip()
            if self._title_contains in title.casefold():
                matches.append((int(hwnd), title))
            return True

        self._user32.EnumWindows(enum_proc_type(callback), 0)
        if not matches:
            raise InputBackendError(
                f"Target window containing {self._title_contains!r} was not found."
            )

        # Mirror the existing target-window policy: prefer exact, then shortest title.
        hwnd, _title = min(
            matches,
            key=lambda item: (
                item[1].casefold() != self._title_contains,
                len(item[1]),
            ),
        )
        return hwnd

    def _validate_client_point(self, hwnd: int, x: int, y: int) -> None:
        rect = wintypes.RECT()
        if not self._user32.GetClientRect(hwnd, ctypes.byref(rect)):
            raise InputBackendError("GetClientRect failed for target window.")
        width = int(rect.right - rect.left)
        height = int(rect.bottom - rect.top)
        if width <= 0 or height <= 0:
            raise InputBackendError("Target window has no usable client area.")
        if not (0 <= x < width and 0 <= y < height):
            raise InputBackendError(
                f"Client point ({x}, {y}) is outside {width}x{height}."
            )

    @staticmethod
    def _make_lparam(x: int, y: int) -> int:
        return (y & 0xFFFF) << 16 | (x & 0xFFFF)

    def click_client(self, x: int, y: int) -> None:
        hwnd = self._find_window()
        self._validate_client_point(hwnd, x, y)
        lparam = self._make_lparam(x, y)
        if not self._user32.PostMessageW(hwnd, WM_LBUTTONDOWN, MK_LBUTTON, lparam):
            raise InputBackendError("WM_LBUTTONDOWN PostMessage failed.")
        if not self._user32.PostMessageW(hwnd, WM_LBUTTONUP, 0, lparam):
            raise InputBackendError("WM_LBUTTONUP PostMessage failed.")

    def key_press(self, virtual_key: int) -> None:
        if not 0 <= int(virtual_key) <= 0xFF:
            raise ValueError("virtual_key must be in range 0..255")
        hwnd = self._find_window()
        if not self._user32.PostMessageW(hwnd, WM_KEYDOWN, int(virtual_key), 0):
            raise InputBackendError("WM_KEYDOWN PostMessage failed.")
        if not self._user32.PostMessageW(hwnd, WM_KEYUP, int(virtual_key), 0):
            raise InputBackendError("WM_KEYUP PostMessage failed.")
