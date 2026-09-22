from __future__ import annotations

from app.config import ConfigurationError, Settings
from app.menu import ApplicationMenu


def main() -> int:
    try:
        settings = Settings.from_env()
        settings.prepare_directories()
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}")
        return 2

    menu = ApplicationMenu(settings)
    try:
        menu.run()
    finally:
        menu.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
