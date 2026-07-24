import importlib.machinery
import importlib.util
import runpy
import unittest
from pathlib import Path
from unittest.mock import patch

from macro_clicker import app


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_launcher_module():
    path = PROJECT_ROOT / "launcher.pyw"
    loader = importlib.machinery.SourceFileLoader("test_launcher", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None:
        raise RuntimeError("Could not create a module spec for launcher.pyw")
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class EntrypointTests(unittest.TestCase):
    def test_python_module_propagates_application_exit_code(self):
        with patch.object(app, "main", return_value=7):
            with self.assertRaises(SystemExit) as raised:
                runpy.run_module("macro_clicker.__main__", run_name="__main__")

        self.assertEqual(raised.exception.code, 7)

    def test_windows_launcher_returns_application_exit_code(self):
        launcher = _load_launcher_module()

        with patch.object(app, "main", return_value=6):
            self.assertEqual(launcher.main(), 6)

    def test_windows_launcher_preserves_integer_system_exit(self):
        launcher = _load_launcher_module()

        with patch.object(app, "main", side_effect=SystemExit(5)):
            self.assertEqual(launcher.main(), 5)

    def test_windows_launcher_reports_unexpected_failure(self):
        launcher = _load_launcher_module()

        with (
            patch.object(app, "main", side_effect=RuntimeError("boom")),
            patch.object(launcher, "_report_startup_error") as report,
        ):
            self.assertEqual(launcher.main(), 1)

        report.assert_called_once()


if __name__ == "__main__":
    unittest.main()
