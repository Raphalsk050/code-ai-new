from __future__ import annotations

from pathlib import Path

from code_ai.core.rules import RuleSource
from code_ai.core.workflows import WorkflowSource
from code_ai.interop import paths
from code_ai.interop.mcp import McpDeclaration, codex_server, load_toml
from code_ai.interop.subagents import AgentSource
from code_ai.tools.skills.common import SkillSource
from code_ai.util.arguments import CODEX

# OpenAI Codex, everything under $CODEX_HOME (~/.codex):
#
#   AGENTS.override.md, else AGENTS.md    global instructions
#   config.toml                           [mcp_servers.*], project_doc_fallback_filenames
#   prompts/*.md                          custom prompts, run as /prompts:<name>
#   skills/<name>/SKILL.md                skills before they moved to ~/.agents/skills
#   <repo>/.codex/config.toml             project MCP servers
#
# Project AGENTS.md files and .agents/skills are the shared convention (see
# code_ai.interop.shared). Codex's ``rules/`` are command-approval policies, not
# instructions, and are deliberately not read.

ORIGIN = "codex"
CODEX_HOME_ENV = "CODEX_HOME"

CONFIG_FILENAME = "config.toml"


def codex_home() -> Path:
    override = None if paths.redirected() else paths.env_path(CODEX_HOME_ENV)
    return override or paths.home() / ".codex"


def fallback_filenames() -> tuple[str, ...]:
    """Extra names Codex treats as AGENTS.md (``project_doc_fallback_filenames``)."""

    names = load_toml(codex_home() / CONFIG_FILENAME).get("project_doc_fallback_filenames")
    if not isinstance(names, list):
        return ()
    return tuple(str(name).strip() for name in names if str(name).strip())


def global_instruction_sources() -> list[RuleSource]:
    home = codex_home()
    return [
        RuleSource(
            path=home / "AGENTS.md",
            scope="global",
            origin=ORIGIN,
            authoritative=True,
            display_dir=paths.display_dir(home),
            override=home / "AGENTS.override.md",
        )
    ]


def rule_sources(workspace: Path | str) -> list[RuleSource]:
    return []


def skill_sources(workspace: Path | str) -> list[SkillSource]:
    sources = [SkillSource(root=codex_home() / "skills", origin=ORIGIN)]
    admin = paths.machine_file("", "/etc/codex/skills", "/etc/codex/skills")
    if admin is not None and str(admin):
        sources.append(SkillSource(root=admin, origin=ORIGIN))
    return sources


def workflow_sources(workspace: Path | str) -> list[WorkflowSource]:
    return [
        WorkflowSource(
            root=codex_home() / "prompts", scope="global", origin=ORIGIN, dialect=CODEX
        )
    ]


def agent_sources(workspace: Path | str) -> list[AgentSource]:
    return []


def mcp_declarations(workspace: Path | str) -> list[McpDeclaration]:
    found: list[McpDeclaration] = []
    files = [(codex_home() / CONFIG_FILENAME, True)]
    files += [
        (directory / ".codex" / CONFIG_FILENAME, False)
        for directory in reversed(paths.project_directories(workspace))
    ]
    for path, user_level in files:
        servers = load_toml(path).get("mcp_servers")
        if not isinstance(servers, dict):
            continue
        for name, raw in servers.items():
            if isinstance(raw, dict):
                found.append(McpDeclaration(str(name), codex_server(raw), ORIGIN, path, user_level))
    return found
