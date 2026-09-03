"""Allow the desktop application to start with ``python -m macro_clicker``."""

from .activity_clear_runtime import install_activity_clear_runtime
from .app import main

install_activity_clear_runtime()

if __name__ == "__main__":
    raise SystemExit(main())
