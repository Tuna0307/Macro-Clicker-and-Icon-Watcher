"""Windows GUI launcher that makes startup failures visible and persistent."""
import ctypes
import os
import sys
import traceback
from datetime import datetime

from macro_clicker.runtime_paths import STARTUP_ERROR_LOG

APP_DIR = os.path.dirname(os.path.abspath(__file__))


def _report_startup_error(exc):
    try:
        os.makedirs(os.path.dirname(STARTUP_ERROR_LOG), exist_ok=True)
        with open(STARTUP_ERROR_LOG, "a", encoding="utf-8") as handle:
            handle.write(f"\n[{datetime.now().isoformat(timespec='seconds')}]\n")
            handle.write(traceback.format_exc())
    except OSError:
        pass

    message = (
        f"PC Macro Builder could not start.\n\n{type(exc).__name__}: {exc}"
        f"\n\nDetails were written to:\n{STARTUP_ERROR_LOG}"
    )
    try:
        ctypes.windll.user32.MessageBoxW(None, message, "PC Macro Builder", 0x10)
    except Exception:
        pass


def main():
    os.chdir(APP_DIR)
    if APP_DIR not in sys.path:
        sys.path.insert(0, APP_DIR)
    try:
        from macro_clicker.app import main as run_application

        run_application()
    except SystemExit:
        # app.main() already reports expected non-zero exits (for example a
        # second instance or a GUI initialization failure).
        pass
    except BaseException as exc:
        _report_startup_error(exc)


if __name__ == "__main__":
    main()
