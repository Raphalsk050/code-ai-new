"""YAML frontmatter for the markdown files skills, rules, workflows and agents use.

Not a YAML parser: the subset those files actually carry - scalars, inline and
block lists, one level of nested mapping, and ``|``/``>`` block scalars - with no
third-party dependency. Anything it cannot read is skipped, never raised.
"""

from __future__ import annotations

import re
from typing import Any

_FRONTMATTER_RE = re.compile(r"^---[ \t]*\n(?:(.*?)\n)?---[ \t]*(?:\n|$)(.*)$", re.DOTALL)
_KEY_RE = re.compile(r"""^("[^"]+"|'[^']+'|[A-Za-z0-9_*][A-Za-z0-9_.*-]*)\s*:(?:\s+(.*)|\s*)$""")
_BLOCK_SCALAR_RE = re.compile(r"^[|>][+-]?\d*$")
_BOM = chr(0xFEFF)


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """``(frontmatter, body)``. Keys are lowercased; a file without one is all body."""

    text = text.removeprefix(_BOM).replace("\r\n", "\n").replace("\r", "\n")
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text.strip()
    return parse_block(match.group(1) or ""), match.group(2).strip()


def parse_block(raw: str) -> dict[str, Any]:
    lines = raw.split("\n")
    result: dict[str, Any] = {}
    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        if not line.strip() or line.lstrip().startswith("#") or line[:1].isspace():
            continue
        match = _KEY_RE.match(line.rstrip())
        if not match:
            continue
        key, value = _scalar(match.group(1)).lower(), (match.group(2) or "").strip()
        nested, index = _indented(lines, index)
        if _BLOCK_SCALAR_RE.match(value):
            result[key] = _block_scalar(nested, folded=value.startswith(">"))
        elif value and nested and not value.startswith(("[", "{", "'", '"')):
            # A plain scalar that wraps onto the next lines.
            result[key] = " ".join([value, *(item.strip() for item in nested if item.strip())])
        elif value:
            result[key] = _scalar_or_inline(value)
        elif any(item.lstrip().startswith("- ") or item.strip() == "-" for item in nested):
            result[key] = [_scalar(item.strip()[1:].strip()) for item in nested
                           if item.strip().startswith("-") and item.strip()[1:].strip()]
        elif nested:
            result[key] = _mapping(nested)
        else:
            result[key] = ""
    return result


def as_list(value: Any) -> list[str]:
    """A list field written as a YAML list, an inline list, or ``a, b`` / ``a b``."""

    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, dict):
        return [str(key) for key in value]
    text = str(value or "").strip()
    if not text:
        return []
    separator = "," if "," in text else None
    return [part.strip().strip("'\"") for part in text.split(separator) if part.strip()]


def as_text(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    if isinstance(value, dict):
        return ", ".join(f"{key}: {item}" for key, item in value.items())
    return str(value or "").strip()


def is_true(value: Any) -> bool:
    return str(value).strip().casefold() in {"true", "yes", "1", "on"}


def _indented(lines: list[str], index: int) -> tuple[list[str], int]:
    """The indented (or blank) lines that belong to the key just read."""

    nested: list[str] = []
    while index < len(lines) and (not lines[index].strip() or lines[index][:1].isspace()):
        nested.append(lines[index])
        index += 1
    while nested and not nested[-1].strip():
        nested.pop()
    return nested, index


def _block_scalar(lines: list[str], *, folded: bool) -> str:
    if not lines:
        return ""
    indent = min(len(line) - len(line.lstrip()) for line in lines if line.strip())
    body = [line[indent:] if line.strip() else "" for line in lines]
    if not folded:
        return "\n".join(body).strip()
    return " ".join(line.strip() for line in body if line.strip())


def _mapping(lines: list[str]) -> dict[str, Any]:
    indent = min(len(line) - len(line.lstrip()) for line in lines if line.strip())
    own = [line[indent:] for line in lines if line.strip()]
    return parse_block("\n".join(own))


def _scalar_or_inline(value: str) -> Any:
    if value.startswith("[") and value.endswith("]"):
        return [_scalar(part) for part in _split_inline(value[1:-1]) if part.strip()]
    if value.startswith("{") and value.endswith("}"):
        mapping: dict[str, Any] = {}
        for part in _split_inline(value[1:-1]):
            key, found, item = part.partition(":")
            if found and key.strip():
                mapping[_scalar(key).lower()] = _scalar(item)
        return mapping
    return _scalar(value)


def _split_inline(text: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    quote = ""
    for char in text:
        if quote:
            current.append(char)
            if char == quote:
                quote = ""
        elif char in "'\"":
            quote = char
            current.append(char)
        elif char == ",":
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    parts.append("".join(current))
    return [part.strip() for part in parts]


def _scalar(value: str) -> str:
    # Trailing "# comments" are kept: "Fix issue #12" in a description matters more.
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value
