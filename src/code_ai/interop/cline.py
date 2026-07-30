from __future__ import annotations

import os
from pathlib import Path

from code_ai.core.rules import RuleSource
from code_ai.core.workflows import WorkflowSource
from code_ai.tools.skills.common import SkillSource

# Cline's on-disk conventions, expressed as Code-AI sources.
#
# Cline is mid-migration between two layouts and reads both, so both are read
# here as well - in each scope the modern ``.cline`` directory comes first:
#
#   modern:  ~/.cline/{rules,workflows,skills}/          (global)
#            <ws>/.cline/{rules,workflows,skills}/       (project)
#   legacy:  ~/Documents/Cline/{Rules,Workflows}/        (global)
#            <ws>/.clinerules                            (a single rules file)
#            <ws>/.clinerules/                           (a directory of rules)
#            <ws>/.clinerules/workflows/
#            <ws>/.clinerules/skills/
#            <ws>/.agents/skills/
#
# The legacy documents folder is a Cline setting, and on some Linux setups it
# lands in ``~/Cline``, so both the alternate default and an explicit override
# are honoured.

ORIGIN = "cline"

# Points at Cline's global documents folder when it lives somewhere else (Cline's
# own setting is not readable from here, so an explicit override is the escape
# hatch).
CLINE_HOME_ENV = "CODE_AI_CLINE_HOME"

MODERN_DIRNAME = ".cline"
LEGACY_RULES_NAME = ".clinerules"
LEGACY_AGENTS_DIRNAME = ".agents"

RULES_DIRNAME = "rules"
WORKFLOWS_DIRNAME = "workflows"
SKILLS_DIRNAME = "skills"

GLOBAL_RULES_DIRNAME = "Rules"
GLOBAL_WORKFLOWS_DIRNAME = "Workflows"

# Cline combines every ``.md`` and ``.txt`` file it finds in the rules folder.
RULE_EXTENSIONS = frozenset({".md", ".markdown", ".mdc", ".txt"})

# Workflows and skills live inside the legacy rules folder but are loaded as
# workflows and skills, never as always-on rules.
_NON_RULE_DIRS = frozenset({WORKFLOWS_DIRNAME, SKILLS_DIRNAME})


def cline_global_dir() -> Path:
    """Cline's install-wide directory in the current layout (``~/.cline``)."""

    return Path.home() / MODERN_DIRNAME


def cline_home() -> Path:
    """Legacy documents folder holding install-wide rules and workflows.

    ``~/Documents/Cline`` is the documented default; ``~/Cline`` is where it
    lands on some Linux/WSL setups, so it is used when the default is absent.
    """

    override = os.environ.get(CLINE_HOME_ENV)
    if override:
        return Path(override).expanduser()
    documents = Path.home() / "Documents" / "Cline"
    if documents.is_dir():
        return documents
    alternate = Path.home() / "Cline"
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
    """Rule locations Cline reads, global scope first.

    The legacy ``.clinerules`` entry covers both of its shapes at once: as a file
    it is a single rule, as a directory it is a tree of them.
    """

    resolved = Path(workspace).expanduser().resolve()
    return [
        _rules_dir(cline_global_dir() / RULES_DIRNAME, scope="global"),
        _rules_dir(cline_home() / GLOBAL_RULES_DIRNAME, scope="global"),
        _rules_dir(resolved / MODERN_DIRNAME / RULES_DIRNAME),
        _rules_dir(resolved / LEGACY_RULES_NAME),
    ]


def workflow_sources(workspace: Path | str) -> list[WorkflowSource]:
    """Workflow directories Cline reads, project scope before global.

    Project workflows come first so a repository's procedure wins over a personal
    one of the same name, which is how a shared repo is expected to behave.
    """

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
    """Skill directories Cline scans, in Cline's own order.

    Global skills come first here because that is the order Cline searches, so a
    name defined in both scopes resolves to the same skill in both tools. Skills
    use the ``<name>/SKILL.md`` layout Code-AI already reads, so nothing else has
    to change.
    """

    resolved = Path(workspace).expanduser().resolve()
    return [
        SkillSource(root=cline_global_dir() / SKILLS_DIRNAME, origin=ORIGIN),
        SkillSource(root=resolved / MODERN_DIRNAME / SKILLS_DIRNAME, origin=ORIGIN),
        SkillSource(root=resolved / LEGACY_RULES_NAME / SKILLS_DIRNAME, origin=ORIGIN),
        SkillSource(root=resolved / LEGACY_AGENTS_DIRNAME / SKILLS_DIRNAME, origin=ORIGIN),
    ]
