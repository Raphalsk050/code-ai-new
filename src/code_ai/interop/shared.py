from __future__ import annotations

from pathlib import Path

from code_ai.core.rules import RuleSource
from code_ai.interop import codex, paths
from code_ai.tools.skills.common import SkillSource

# Conventions no single agent owns, read by Codex, OpenCode, Cline, Claude Code
# and more:
#
#   AGENTS.md          project instructions, from the repository root down to
#                      the workspace; AGENTS.override.md replaces it where present
#   .agents/skills/    skills in the Agent Skills layout, project and ~/.agents

ORIGIN = "agents"

INSTRUCTIONS_FILENAME = "AGENTS.md"
OVERRIDE_FILENAME = "AGENTS.override.md"


def instruction_sources(directory: Path, *, root: Path) -> list[RuleSource]:
    """The AGENTS.md of one directory between the repository root and the workspace."""

    return [
        RuleSource(
            path=directory / INSTRUCTIONS_FILENAME,
            scope="project",
            origin=ORIGIN,
            authoritative=True,
            display_dir=paths.display_dir(directory, root),
            override=directory / OVERRIDE_FILENAME,
            fallbacks=tuple(directory / name for name in codex.fallback_filenames()),
            # Claude Code expands imports in the AGENTS.md it reads.
            expand_imports=True,
            strip_comments=True,
            import_root=root,
        )
    ]


def skill_sources(workspace: Path | str) -> list[SkillSource]:
    """Nearest directory first, then the user's own."""

    directories = reversed(paths.project_directories(workspace))
    return [
        *(SkillSource(root=directory / ".agents" / "skills", origin=ORIGIN)
          for directory in directories),
        SkillSource(root=paths.home() / ".agents" / "skills", origin=ORIGIN),
    ]
