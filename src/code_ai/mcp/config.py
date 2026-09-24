"""Where MCP servers are declared, and what one declaration says.

Servers come from the user's own ``config.json`` (``mcp_servers``), from the
workspace - Code-AI's ``.code-ai/mcp.json`` plus ``.mcp.json`` (Claude Code),
``.cursor/mcp.json`` and ``.vscode/mcp.json`` - and from what other agents keep
for the user (see :func:`code_ai.interop.mcp_declarations`), so servers already
set up for one of them work here untouched.

Anything outside ``config.json`` can start any program on the user's machine, so
it waits for the user's approval. The approval is bound to what the server runs
(its fingerprint): editing the command asks again.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

TRANSPORTS = ("stdio", "http", "sse")
DEFER_MODES = ("auto", "always", "never")

# Where a declaration came from, in the order a name collision is settled: the
# user's own config wins over anything a repository ships.
SOURCE_CONFIG = "config"
SOURCE_PROJECT = "project"
_PROJECT_FILES: tuple[tuple[str, str], ...] = (
    (".code-ai/mcp.json", SOURCE_PROJECT),
    (".mcp.json", "claude"),
    (".cursor/mcp.json", "cursor"),
    (".vscode/mcp.json", "vscode"),
)

_VARIABLE = re.compile(r"\$\{([^}]+)\}")
_NAME = re.compile(r"[^A-Za-z0-9_-]+")


@dataclass(slots=True)
class McpServerConfig:
    name: str
    transport: str = "stdio"
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str = ""
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    # client_id, client_secret, scopes: for a server that does not let clients register.
    oauth: dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    # Tools the server marks read-only run without an approval prompt.
    trust: bool = False
    # Whether the tools wait in a group until the model loads it.
    defer: str = "auto"
    startup_timeout_s: float = 90.0
    timeout_s: float = 60.0
    tool_timeout_s: float = 600.0
    source: str = SOURCE_CONFIG
    source_path: str = ""
    # Declared in another agent's user config, not a repository: one approval
    # holds in every workspace.
    user_level: bool = False
    # Set when the declaration could not be read; the server is listed, never run.
    problem: str = ""

    @property
    def from_workspace(self) -> bool:
        return self.source != SOURCE_CONFIG

    def fingerprint(self, workspace: Path | str) -> str:
        """What an approval is bound to: the workspace, the name and what runs.

        The environment counts: ``NODE_OPTIONS`` alone can make an approved
        command run something else.
        """

        body = json.dumps(
            {
                "workspace": "" if self.user_level else str(workspace),
                "name": self.name,
                "transport": self.transport,
                "command": self.command,
                "args": self.args,
                "env": self.env,
                "cwd": self.cwd,
                "url": self.url,
                "headers": self.headers,
            },
            sort_keys=True,
        )
        return hashlib.sha256(body.encode("utf-8")).hexdigest()[:32]

    def payload(self) -> dict[str, Any]:
        """The declaration as the settings panel edits it, secrets and all."""

        return {
            "name": self.name,
            "transport": self.transport,
            "command": self.command,
            "args": list(self.args),
            "env": dict(self.env),
            "cwd": self.cwd,
            "url": self.url,
            "headers": dict(self.headers),
            "oauth": dict(self.oauth),
            "enabled": self.enabled,
            "trust": self.trust,
            "defer": self.defer,
            "timeout_s": self.timeout_s,
            "tool_timeout_s": self.tool_timeout_s,
            "source": self.source,
            "source_path": self.source_path,
            "user_level": self.user_level,
            "problem": self.problem,
        }

    def to_config(self) -> dict[str, Any]:
        """The entry written back under ``mcp_servers``: only what differs from default."""

        entry: dict[str, Any] = {}
        if self.transport == "stdio":
            entry["command"] = self.command
            if self.args:
                entry["args"] = list(self.args)
            if self.env:
                entry["env"] = dict(self.env)
            if self.cwd:
                entry["cwd"] = self.cwd
        else:
            entry["type"] = self.transport
            entry["url"] = self.url
            if self.headers:
                entry["headers"] = dict(self.headers)
            if self.oauth:
                entry["oauth"] = dict(self.oauth)
        if not self.enabled:
            entry["enabled"] = False
        if self.trust:
            entry["trust"] = True
        if self.defer != "auto":
            entry["defer"] = self.defer
        for key, default in (("timeout_s", 60.0), ("tool_timeout_s", 600.0)):
            value = getattr(self, key)
            if value != default:
                entry[key] = value
        return entry


def server_name(raw: str) -> str:
    """A name safe to put inside a tool name: letters, digits, ``_`` and ``-``."""

    cleaned = _NAME.sub("_", raw.strip()).strip("_")
    return cleaned[:40] or "server"


def parse_server(
    name: str, raw: Any, *, source: str = SOURCE_CONFIG, source_path: str = ""
) -> McpServerConfig:
    """One declaration, in any of the shapes the supported agents write."""

    server = McpServerConfig(name=server_name(name), source=source, source_path=source_path)
    if not isinstance(raw, dict):
        server.problem = "The declaration is not a JSON object."
        return server
    kind = str(raw.get("type") or raw.get("transport") or "").strip().lower()
    if kind in ("streamable-http", "streamablehttp", "http-stream"):
        kind = "http"
    url = str(raw.get("url") or raw.get("serverUrl") or "").strip()
    command = str(raw.get("command") or "").strip()
    if not kind:
        kind = "stdio" if command else "http" if url else ""
    if kind not in TRANSPORTS:
        server.problem = (
            f"Unknown transport {kind!r}; use stdio, http or sse."
            if kind
            else "Give it a command (stdio) or a url (http)."
        )
        return server
    server.transport = kind
    if kind == "stdio":
        if not command:
            server.problem = "A stdio server needs a command."
        server.command = command
        server.args = [str(item) for item in raw.get("args") or [] if item is not None]
        server.env = _string_map(raw.get("env"))
        server.cwd = str(raw.get("cwd") or "").strip()
    else:
        if not url.startswith(("http://", "https://")):
            server.problem = "The url must start with http:// or https://."
        server.url = url
        server.headers = _string_map(raw.get("headers"))
        server.oauth = _oauth(raw.get("oauth"))
    server.enabled = raw.get("enabled", raw.get("disabled") is not True) is not False
    # Trust is the user's to give; a repository cannot declare itself trusted.
    server.trust = raw.get("trust") is True and source == SOURCE_CONFIG
    defer = str(raw.get("defer") or "auto").strip().lower()
    server.defer = defer if defer in DEFER_MODES else "auto"
    for key in ("startup_timeout_s", "timeout_s", "tool_timeout_s"):
        value = raw.get(key)
        if isinstance(value, int | float) and value > 0:
            setattr(server, key, float(value))
    return server


def load_servers(
    configured: dict[str, Any] | None, workspace: Path | str | None
) -> list[McpServerConfig]:
    """Every server declared for this session, the user's config first."""

    servers: dict[str, McpServerConfig] = {}
    for name, raw in (configured or {}).items():
        server = parse_server(str(name), raw)
        servers.setdefault(server.name, server)
    if workspace is not None:
        root = Path(workspace)
        for relative, source in _PROJECT_FILES:
            path = root / relative
            for name, raw in _read_declarations(path).items():
                server = parse_server(name, raw, source=source, source_path=str(path))
                servers.setdefault(server.name, server)
        for declaration in _foreign(root):
            server = parse_server(
                declaration.name,
                declaration.raw,
                source=declaration.origin,
                source_path=str(declaration.path),
            )
            server.user_level = declaration.user_level
            servers.setdefault(server.name, server)
    return list(servers.values())


def _foreign(workspace: Path) -> list[Any]:
    from code_ai.interop import mcp_declarations

    try:
        return mcp_declarations(workspace)
    except Exception:  # noqa: BLE001 - someone else's config must not stop the session
        return []


def parse_import(text: str) -> tuple[list[tuple[str, dict[str, Any]]], str]:
    """Servers out of a pasted snippet, in any shape a README hands over.

    ``{"mcpServers": {...}}`` (Claude, Cursor), ``{"servers": {...}}`` (VS Code)
    or the bare name-to-server map. Returns the servers and, when there are
    none, why.
    """

    try:
        data = json.loads(text)
    except ValueError as exc:
        return [], f"Not valid JSON: {exc}"
    if not isinstance(data, dict):
        return [], "Paste a JSON object."
    block = data.get("mcpServers", data.get("servers", data.get("mcp_servers", data)))
    servers = [
        (server_name(str(name)), dict(entry))
        for name, entry in (block.items() if isinstance(block, dict) else ())
        if isinstance(entry, dict)
        and (entry.get("command") or entry.get("url") or entry.get("serverUrl"))
    ]
    if not servers:
        return [], "No server found: each one needs a command or a url."
    return servers, ""


def quick_declaration(target: str, extras: str = "", *, legacy_sse: bool = False) -> dict[str, Any]:
    """A declaration from one line: a command line or a URL, plus ``KEY=value; ...``.

    The pairs are the environment of a command and the headers of a URL. What
    the terminal's add form and ``/mcp add`` build a server from.
    """

    target = target.strip()
    pairs: dict[str, str] = {}
    for part in extras.split(";"):
        key, found, value = part.partition("=")
        if found and key.strip():
            pairs[key.strip()] = value.strip()
    if target.startswith(("http://", "https://")):
        return {"type": "sse" if legacy_sse else "http", "url": target, "headers": pairs}
    try:
        # Backslashes are path separators on Windows, not escapes.
        words = shlex.split(target, posix=os.name != "nt")
    except ValueError:
        words = target.split()
    words = [word.strip('"') for word in words]
    return {"command": words[0] if words else "", "args": words[1:], "env": pairs}


def expand(value: str, *, workspace: Path | str | None, env: dict[str, str] | None = None) -> str:
    """Fill ``${VAR}``, ``${env:VAR}``, ``${VAR:-default}`` and ``${workspaceFolder}``.

    An unknown variable becomes empty rather than staying as literal text: a
    token sent as ``${GITHUB_TOKEN}`` is a harder failure to read than a missing
    one, and :func:`missing_variables` says which ones were empty.
    """

    lookup = {**os.environ, **(env or {})}

    def fill(match: re.Match[str]) -> str:
        inner = match.group(1).strip()
        if inner in ("workspaceFolder", "workspaceRoot") and workspace is not None:
            return str(workspace)
        if inner.startswith("env:"):
            inner = inner[4:]
        name, _, default = inner.partition(":-")
        return lookup.get(name.strip(), default)

    return _VARIABLE.sub(fill, value)


def missing_variables(values: list[str], *, env: dict[str, str] | None = None) -> list[str]:
    lookup = {**os.environ, **(env or {})}
    missing: list[str] = []
    for value in values:
        for match in _VARIABLE.finditer(value):
            inner = match.group(1).strip()
            if inner in ("workspaceFolder", "workspaceRoot") or ":-" in inner:
                continue
            name = inner[4:] if inner.startswith("env:") else inner
            if name.startswith("input:"):
                missing.append(name)
            elif name not in lookup and name not in missing:
                missing.append(name)
    return missing


def _read_declarations(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # A broken file in someone's repository must not stop the session.
        return {}
    if not isinstance(data, dict):
        return {}
    block = data.get("mcpServers", data.get("servers", data.get("mcp_servers")))
    return block if isinstance(block, dict) else {}


def _oauth(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    scopes = raw.get("scopes", raw.get("scope"))
    if isinstance(scopes, list):
        scopes = " ".join(str(item) for item in scopes)
    values = {
        "client_id": raw.get("client_id") or raw.get("clientId"),
        "client_secret": raw.get("client_secret") or raw.get("clientSecret"),
        "scopes": scopes,
    }
    return {key: str(value) for key, value in values.items() if value}


def _string_map(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    return {str(key): str(value) for key, value in raw.items() if value is not None}
