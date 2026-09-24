"""JSON with comments and trailing commas, as VS Code and OpenCode write it."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")


def loads(text: str) -> Any:
    # Plain JSON first: ~/.claude.json runs to megabytes and the stripper is pure Python.
    try:
        return json.loads(text)
    except ValueError:
        return json.loads(_TRAILING_COMMA_RE.sub(r"\1", _strip_comments(text)))


def load_file(path: Path) -> dict[str, Any]:
    """The object in ``path``, or ``{}`` when it is missing or not readable."""

    try:
        data = loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _strip_comments(text: str) -> str:
    out: list[str] = []
    index, length = 0, len(text)
    in_string = False
    while index < length:
        char = text[index]
        if in_string:
            out.append(char)
            if char == "\\" and index + 1 < length:
                out.append(text[index + 1])
                index += 2
                continue
            if char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            out.append(char)
            index += 1
        elif text.startswith("//", index):
            end = text.find("\n", index)
            index = length if end < 0 else end
        elif text.startswith("/*", index):
            end = text.find("*/", index + 2)
            index = length if end < 0 else end + 2
        else:
            out.append(char)
            index += 1
    return "".join(out)
