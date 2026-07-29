from __future__ import annotations

import os
from pathlib import Path

from code_ai.core.rules import RuleSource
from code_ai.core.workflows import WorkflowSource
from code_ai.tools.skills.common import SkillSource

# Cline's on-disk conventions, expressed as Code-AI sources.
#
# Project assets live in the repository under ``.clinerules``, which is either a
# single rules file or a directory. Inside the directory, ``workflows/`` holds
# named procedures and (in Cline builds that ship skills) ``skills/`` holds
# skill packages; both are excluded from the rules walk so a workflow is not
# force-fed to the model as an always-on rule.
#
# Install-wide assets live under a documents folder, one directory per kind. Its
# location is configurable in Cline, so an override is honoured here too.

ORIGIN = "cline"

# Points at Cline's global assets folder when the user relocated it (Cline's own
# setting is not readable from here, so an explicit override is the escape hatch).
CLINE_HOME_ENV = "CODE_AI_CLINE_HOME"

PROJECT_RULES_NAME = ".clinerules"
PROJECT_ALT_DIRNAME = ".cline"
WORKFLOWS_DIRNAME = "workflows"
SKILLS_DIRNAME = "skills"

GLOBAL_RULES_DIRNAME = "Rules"
GLOBAL_WORKFLOWS_DIRNAME = "Workflows"
GLOBAL_SKILLS_DIRNAME = "Skills"

# Cline shows every file it finds in the rules folder, not only markdown, so the
# common plain-text extensions are all treated as rule files.
RULE_EXTENSIONS = frozenset({".md", ".markdown", ".mdc", ".txt"})

_NON_RULE_DIRS = frozenset({WORKFLOWS_DIRNAME, SKILLS_DIRNAME})


def cline_home() -> Path:
    """Directory holding Cline's install-wide rules, workflows, and skills."""

    override = os.environ.get(CLINE_HOME_ENV)
    if override:
        return Path(override).expanduser()
    return Path.home() / "Documents" / "Cline"


def rule_sources(workspace: Path | str) -> list[RuleSource]:
    """Rule locations Cline reads, in scope order.

    ``.clinerules`` covers both supported project layouts at once: as a file it
    is a single rule, as a directory it is a tree of them.
    """

    resolved = Path(workspace).expanduser().resolve()
    return [
        RuleSource(
            path=cline_home() / GLOBAL_RULES_DIRNAME,
            scope="global",
            origin=ORIGIN,
            recursive=True,
            exclude_dirs=_NON_RULE_DIRS,
            extensions=RULE_EXTENSIONS,
        ),
        RuleSource(
            path=resolved / PROJECT_RULES_NAME,
            scope="project",
            origin=ORIGIN,
            recursive=True,
            exclude_dirs=_NON_RULE_DIRS,
            extensions=RULE_EXTENSIONS,
        ),
    ]


def workflow_sources(workspace: Path | str) -> list[WorkflowSource]:
    """Workflow directories Cline reads, project scope before global.

    Project workflows come first so a repository's procedure wins over a
    personal one of the same name, which is how a shared repo is expected to
    behave.
    """

    resolved = Path(workspace).expanduser().resolve()
    return [
        WorkflowSource(
            root=resolved / PROJECT_RULES_NAME / WORKFLOWS_DIRNAME,
            scope="project",
            origin=ORIGIN,
        ),
        WorkflowSource(
            root=cline_home() / GLOBAL_WORKFLOWS_DIRNAME,
            scope="global",
            origin=ORIGIN,
        ),
    ]


def skill_sources(workspace: Path | str) -> list[SkillSource]:
    """Candidate skill directories for a Cline install.

    Cline's skill packages use the same ``<name>/SKILL.md`` layout Code-AI reads,
    so the only open question is where they sit. Every plausible location is
    probed and missing ones are silently skipped: a user who keeps skills in one
    of them gets them for free, and a user with none pays nothing.
    """

    resolved = Path(workspace).expanduser().resolve()
    return [
        SkillSource(root=resolved / PROJECT_RULES_NAME / SKILLS_DIRNAME, origin=ORIGIN),
        SkillSource(root=resolved / PROJECT_ALT_DIRNAME / SKILLS_DIRNAME, origin=ORIGIN),
        SkillSource(root=cline_home() / GLOBAL_SKILLS_DIRNAME, origin=ORIGIN),
    ]
