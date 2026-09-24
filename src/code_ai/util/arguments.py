"""What the user typed after ``/name``, filled into a saved prompt's placeholders.

Each agent spells placeholders its own way, so the dialect decides:

* ``claude-code``: ``$ARGUMENTS``, ``$ARGUMENTS[N]`` and ``$N`` (0-based), plus
  ``$name`` for names declared in the ``arguments`` frontmatter; ``\\$`` is a
  literal dollar.
* ``codex``: ``$ARGUMENTS``, ``$1``..``$9`` (1-based), ``$KEY`` from
  ``KEY=value`` pairs, ``$$`` for a literal dollar.
* ``opencode``: ``$ARGUMENTS`` and ``$1``, ``$2``... (1-based).

A positional placeholder with no matching argument stays as written: a price
like ``$5`` in a prompt must survive being run with no arguments.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

CLAUDE_CODE = "claude-code"
CODEX = "codex"
OPENCODE = "opencode"

_TOKEN_RE = re.compile(r'"([^"]*)"|\'([^\']*)\'|(\S+)')
_NAMED_PAIR_RE = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$", re.DOTALL)

_CLAUDE_RE = re.compile(
    r"\\\$|\$ARGUMENTS\[(\d+)\]|\$ARGUMENTS(?![A-Za-z0-9_])|\$(\d+)|\$([A-Za-z_][A-Za-z0-9_-]*)"
)
_CODEX_RE = re.compile(r"\$\$|\$ARGUMENTS(?![A-Za-z0-9_])|\$([1-9])|\$([A-Z][A-Z0-9_]*)")
_OPENCODE_RE = re.compile(r"\$ARGUMENTS(?![A-Za-z0-9_])|\$(\d+)")


def split_arguments(text: str) -> list[str]:
    """Words, with quotes grouping a multi-word value. Backslashes are kept (paths)."""

    words: list[str] = []
    for match in _TOKEN_RE.finditer(text or ""):
        double, single, bare = match.groups()
        words.append(double if double is not None else single if single is not None else bare)
    return words


def fill_arguments(
    body: str, argument: str, *, dialect: str, names: Sequence[str] = ()
) -> tuple[str, bool]:
    """``(text, used)``; ``used`` is False when no placeholder took the input."""

    text = (argument or "").strip()
    if dialect == CLAUDE_CODE:
        return _fill_claude(body, text, names)
    if dialect == CODEX:
        return _fill_codex(body, text)
    if dialect == OPENCODE:
        return _fill_opencode(body, text)
    return body, False


def _fill_claude(body: str, text: str, names: Sequence[str]) -> tuple[str, bool]:
    words = split_arguments(text)
    declared = {name: index for index, name in enumerate(names)}
    used = False

    def replace(match: re.Match[str]) -> str:
        nonlocal used
        whole = match.group(0)
        if whole == "\\$":
            return "$"
        indexed, positional, named = match.groups()
        if indexed is not None or positional is not None:
            index = int(indexed if indexed is not None else positional)
            if index >= len(words):
                return whole
            used = True
            return words[index]
        if named is not None:
            if named not in declared:
                return whole
            used = True
            index = declared[named]
            return words[index] if index < len(words) else ""
        used = True
        return text

    return _CLAUDE_RE.sub(replace, body), used


def _fill_codex(body: str, text: str) -> tuple[str, bool]:
    pairs: dict[str, str] = {}
    words: list[str] = []
    for word in split_arguments(text):
        pair = _NAMED_PAIR_RE.match(word)
        if pair:
            pairs[pair.group(1)] = pair.group(2)
        else:
            words.append(word)
    used = False

    def replace(match: re.Match[str]) -> str:
        nonlocal used
        whole = match.group(0)
        if whole == "$$":
            return "$"
        positional, named = match.groups()
        if positional is not None:
            index = int(positional) - 1
            if index >= len(words):
                return whole
            used = True
            return words[index]
        if named is not None:
            if named not in pairs:
                return whole
            used = True
            return pairs[named]
        used = True
        return " ".join(words)

    return _CODEX_RE.sub(replace, body), used


def _fill_opencode(body: str, text: str) -> tuple[str, bool]:
    words = split_arguments(text)
    used = False

    def replace(match: re.Match[str]) -> str:
        nonlocal used
        positional = match.group(1)
        if positional is None:
            used = True
            return text
        index = int(positional) - 1
        if index < 0 or index >= len(words):
            return match.group(0)
        used = True
        return words[index]

    return _OPENCODE_RE.sub(replace, body), used
