from __future__ import annotations

import os
from pathlib import Path

from code_ai.core.rules import RuleSource
from code_ai.core.workflows import WorkflowSource
from code_ai.interop import paths
from code_ai.interop.mcp import McpDeclaration, json_servers
from code_ai.interop.subagents import AgentSource
from code_ai.tools.skills.common import SkillSource

# Cline reads two layouts, mid-migration, and so do we; the modern ``.cline``
# directory comes first in each scope:
#
#   modern:  ~/.cline/{rules,workflows,skills}/          (global)
#            <ws>/.cline/{rules,workflows,skills}/       (project)
#   legacy:  ~/Documents/Cline/{Rules,Workflows}/        (global)
#            <ws>/.clinerules                            (a single rules file)
#            <ws>/.clinerules/                           (a directory of rules)
#            <ws>/.clinerules/workflows/
#            <ws>/.clinerules/skills/
#
# The legacy documents folder is a Cline setting and lands in ``~/Cline`` on some
# Linux setups, so both that and an explicit override are honoured. MCP servers
# live in the editor's global storage (the VS Code extension) or under
# ``~/.cline`` (the CLI).

ORIGIN = "cline"

# Cline's own setting is not readable from here; this is the escape hatch.
CLINE_HOME_ENV = "CODE_AI_CLINE_HOME"

MODERN_DIRNAME = ".cline"
LEGACY_RULES_NAME = ".clinerules"

RULES_DIRNAME = "rules"
WORKFLOWS_DIRNAME = "workflows"
SKILLS_DIRNAME = "skills"

GLOBAL_RULES_DIRNAME = "Rules"
GLOBAL_WORKFLOWS_DIRNAME = "Workflows"

# Cline combines every ``.md`` and ``.txt`` file it finds in the rules folder.
RULE_EXTENSIONS = frozenset({".md", ".markdown", ".mdc", ".txt"})

# Loaded as workflows and skills, never as always-on rules.
_NON_RULE_DIRS = frozenset({WORKFLOWS_DIRNAME, SKILLS_DIRNAME})

EXTENSION_ID = "saoudrizwan.claude-dev"
MCP_SETTINGS_FILENAME = "cline_mcp_settings.json"
# Editors the extension is commonly installed in.
_EDITORS = ("Code", "Code - Insiders", "VSCodium", "Cursor", "Windsurf")


def cline_global_dir() -> Path:
    """Cline's install-wide directory in the current layout (``~/.cline``)."""

    return paths.home() / MODERN_DIRNAME


def cline_home() -> Path:
    """Legacy documents folder: ``~/Documents/Cline``, else ``~/Cline`` when only it exists."""

    override = os.environ.get(CLINE_HOME_ENV)
    if override:
        return Path(override).expanduser()
    documents = paths.home() / "Documents" / "Cline"
    if documents.is_dir():
        return documents
    alternate = paths.home() / "Cline"
    return alternate if alternate.is_dir() else documents


def _rules_dir(root: Path, *, scope: str = "project") -> RuleSource:
    return RuleSource(
        path=root,
        scope=scope,
        origin=ORIGIN,
        recursive=True,
        exclude_dirs=_NON_RULE_DIRS,
        extensions=RULE_EXTENSIONS,
    )


def rule_sources(workspace: Path | str) -> list[RuleSource]:
    """Global scope first. ``.clinerules`` is one rule as a file, a tree as a directory."""

    resolved = Path(workspace).expanduser().resolve()
    return [
        _rules_dir(cline_global_dir() / RULES_DIRNAME, scope="global"),
        _rules_dir(cline_home() / GLOBAL_RULES_DIRNAME, scope="global"),
        _rules_dir(resolved / MODERN_DIRNAME / RULES_DIRNAME),
        _rules_dir(resolved / LEGACY_RULES_NAME),
    ]


def workflow_sources(workspace: Path | str) -> list[WorkflowSource]:
    """Project before global, so a repository's procedure wins over a personal one."""

    resolved = Path(workspace).expanduser().resolve()
    return [
        WorkflowSource(
            root=resolved / MODERN_DIRNAME / WORKFLOWS_DIRNAME,
            scope="project",
            origin=ORIGIN,
        ),
        WorkflowSource(
            root=resolved / LEGACY_RULES_NAME / WORKFLOWS_DIRNAME,
            scope="project",
            origin=ORIGIN,
        ),
        WorkflowSource(
            root=cline_global_dir() / WORKFLOWS_DIRNAME,
            scope="global",
            origin=ORIGIN,
        ),
        WorkflowSource(
            root=cline_home() / GLOBAL_WORKFLOWS_DIRNAME,
            scope="global",
            origin=ORIGIN,
        ),
    ]


def skill_sources(workspace: Path | str) -> list[SkillSource]:
    """Global first, as Cline searches, so a name defined in both resolves the same way.

    ``.agents/skills`` is shared with other agents (see code_ai.interop.shared).
    """

    resolved = Path(workspace).expanduser().resolve()
    return [
        SkillSource(root=cline_global_dir() / SKILLS_DIRNAME, origin=ORIGIN),
        SkillSource(root=resolved / MODERN_DIRNAME / SKILLS_DIRNAME, origin=ORIGIN),
        SkillSource(root=resolved / LEGACY_RULES_NAME / SKILLS_DIRNAME, origin=ORIGIN),
    ]


def agent_sources(workspace: Path | str) -> list[AgentSource]:
    return []


def mcp_settings_files() -> list[Path]:
    storage = [
        paths.app_data() / editor / "User" / "globalStorage" / EXTENSION_ID / "settings"
        for editor in _EDITORS
    ]
    return [
        *(directory / MCP_SETTINGS_FILENAME for directory in storage),
        cline_global_dir() / "data" / "settings" / MCP_SETTINGS_FILENAME,
        cline_global_dir() / "mcp.json",
    ]


def mcp_declarations(workspace: Path | str) -> list[McpDeclaration]:
    found: list[McpDeclaration] = []
    for path in mcp_settings_files():
        if path.is_file():
            found += json_servers(path, ORIGIN, user_level=True)
    return found
