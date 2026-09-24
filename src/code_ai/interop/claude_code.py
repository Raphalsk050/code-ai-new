from __future__ import annotations

import os
from pathlib import Path

from code_ai.core.rules import RuleSource
from code_ai.core.workflows import WorkflowSource
from code_ai.interop import paths
from code_ai.interop.mcp import McpDeclaration, declarations, json_servers
from code_ai.interop.subagents import AgentSource
from code_ai.tools.skills.common import SkillSource
from code_ai.util import jsonc
from code_ai.util.arguments import CLAUDE_CODE

# Anthropic's Claude Code (and Claude Desktop, for MCP servers):
#
#   managed CLAUDE.md                       IT-deployed, machine-wide
#   ~/.claude/CLAUDE.md, ~/.claude/rules/   the user's own instructions
#   <dir>/CLAUDE.md, .claude/CLAUDE.md,     project instructions, from the
#   CLAUDE.local.md, .claude/AGENTS.md      repository root down to the workspace
#   <dir>/.claude/rules/**/*.md             rules; ``paths:`` scopes one to files
#   {~,<dir>}/.claude/skills/<name>/        skills
#   {<dir>,~}/.claude/commands/**/*.md      commands, nested ones as /dir:name
#   {<dir>,~}/.claude/agents/**/*.md        sub-agents
#   ~/.claude.json                          MCP servers, user and per-project
#   claude_desktop_config.json              Claude Desktop's MCP servers
#
# ``@path`` imports are expanded and block HTML comments dropped, as Claude Code
# does; a repository's imports cannot reach outside it.

ORIGIN = "claude-code"
DESKTOP_ORIGIN = "claude-desktop"

DIRNAME = ".claude"
INSTRUCTIONS_FILENAME = "CLAUDE.md"
LOCAL_INSTRUCTIONS_FILENAME = "CLAUDE.local.md"


def user_dir() -> Path:
    return paths.home() / DIRNAME


def _instructions(
    path: Path, *, scope: str, display_dir: str, root: Path | None
) -> RuleSource:
    return RuleSource(
        path=path,
        scope=scope,
        origin=ORIGIN,
        authoritative=True,
        display_dir=display_dir,
        expand_imports=True,
        strip_comments=True,
        import_root=root,
    )


def _rules(path: Path, *, scope: str, root: Path | None) -> RuleSource:
    return RuleSource(
        path=path,
        scope=scope,
        origin=ORIGIN,
        recursive=True,
        expand_imports=True,
        strip_comments=True,
        import_root=root,
    )


def global_instruction_sources() -> list[RuleSource]:
    sources: list[RuleSource] = []
    managed = paths.machine_file(
        r"C:\Program Files\ClaudeCode\CLAUDE.md",
        "/Library/Application Support/ClaudeCode/CLAUDE.md",
        "/etc/claude-code/CLAUDE.md",
    )
    if managed is not None:
        sources.append(
            _instructions(managed, scope="global", display_dir=str(managed.parent), root=None)
        )
    sources.append(
        _instructions(
            user_dir() / INSTRUCTIONS_FILENAME,
            scope="global",
            display_dir=paths.display_dir(user_dir()),
            root=None,
        )
    )
    return sources


def instruction_sources(directory: Path, *, root: Path) -> list[RuleSource]:
    """One directory's files, in the order Claude Code reads them."""

    here = paths.display_dir(directory, root)
    nested = f"{here}/{DIRNAME}" if here else DIRNAME
    return [
        _instructions(directory / INSTRUCTIONS_FILENAME, scope="project", display_dir=here,
                      root=root),
        _instructions(directory / DIRNAME / INSTRUCTIONS_FILENAME, scope="project",
                      display_dir=nested, root=root),
        _instructions(directory / LOCAL_INSTRUCTIONS_FILENAME, scope="project",
                      display_dir=here, root=root),
        _instructions(directory / DIRNAME / "AGENTS.md", scope="project",
                      display_dir=nested, root=root),
    ]


def rule_sources(workspace: Path | str) -> list[RuleSource]:
    directories = paths.project_directories(workspace)
    return [
        _rules(user_dir() / "rules", scope="global", root=None),
        *(_rules(directory / DIRNAME / "rules", scope="project", root=directories[0])
          for directory in directories),
    ]


def skill_sources(workspace: Path | str) -> list[SkillSource]:
    """Personal before project: Claude Code's own precedence."""

    return [
        SkillSource(root=user_dir() / "skills", origin=ORIGIN, dialect=CLAUDE_CODE),
        *(SkillSource(root=directory / DIRNAME / "skills", origin=ORIGIN, dialect=CLAUDE_CODE)
          for directory in reversed(paths.project_directories(workspace))),
    ]


def workflow_sources(workspace: Path | str) -> list[WorkflowSource]:
    def commands(root: Path, scope: str) -> WorkflowSource:
        return WorkflowSource(
            root=root, scope=scope, origin=ORIGIN, dialect=CLAUDE_CODE,
            recursive=True, namespaced=True,
        )

    return [
        *(commands(directory / DIRNAME / "commands", "project")
          for directory in reversed(paths.project_directories(workspace))),
        commands(user_dir() / "commands", "global"),
    ]


def agent_sources(workspace: Path | str) -> list[AgentSource]:
    return [
        *(AgentSource(root=directory / DIRNAME / "agents", origin=ORIGIN, dialect=CLAUDE_CODE,
                      recursive=True)
          for directory in reversed(paths.project_directories(workspace))),
        AgentSource(root=user_dir() / "agents", origin=ORIGIN, dialect=CLAUDE_CODE,
                    recursive=True),
    ]


def mcp_declarations(workspace: Path | str) -> list[McpDeclaration]:
    """User and per-project servers from ~/.claude.json, then Claude Desktop's.

    ``.mcp.json`` in the repository is read with the other workspace files in
    :mod:`code_ai.mcp.config`.
    """

    path = paths.home() / ".claude.json"
    data = jsonc.load_file(path)
    project = _project_entry(data.get("projects"), workspace)
    found = [
        *declarations(project.get("mcpServers"), origin=ORIGIN, path=path, user_level=False),
        *declarations(data.get("mcpServers"), origin=ORIGIN, path=path, user_level=True),
    ]
    switched_off = {str(name) for name in project.get("disabledMcpServers") or []}
    for declaration in found:
        if declaration.name in switched_off:
            declaration.raw["enabled"] = False
    for folder in ("Claude", "claude"):
        desktop = paths.app_data() / folder / "claude_desktop_config.json"
        if desktop.is_file():
            found += json_servers(desktop, DESKTOP_ORIGIN, user_level=True)
            break
    return found


def _project_entry(projects: object, workspace: Path | str) -> dict:
    """Claude Code keys projects by path, with forward slashes even on Windows."""

    if not isinstance(projects, dict):
        return {}
    wanted = _comparable(Path(workspace).expanduser().resolve())
    for key, entry in projects.items():
        if isinstance(entry, dict) and _comparable(Path(str(key))) == wanted:
            return entry
    return {}


def _comparable(path: Path) -> str:
    return os.path.normcase(os.path.normpath(str(path)))
