from __future__ import annotations

import sys


def test_headless_module_does_not_import_textual() -> None:
    sys.modules.pop("textual", None)
    import code_ai.cli.headless  # noqa: F401

    assert "textual" not in sys.modules
