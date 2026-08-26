"""Reuse of rules, skills, and workflows authored for other coding agents.

A user who already wrote instructions for another agent should not have to
re-author or copy anything to get the same behaviour here. This package maps
those agents' on-disk conventions onto Code-AI's own source objects; discovery is
purely additive and every location is optional, so an install with none of them
present behaves exactly as before.

Each provider module (see :mod:`code_ai.interop.cline`) exposes the same three
functions - ``rule_sources``, ``workflow_sources``, ``skill_sources`` - so
supporting another agent means adding a module and listing it in ``_PROVIDERS``.
"""

from __future__ import annotations

from pathlib import Path

from code_ai.core.rules import RuleSource
from code_ai.core.workflows import WorkflowSource, native_workflow_sources
from code_ai.interop import cline
from code_ai.tools.skills.common import SkillSource, native_skill_source

_PROVIDERS = (cline,)

__all__ = [
    "cline",
    "external_rule_sources",
    "skill_sources",
    "workflow_sources",
]


def external_rule_sources(workspace: Path | str) -> list[RuleSource]:
    """Rule locations owned by other agents.

    Returned separately from Code-AI's own rule directories because
    :class:`~code_ai.core.rules.RulesService` already knows those and keeps them
    first in each scope.
    """

    return [source for provider in _PROVIDERS for source in provider.rule_sources(workspace)]


def skill_sources(workspace: Path | str) -> list[SkillSource]:
    """Every skill directory to search this session, highest precedence first.

    Code-AI's own directory leads, so a skill the user (or ``create_skill``)
    wrote here shadows a same-named one picked up from another agent.
    """

    sources = [native_skill_source()]
    for provider in _PROVIDERS:
        sources.extend(provider.skill_sources(workspace))
    return sources


def workflow_sources(workspace: Path | str) -> list[WorkflowSource]:
    """Every workflow directory to search this session, highest precedence first."""

    sources = list(native_workflow_sources(workspace))
    for provider in _PROVIDERS:
        sources.extend(provider.workflow_sources(workspace))
    return sources
