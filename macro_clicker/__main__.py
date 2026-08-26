"""Allow the desktop bot application to start with ``python -m macro_clicker``."""

from .bot_app import main

if __name__ == "__main__":
    raise SystemExit(main())
