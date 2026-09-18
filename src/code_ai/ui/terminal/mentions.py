"""Typing ``@`` in the prompt offers the workspace's files.

Naming a file used to mean typing its path from memory, which is the kind of
small friction that gets paid on every single message. The shape here is the
one the prompt already uses for slash commands - a list under the box, Tab to
accept - so there is one thing to learn rather than two.

Kept apart from the widget on purpose: matching and ranking are the part worth
testing, and they need no terminal to run.
"""

from __future__ import annotations

import re
from pathlib import Path

from code_ai.util.ignore import WorkspaceIgnore

# The partial path under the caret: an ``@`` that starts the text or follows
# whitespace, then everything up to the end that is not whitespace. Anchored at
# the end because that is where the caret is while typing - an ``@`` earlier in
# a finished sentence is a mention the user already made, or an email address,
# and neither wants a list popping up underneath it.
_MENTION = re.compile(r"(?:^|(?<=\s))@([^\s@]*)$")

# How many files the picker offers. The list sits under a prompt box, so more
# than this stops being a glance and starts being a scroll.
MENTION_LIMIT = 8

# Files considered per keystroke. A monorepo walk is not free and the answer is
# cut to eight anyway, so the walk stops here rather than reading every path to
# rank a list nobody sees the bottom of.
_SCAN_LIMIT = 4000


def mention_prefix(text: str) -> str | None:
    """The partial path being typed after an ``@``, or None when there is none.

    Returns ``""`` for a bare ``@``, which is a real answer - it means "offer
    everything" - and is why this is not written as a truthiness check.
    """

    match = _MENTION.search(text)
    return match.group(1) if match else None


def file_suggestions(
    workspace: Path, prefix: str, *, limit: int = MENTION_LIMIT
) -> list[str]:
    """Workspace-relative paths matching ``prefix``, best first.

    Matching is case-insensitive and on the whole relative path, so ``@orch``
    finds ``src/code_ai/core/orchestration.py`` without typing the directories.
    """

    workspace = Path(workspace)
    if not workspace.is_dir():
        return []
    needle = prefix.replace("\\", "/").lower()
    # The ignore rules the rest of the agent uses, so the picker never offers a
    # path from .venv or node_modules that the file tools would refuse anyway.
    ignore = WorkspaceIgnore(workspace=workspace)
    scored: list[tuple[int, int, str]] = []
    scanned = 0
    for path in ignore.walk():
        scanned += 1
        if scanned > _SCAN_LIMIT:
            break
        try:
            relative = path.relative_to(workspace).as_posix()
        except ValueError:  # pragma: no cover - walk stays inside the workspace
            continue
        rank = _rank(relative.lower(), path.name.lower(), needle)
        if rank is None:
            continue
        scored.append((rank, len(relative), relative))
    scored.sort()
    return [relative for _rank, _length, relative in scored[:limit]]


def _rank(relative: str, name: str, needle: str) -> int | None:
    """How well one path answers ``needle``; lower is better, None is no match.

    The order is what someone typing expects: the file whose *name* starts with
    what they typed beats one that merely contains it somewhere in a directory
    further up the tree.
    """

    if not needle:
        return 2
    if name.startswith(needle):
        return 0
    if relative.startswith(needle):
        return 1
    if needle in name:
        return 2
    if needle in relative:
        return 3
    return None


def render_mentions(paths: list[str]) -> str:
    """The list shown under the prompt, in the slash-command panel's shape."""

    if not paths:
        return ""
    lines = [f"@{path}" for path in paths]
    return "\n".join(lines)


def mention_completion(text: str, paths: list[str]) -> str | None:
    """``text`` with the partial mention replaced by the best match.

    None when there is nothing to accept, which includes the case where the
    text already holds exactly what would be inserted - pressing Tab there must
    fall through to whatever Tab otherwise does rather than look broken.
    """

    prefix = mention_prefix(text)
    if prefix is None or not paths:
        return None
    completed = f"{text[: len(text) - len(prefix)]}{paths[0]} "
    return completed if completed != text else None
