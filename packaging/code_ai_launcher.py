"""Frozen-binary entry point for PyInstaller.

PyInstaller bundles a single script rather than a console-script entry point, so
this thin launcher forwards to the real CLI and propagates its exit code.
"""

from __future__ import annotations

from code_ai.cli.main import main

if __name__ == "__main__":
    raise SystemExit(main())
