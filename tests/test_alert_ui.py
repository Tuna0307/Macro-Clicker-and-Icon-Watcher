from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

from macro_clicker import alert_ui, alert_watcher


def test_alert_watcher_keeps_the_extracted_window_api():
    assert alert_watcher.ScreenRegionPicker is alert_ui.ScreenRegionPicker
    assert alert_watcher.RegionOverlay is alert_ui.RegionOverlay
    assert alert_watcher.AlertPopup is alert_ui.AlertPopup


def test_region_picker_returns_a_clamped_absolute_crop():
    picker = object.__new__(alert_ui.ScreenRegionPicker)
    picker.start_x = -3
    picker.start_y = 2
    picker.full_img = Image.fromarray(np.zeros((10, 12, 3), dtype=np.uint8))
    picker.origin_x = -100
    picker.origin_y = 50
    picker.destroy = Mock()
    picker.on_picked = Mock()
    picker.completed = False

    event = type("Event", (), {"x": 20, "y": 9})()
    picker._on_release(event)

    picker.destroy.assert_called_once_with()
    crop, absolute_box = picker.on_picked.call_args.args
    assert crop.shape == (7, 12, 3)
    assert absolute_box == (-100, 52, 12, 7)


def test_alert_popup_follows_physical_monitor_after_reordering():
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

    popup = object.__new__(alert_ui.AlertPopup)

    with patch.object(alert_ui.mss, "MSS", return_value=Capture()):
        rect = popup._alert_monitor_rect(
            2,
            monitor_unique_id="DISPLAY-B",
            detected_monitor_rect=(500, 0, 300, 200),
        )

    assert rect == (0, 0, 100, 100)


def test_alert_popup_uses_detection_rect_when_monitor_identity_disappears():
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
        ]

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    popup = object.__new__(alert_ui.AlertPopup)

    with patch.object(alert_ui.mss, "MSS", return_value=Capture()):
        rect = popup._alert_monitor_rect(
            2,
            monitor_unique_id="DISPLAY-B",
            detected_monitor_rect=(-1920, 0, 1920, 1080),
        )

    assert rect == (-1920, 0, 1920, 1080)


def test_alert_popup_excludes_the_top_level_window_from_capture():
    user32 = Mock()
    user32.GetAncestor.return_value = 222
    user32.SetWindowDisplayAffinity.return_value = 1
    popup = object.__new__(alert_ui.AlertPopup)
    popup.winfo_id = Mock(return_value=111)

    with (
        patch.object(alert_ui.sys, "platform", "win32"),
        patch.object(alert_ui.ctypes, "windll", Mock(user32=user32)),
    ):
        excluded = popup._exclude_from_screen_capture()

    assert excluded
    ancestor_hwnd, ancestor_flag = user32.GetAncestor.call_args.args
    assert ancestor_hwnd.value == 111
    assert ancestor_flag == 2
    affinity_hwnd, affinity = user32.SetWindowDisplayAffinity.call_args.args
    assert affinity_hwnd.value == 222
    assert affinity.value == 0x00000011
