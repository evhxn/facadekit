"""Entry point for the frozen FacadeKit app (PyInstaller)."""

from __future__ import annotations

import sys
import traceback


def _pause() -> None:
    try:
        input("\nPress Enter to close this window...")
    except (EOFError, OSError):
        pass


def run(argv: list[str]) -> int:
    from facadekit.cli import main

    return main(argv if argv else ["serve"])


if __name__ == "__main__":
    interactive = len(sys.argv) == 1
    try:
        code = run(sys.argv[1:])
    except KeyboardInterrupt:
        code = 0
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
    except Exception:
        traceback.print_exc()
        code = 1
    # Keep the console open on failure when launched by double-click (no arguments).
    if code and interactive:
        _pause()
    sys.exit(code)
