"""Sub-agents defined for other agents, as Code-AI sub-agent profiles.

Claude Code and OpenCode both keep one markdown file per agent - a description
to delegate on, the tools it may use, and a body that is its system prompt. The
tools are mapped onto Code-AI capabilities; a tool with no counterpart here is
dropped rather than guessed at.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from code_ai.core.subagents.profiles import SubagentProfile
from code_ai.interop.mcp import opencode_value
from code_ai.tools.base import ToolCapability
from code_ai.util import jsonc
from code_ai.util.frontmatter import as_list, as_text, is_true, split_frontmatter

CLAUDE_CODE = "claude-code"
OPENCODE = "opencode"

KIND_MARKDOWN = "markdown"
KIND_OPENCODE_JSON = "opencode-json"

_READ = ToolCapability.LOCAL_READ
_WRITE = ToolCapability.LOCAL_WRITE
_PROCESS = ToolCapability.PROCESS
_WEB = ToolCapability.WEB
_MCP = ToolCapability.MCP

# Everything a sub-agent may hold here: what "no tools field" means in both agents.
_ALL = frozenset({_READ, _WRITE, _PROCESS, _WEB, _MCP})

_CLAUDE_TOOLS: dict[str, ToolCapability] = {
    **dict.fromkeys(("read", "grep", "glob", "ls", "lsp", "notebookread"), _READ),
    **dict.fromkeys(("edit", "write", "multiedit", "notebookedit"), _WRITE),
    **dict.fromkeys(("bash", "powershell", "bashoutput", "killshell", "monitor"), _PROCESS),
    **dict.fromkeys(("webfetch", "websearch"), _WEB),
}
_OPENCODE_TOOLS: dict[str, ToolCapability] = {
    **dict.fromkeys(("read", "grep", "glob", "list", "lsp"), _READ),
    **dict.fromkeys(("edit", "write", "patch", "multiedit"), _WRITE),
    "bash": _PROCESS,
    **dict.fromkeys(("webfetch", "websearch"), _WEB),
}

_NAME_RE = re.compile(r"[^a-z0-9_-]+")
_MIN_STEPS, _MAX_STEPS = 5, 200


@dataclass(frozen=True, slots=True)
class AgentSource:
    root: Path
    origin: str
    dialect: str
    recursive: bool = False
    kind: str = KIND_MARKDOWN


def load_profiles(
    sources: Sequence[AgentSource], *, reserved: Iterable[str] = ()
) -> list[SubagentProfile]:
    """Every agent the sources define; a name already taken keeps its first owner."""

    taken = {name.casefold() for name in reserved}
    profiles: list[SubagentProfile] = []
    for source in sources:
        for profile in _load_source(source):
            if profile.name in taken:
                continue
            taken.add(profile.name)
            profiles.append(profile)
    return profiles


def _load_source(source: AgentSource) -> list[SubagentProfile]:
    if source.kind == KIND_OPENCODE_JSON:
        agents = jsonc.load_file(source.root).get("agent")
        if not isinstance(agents, dict):
            return []
        return [
            profile
            for name, entry in sorted(agents.items())
            if isinstance(entry, dict)
            and (
                profile := _opencode(
                    str(name),
                    entry,
                    opencode_value(entry.get("prompt") or "", source.root.parent),
                    source,
                    source.root,
                )
            )
        ]
    if not source.root.is_dir():
        return []
    try:
        files = sorted(
            source.root.rglob("*.md") if source.recursive else source.root.glob("*.md"),
            key=lambda item: str(item).casefold(),
        )
    except OSError:
        return []
    profiles: list[SubagentProfile] = []
    for path in files:
        try:
            front, body = split_frontmatter(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
        if source.dialect == CLAUDE_CODE:
            profile = _claude(front, body, source, path)
        else:
            profile = _opencode(path.stem, front, body, source, path)
        if profile is not None:
            profiles.append(profile)
    return profiles


def _claude(
    front: dict[str, Any], body: str, source: AgentSource, path: Path
) -> SubagentProfile | None:
    # Claude Code skips a file with no name or no description, and so do we.
    name, description = as_text(front.get("name")), as_text(front.get("description"))
    if not name or not description:
        return None
    tools = as_list(front.get("tools"))
    capabilities = _mapped(tools, _CLAUDE_TOOLS) if tools else set(_ALL)
    for tool in as_list(front.get("disallowedtools")):
        capabilities -= _mapped([tool], _CLAUDE_TOOLS)
    return _profile(
        name, description, body, capabilities, front.get("maxturns"), source, path
    )


def _opencode(
    name: str, front: dict[str, Any], body: str, source: AgentSource, path: Path
) -> SubagentProfile | None:
    description = as_text(front.get("description"))
    mode = as_text(front.get("mode")).casefold()
    if not description or mode == "primary" or is_true(front.get("disable")):
        return None
    capabilities = set(_ALL)
    switches = front.get("tools")
    if isinstance(switches, dict):
        for tool, enabled in switches.items():
            if str(enabled).strip().casefold() == "false":
                capabilities -= _mapped([tool], _OPENCODE_TOOLS)
    permission = front.get("permission")
    if isinstance(permission, dict):
        for tool, rule in permission.items():
            if isinstance(rule, str) and rule.strip().casefold() == "deny":
                capabilities -= _mapped([tool], _OPENCODE_TOOLS)
    prompt = as_text(front.get("prompt")) if not body else body
    steps = front.get("steps") or front.get("maxsteps")
    return _profile(name, description, prompt, capabilities, steps, source, path)


def _mapped(tools: Iterable[str], table: dict[str, ToolCapability]) -> set[ToolCapability]:
    capabilities: set[ToolCapability] = set()
    for tool in tools:
        # "Bash(git *)" restricts the tool; the tool is still the tool.
        base = tool.split("(", 1)[0].strip().casefold()
        if base.startswith("mcp__"):
            capabilities.add(_MCP)
        elif base in table:
            capabilities.add(table[base])
    return capabilities


def _profile(
    name: str,
    description: str,
    prompt: str,
    capabilities: set[ToolCapability],
    steps: Any,
    source: AgentSource,
    path: Path,
) -> SubagentProfile | None:
    slug = _NAME_RE.sub("-", name.strip().casefold()).strip("-")
    if not slug:
        return None
    # An agent that cannot read cannot do anything useful, and reading is safe.
    capabilities.add(_READ)
    acts = bool(capabilities & {_WRITE, _PROCESS})
    try:
        max_steps = min(max(int(str(steps).strip()), _MIN_STEPS), _MAX_STEPS)
    except ValueError:
        # Not said: no cap, like the built-in agents.
        max_steps = 0
    return SubagentProfile(
        name=slug,
        description=" ".join(description.split()),
        allowed_capabilities=frozenset(capabilities),
        max_model_steps=max_steps,
        role_prompt=prompt.strip() or description,
        timeout_field="subagent_worker_timeout_s" if acts else "subagent_explorer_timeout_s",
        origin=source.origin,
        source_path=str(path),
    )
