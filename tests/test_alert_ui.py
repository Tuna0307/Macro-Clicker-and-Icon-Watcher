from unittest.mock import Mock

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
