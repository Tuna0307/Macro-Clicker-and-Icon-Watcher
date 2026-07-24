import json
import os
import tempfile
import unittest

from macro_clicker.ui_preferences import (
    UiPreferences,
    load_ui_preferences,
    save_ui_preferences,
)


class UiPreferencesTests(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "nested", "ui.json")
            expected = UiPreferences(sounds_enabled=False, animations_enabled=True)
            save_ui_preferences(expected, path)
            self.assertEqual(load_ui_preferences(path), expected)

    def test_invalid_values_fall_back_independently(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "ui.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(
                    {"sounds_enabled": "yes", "animations_enabled": False}, handle
                )
            self.assertEqual(
                load_ui_preferences(path),
                UiPreferences(sounds_enabled=True, animations_enabled=False),
            )

    def test_non_object_or_broken_json_uses_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "ui.json")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("not json")
            self.assertEqual(load_ui_preferences(path), UiPreferences())


if __name__ == "__main__":
    unittest.main()
