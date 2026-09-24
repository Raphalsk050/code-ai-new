"""Reuse of what the user already set up for other coding agents.

Instructions, rules, skills, commands, sub-agents and MCP servers written for
Claude Code, Codex, OpenCode or Cline - and the conventions they share, like
AGENTS.md - are read where they are, so nothing has to be copied or re-authored.
Every location is optional: an install with none of them behaves as before.

Each agent has a module mapping its layout onto Code-AI's sources; this one
decides the order they are consulted in.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import TypeVar

from code_ai.core.rules import RuleSource
from code_ai.core.subagents.profiles import SubagentProfile
from code_ai.core.workflows import WorkflowSource, native_workflow_sources
from code_ai.interop import claude_code, cline, codex, opencode, paths, shared
from code_ai.interop.mcp import McpDeclaration
from code_ai.interop.subagents import AgentSource, load_profiles
from code_ai.tools.skills.common import SkillSource, native_skill_source

__all__ = [
    "agent_sources",
    "claude_code",
    "cline",
    "codex",
    "external_rule_sources",
    "mcp_declarations",
    "opencode",
    "shared",
    "skill_sources",
    "subagent_profiles",
    "workflow_sources",
]

_T = TypeVar("_T")


def external_rule_sources(workspace: Path | str) -> list[RuleSource]:
    """Rules and instruction files owned by other agents.

    Instruction files go the user's first, then the repository's from its root
    down to the workspace - Claude Code's files before AGENTS.md in each
    directory, as Claude Code reads them - so the nearest file has the last
    word. Code-AI's own directories and CODEAI.md are added by the caller.
    """

    directories = paths.project_directories(workspace)
    root = directories[0]
    sources: list[RuleSource] = []
    for provider in (claude_code, codex, opencode):
        sources += provider.global_instruction_sources()
    for directory in directories:
        sources += claude_code.instruction_sources(directory, root=root)
        sources += shared.instruction_sources(directory, root=root)
    sources += opencode.project_instruction_sources(workspace)
    for provider in (cline, claude_code):
        sources += provider.rule_sources(workspace)
    return _unique(sources, key=lambda source: (source.path, source.override))


def skill_sources(workspace: Path | str) -> list[SkillSource]:
    """Every skill directory, highest precedence first; Code-AI's own leads."""

    sources = [native_skill_source()]
    for provider in (cline, claude_code, opencode, codex, shared):
        sources.extend(provider.skill_sources(workspace))
    return _unique(sources, key=lambda source: source.root)


def workflow_sources(workspace: Path | str) -> list[WorkflowSource]:
    """Code-AI's own first, then every project directory before any global one."""

    foreign = [
        source
        for provider in (cline, claude_code, opencode, codex)
        for source in provider.workflow_sources(workspace)
    ]
    foreign.sort(key=lambda source: source.scope != "project")
    return _unique(
        [*native_workflow_sources(workspace), *foreign],
        key=lambda source: (source.root, source.kind),
    )


def agent_sources(workspace: Path | str) -> list[AgentSource]:
    sources = [
        source
        for provider in (claude_code, opencode, cline, codex)
        for source in provider.agent_sources(workspace)
    ]
    return _unique(sources, key=lambda source: (source.root, source.kind))


def subagent_profiles(
    workspace: Path | str, *, reserved: Iterable[str] = ()
) -> list[SubagentProfile]:
    """Sub-agents defined for other agents; names in ``reserved`` stay built-in."""

    return load_profiles(agent_sources(workspace), reserved=reserved)


def mcp_declarations(workspace: Path | str) -> list[McpDeclaration]:
    """MCP servers other agents declare outside the workspace files code_ai.mcp reads."""

    return [
        declaration
        for provider in (claude_code, codex, opencode, cline)
        for declaration in provider.mcp_declarations(workspace)
    ]


def _unique(items: Iterable[_T], *, key) -> list[_T]:
    seen: set = set()
    unique: list[_T] = []
    for item in items:
        marker = key(item)
        if marker in seen:
            continue
        seen.add(marker)
        unique.append(item)
    return unique
