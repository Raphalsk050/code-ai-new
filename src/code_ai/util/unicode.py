"""Text that has to survive being encoded as UTF-8."""

from __future__ import annotations

import re
import sys
from typing import Any

# Paired surrogates never reach a Python str (the JSON decoder joins them), so
# any code point in this range is a lone one.
_LONE_SURROGATE_RE = re.compile("[\ud800-\udfff]")


def scrub(text: str) -> str:
    """Lone surrogates become U+FFFD: no UTF-8 request or file can carry them, and
    one left in a conversation fails every request that re-sends it."""

    return _LONE_SURROGATE_RE.sub("�", text)


def scrub_json(value: Any) -> Any:
    if isinstance(value, str):
        return scrub(value)
    if isinstance(value, dict):
        return {key: scrub_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [scrub_json(item) for item in value]
    return value


def use_utf8_stdio() -> None:
    """Read and write the process's own pipes as UTF-8, whatever the locale.

    Windows decodes a pipe in its ANSI code page, so the extension's UTF-8 came in
    as mojibake plus lone surrogates.
    """

    for stream, errors in ((sys.stdin, "replace"), (sys.stdout, "backslashreplace"),
                           (sys.stderr, "backslashreplace")):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors=errors)
        except (OSError, ValueError):
            # Already read from, or not a text stream we own: leave it as it is.
            continue
