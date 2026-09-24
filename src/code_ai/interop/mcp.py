"""MCP servers other agents declare, in the shape ``code_ai.mcp.config`` reads.

Each agent writes servers its own way; the provider modules translate theirs
into :class:`McpDeclaration` and the MCP manager takes it from there, approval
included - nothing declared outside Code-AI's own config starts unasked.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from code_ai.util import jsonc


@dataclass(frozen=True, slots=True)
class McpDeclaration:
    name: str
    raw: dict[str, Any]
    origin: str
    path: Path
    # In the user's own config rather than a repository: one approval covers
    # every workspace.
    user_level: bool


def json_servers(
    path: Path, origin: str, *, user_level: bool, key: str = "mcpServers"
) -> list[McpDeclaration]:
    block = jsonc.load_file(path).get(key)
    return declarations(block, origin=origin, path=path, user_level=user_level)


def declarations(
    block: Any, *, origin: str, path: Path, user_level: bool
) -> list[McpDeclaration]:
    if not isinstance(block, dict):
        return []
    return [
        McpDeclaration(str(name), dict(raw), origin, path, user_level)
        for name, raw in block.items()
        if isinstance(raw, dict)
    ]


def load_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


# -- Codex: [mcp_servers.<name>] in config.toml ---------------------------------


def codex_server(raw: dict[str, Any]) -> dict[str, Any]:
    entry: dict[str, Any] = {}
    if raw.get("url"):
        headers = {str(key): str(value) for key, value in (raw.get("http_headers") or {}).items()}
        for header, variable in (raw.get("env_http_headers") or {}).items():
            headers[str(header)] = "${" + str(variable) + "}"
        token = raw.get("bearer_token_env_var")
        if token:
            headers.setdefault("Authorization", "Bearer ${" + str(token) + "}")
        entry.update(type="http", url=raw["url"], headers=headers)
    else:
        entry.update(
            command=raw.get("command") or "",
            args=list(raw.get("args") or []),
            env=dict(raw.get("env") or {}),
            cwd=raw.get("cwd") or "",
        )
    if raw.get("enabled") is False:
        entry["enabled"] = False
    for theirs, ours in (("startup_timeout_sec", "startup_timeout_s"),
                         ("tool_timeout_sec", "tool_timeout_s")):
        if isinstance(raw.get(theirs), int | float):
            entry[ours] = raw[theirs]
    return entry


# -- OpenCode: "mcp" in opencode.json -------------------------------------------

_OPENCODE_ENV_RE = re.compile(r"\{env:([^}]+)\}")
_OPENCODE_FILE_RE = re.compile(r"\{file:([^}]+)\}")


def opencode_server(raw: dict[str, Any], base: Path) -> dict[str, Any]:
    entry: dict[str, Any] = {}
    remote = str(raw.get("type") or "").lower() == "remote"
    if remote or (raw.get("url") and not raw.get("command")):
        entry.update(
            type="http",
            url=opencode_value(raw.get("url"), base),
            headers={
                str(key): opencode_value(value, base)
                for key, value in (raw.get("headers") or {}).items()
            },
        )
    else:
        command = raw.get("command") or []
        words = command if isinstance(command, list) else str(command).split()
        words = [opencode_value(word, base) for word in words]
        entry.update(
            command=words[0] if words else "",
            args=words[1:],
            env={
                str(key): opencode_value(value, base)
                for key, value in (raw.get("environment") or {}).items()
            },
        )
    if raw.get("enabled") is False:
        entry["enabled"] = False
    return entry


def opencode_value(value: Any, base: Path) -> str:
    """``{env:VAR}`` becomes ``${VAR}``; ``{file:path}`` becomes the file's text."""

    text = _OPENCODE_ENV_RE.sub(lambda match: "${" + match.group(1).strip() + "}", str(value))

    def read(match: re.Match[str]) -> str:
        target = Path(match.group(1).strip()).expanduser()
        if not target.is_absolute():
            target = base / target
        try:
            return target.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError):
            return ""

    return _OPENCODE_FILE_RE.sub(read, text)
