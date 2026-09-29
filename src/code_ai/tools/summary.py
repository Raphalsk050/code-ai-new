"""What a tool call is working on, as one short line for the front ends."""

from __future__ import annotations

_MAX_CHARS = 80

# The argument that names what a call acts on, most specific first: a search
# is about its query more than the folder it looks in. Typed text and key
# presses are left out on purpose: they can be a password.
_TARGET_KEYS = (
    "query",
    "path",
    "command",
    "url",
    "source",
    "output",
    "name",
    "site",
    "selector",
    "expression",
    "question",
    "completed_step",
    "group",
    "session_id",
    "focus",
    "action",
    "aspect",
)
# Paths lose their start when trimmed, everything else its end.
_PATH_KEYS = frozenset({"path", "source", "output"})
# Arguments holding a list of work items, described by their count.
_COUNTED = {"tasks": "task", "steps": "step", "operations": "change", "sources": "file"}
_SECRET_HINTS = ("password", "passwd", "secret", "token", "key", "auth", "credential")
# What gets typed or written: can be a password, and is never a short name anyway.
_TYPED = frozenset({"text", "content", "values", "chord", "new_text", "old_text"})


def call_target(name: str, arguments: object) -> str:
    """The file, command, query or URL a call is about; "" when none stands out."""
    if not isinstance(arguments, dict):
        return ""
    key, target = _pick(arguments)
    if not target:
        return ""
    if key == "path":
        start, end = arguments.get("start_line"), arguments.get("end_line")
        if isinstance(start, int):
            target += f":{start}" + (f"-{end}" if isinstance(end, int) else "")
    elif key == "query":
        where = arguments.get("path")
        if isinstance(where, str) and where.strip() not in ("", "."):
            target = f"{target} in {' '.join(where.split())}"
    if len(target) <= _MAX_CHARS:
        return target
    if key in _PATH_KEYS:
        return "…" + target[-(_MAX_CHARS - 1) :]
    return target[: _MAX_CHARS - 1] + "…"


def _pick(arguments: dict) -> tuple[str, str]:
    argv = arguments.get("argv")
    if isinstance(argv, list) and argv:
        return "command", " ".join(str(part) for part in argv)
    for key in _TARGET_KEYS:
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return key, " ".join(value.split())
    for key, noun in _COUNTED.items():
        value = arguments.get(key)
        if isinstance(value, list) and value:
            return key, f"{len(value)} {noun}{'' if len(value) == 1 else 's'}"
    # MCP tools and anything newer: the first short text argument that is not
    # a credential.
    for key, value in arguments.items():
        if key in _TYPED or any(hint in str(key).lower() for hint in _SECRET_HINTS):
            continue
        if isinstance(value, str) and 0 < len(value.strip()) <= 200:
            return str(key), " ".join(value.split())
    return "", ""
