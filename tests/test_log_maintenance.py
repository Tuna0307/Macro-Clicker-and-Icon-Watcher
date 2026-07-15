import os
import tempfile
import time
import unittest

from macro_clicker.log_maintenance import cleanup_directory, rotate_log_file


class LogMaintenanceTests(unittest.TestCase):
    def test_rotate_log_file_keeps_numbered_backups(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "pc_macro_builder.log")
            with open(path, "wb") as f:
                f.write(b"a" * 12)

            rotated = rotate_log_file(path, max_bytes=10, backups=2)

            self.assertTrue(rotated)
            self.assertFalse(os.path.exists(path))
            self.assertTrue(os.path.exists(os.path.join(tmp, "pc_macro_builder.1.log")))

    def test_rotate_log_file_shifts_old_backups_and_removes_oldest(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "pc_macro_builder.log")
            for name, text in [
                ("pc_macro_builder.log", "new"),
                ("pc_macro_builder.1.log", "old1"),
                ("pc_macro_builder.2.log", "old2"),
            ]:
                with open(os.path.join(tmp, name), "w", encoding="utf-8") as f:
                    f.write(text * 10)

            rotate_log_file(path, max_bytes=1, backups=2)

            with open(os.path.join(tmp, "pc_macro_builder.1.log"), encoding="utf-8") as f:
                self.assertTrue(f.read().startswith("new"))
            with open(os.path.join(tmp, "pc_macro_builder.2.log"), encoding="utf-8") as f:
                self.assertTrue(f.read().startswith("old1"))

    def test_cleanup_directory_removes_old_files_and_caps_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            now = time.time()
            files = []
            for i in range(5):
                path = os.path.join(tmp, f"debug_{i}.png")
                with open(path, "wb") as f:
                    f.write(b"x")
                mtime = now - (i * 60)
                os.utime(path, (mtime, mtime))
                files.append(path)
            old_path = os.path.join(tmp, "old.png")
            with open(old_path, "wb") as f:
                f.write(b"x")
            old_time = now - 10 * 86400
            os.utime(old_path, (old_time, old_time))

            removed = cleanup_directory(tmp, max_files=3, max_age_days=7, now=now)

            self.assertGreaterEqual(removed, 3)
            remaining = sorted(os.listdir(tmp))
            self.assertEqual(len(remaining), 3)
            self.assertNotIn("old.png", remaining)

    def test_cleanup_preserves_labels_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            now = time.time()
            labels = os.path.join(tmp, "labels.json")
            old_crop = os.path.join(tmp, "old.png")
            for path in (labels, old_crop):
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write("{}")
                os.utime(path, (now - 10 * 86400, now - 10 * 86400))

            cleanup_directory(
                tmp,
                max_files=0,
                max_age_days=1,
                now=now,
                preserve_names={"labels.json"},
            )

            self.assertTrue(os.path.exists(labels))
            self.assertFalse(os.path.exists(old_crop))


if __name__ == "__main__":
    unittest.main()
