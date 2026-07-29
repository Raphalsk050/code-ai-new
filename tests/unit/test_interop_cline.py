from __future__ import annotations

from pathlib import Path

import pytest

from code_ai.core.rules import RulesService
from code_ai.core.workflows import WorkflowService
from code_ai.interop import cline, external_rule_sources, skill_sources, workflow_sources
from code_ai.tools.skills.common import (
    SKILLS_DIR_ENV,
    discover_skills_from,
    load_skill_from,
    render_skills_catalog,
)


@pytest.fixture
def cline_home(tmp_path, monkeypatch) -> Path:
    """A relocated Cline documents folder, so no test touches the real one."""

    home = tmp_path / "ClineDocs"
    monkeypatch.setenv(cline.CLINE_HOME_ENV, str(home))
    return home


@pytest.fixture
def native_skills(tmp_path, monkeypatch) -> Path:
    target = tmp_path / "code-ai-skills"
    target.mkdir()
    monkeypatch.setenv(SKILLS_DIR_ENV, str(target))
    return target


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# --------------------------------------------------------------------------- #
# Rules
# --------------------------------------------------------------------------- #


def test_bare_clinerules_file_is_loaded_as_a_project_rule(tmp_path, cline_home) -> None:
    workspace = tmp_path / "ws"
    _write(workspace / ".clinerules", "Sempre rode pytest -q antes de concluir.")

    service = RulesService(
        global_dir=tmp_path / "g",
        project_dir=tmp_path / "p",
        extra_sources=external_rule_sources(workspace),
    )
    records = service.load()

    assert [(record.scope, record.origin) for record in records] == [("project", "cline")]
    assert records[0].body == "Sempre rode pytest -q antes de concluir."
    rendered = service.render_for_prompt()
    assert "MANDATORY" in rendered
    assert "(project, cline)" in rendered


def test_clinerules_directory_is_loaded_without_workflows_or_skills(tmp_path, cline_home) -> None:
    workspace = tmp_path / "ws"
    _write(workspace / ".clinerules" / "style.md", "Prefira funcoes pequenas.")
    _write(workspace / ".clinerules" / "nested" / "tests.txt", "Rode os testes.")
    _write(workspace / ".clinerules" / "workflows" / "deploy.md", "1. Build\n2. Ship")
    _write(workspace / ".clinerules" / "skills" / "pdf" / "SKILL.md", "---\nname: pdf\n---\nbody")
    _write(cline_home / "Rules" / "language.md", "Responda em pt-BR.")

    service = RulesService(
        global_dir=tmp_path / "g",
        project_dir=tmp_path / "p",
        extra_sources=external_rule_sources(workspace),
    )
    bodies = [record.body for record in service.load()]

    assert "Prefira funcoes pequenas." in bodies
    assert "Rode os testes." in bodies
    assert "Responda em pt-BR." in bodies
    # Workflows and skills live inside the rules folder but are not always-on rules.
    assert not any("Ship" in body for body in bodies)
    assert not any("body" == body for body in bodies)


def test_global_cline_rules_come_before_project_rules(tmp_path, cline_home) -> None:
    workspace = tmp_path / "ws"
    _write(cline_home / "Rules" / "global.md", "Regra global.")
    _write(workspace / ".clinerules" / "project.md", "Regra do projeto.")

    service = RulesService(
        global_dir=tmp_path / "g",
        project_dir=tmp_path / "p",
        extra_sources=external_rule_sources(workspace),
    )

    assert [record.scope for record in service.load()] == ["global", "project"]


def test_native_rules_lead_their_scope(tmp_path, cline_home) -> None:
    workspace = tmp_path / "ws"
    _write(tmp_path / "p" / "own.md", "Regra propria.")
    _write(workspace / ".clinerules" / "foreign.md", "Regra do cline.")

    service = RulesService(
        global_dir=tmp_path / "g",
        project_dir=tmp_path / "p",
        extra_sources=external_rule_sources(workspace),
    )

    assert [record.origin for record in service.load()] == ["code-ai", "cline"]


def test_missing_cline_locations_add_nothing(tmp_path, cline_home) -> None:
    service = RulesService(
        global_dir=tmp_path / "g",
        project_dir=tmp_path / "p",
        extra_sources=external_rule_sources(tmp_path / "ws"),
    )

    assert service.load() == []
    assert service.render_for_prompt() == ""


# --------------------------------------------------------------------------- #
# Skills
# --------------------------------------------------------------------------- #


def test_cline_skills_are_discovered_and_labelled(tmp_path, cline_home, native_skills) -> None:
    workspace = tmp_path / "ws"
    _write(
        cline_home / "Skills" / "pdf-magic" / "SKILL.md",
        "---\nname: pdf-magic\ndescription: Extract tables from PDFs.\n---\n\nDo it.",
    )
    _write(
        native_skills / "release-notes.md",
        "---\nname: release-notes\ndescription: Draft release notes.\n---\n\nSteps.",
    )

    sources = skill_sources(workspace)
    records = discover_skills_from(sources)

    assert {(record.name, record.origin) for record in records} == {
        ("pdf-magic", "cline"),
        ("release-notes", "code-ai"),
    }
    catalog = render_skills_catalog(sources)
    assert "- pdf-magic (cline): Extract tables from PDFs." in catalog
    assert "- release-notes: Draft release notes." in catalog


def test_workspace_cline_skill_directory_is_discovered(tmp_path, cline_home, native_skills) -> None:
    workspace = tmp_path / "ws"
    _write(
        workspace / ".clinerules" / "skills" / "triage" / "SKILL.md",
        "---\nname: triage\ndescription: Triage a bug report.\n---\n\nAsk for repro steps.",
    )

    record = load_skill_from("triage", sources=skill_sources(workspace))

    assert record.origin == "cline"
    assert "repro steps" in record.body


def test_native_skill_shadows_a_same_named_cline_skill(tmp_path, cline_home, native_skills) -> None:
    workspace = tmp_path / "ws"
    _write(
        native_skills / "review" / "SKILL.md",
        "---\nname: review\ndescription: Mine.\n---\n\nMine wins.",
    )
    _write(
        cline_home / "Skills" / "review" / "SKILL.md",
        "---\nname: review\ndescription: Theirs.\n---\n\nTheirs loses.",
    )

    sources = skill_sources(workspace)
    records = discover_skills_from(sources)

    assert [(record.name, record.origin) for record in records] == [("review", "code-ai")]
    assert load_skill_from("review", sources=sources).body == "Mine wins."


# --------------------------------------------------------------------------- #
# Workflows
# --------------------------------------------------------------------------- #


def test_cline_workflows_are_discovered_from_both_scopes(tmp_path, cline_home, monkeypatch) -> None:
    workspace = tmp_path / "ws"
    monkeypatch.setenv("CODE_AI_WORKFLOWS_DIR", str(tmp_path / "own-workflows"))
    _write(workspace / ".clinerules" / "workflows" / "deploy.md", "# Deploy\n\n1. Build\n2. Ship")
    _write(cline_home / "Workflows" / "triage.md", "Ask for repro steps first.")

    service = WorkflowService(sources=workflow_sources(workspace))
    records = service.load()

    assert [(record.name, record.scope, record.origin) for record in records] == [
        ("deploy", "project", "cline"),
        ("triage", "global", "cline"),
    ]
    # No frontmatter: the description falls back to the file's own first lines.
    assert records[0].description == "Deploy"
    assert records[1].description == "Ask for repro steps first."


def test_project_workflow_shadows_the_global_one(tmp_path, cline_home, monkeypatch) -> None:
    workspace = tmp_path / "ws"
    monkeypatch.setenv("CODE_AI_WORKFLOWS_DIR", str(tmp_path / "own-workflows"))
    _write(workspace / ".clinerules" / "workflows" / "deploy.md", "Project steps.")
    _write(cline_home / "Workflows" / "deploy.md", "Global steps.")

    service = WorkflowService(sources=workflow_sources(workspace))

    assert [record.body for record in service.load()] == ["Project steps."]


def test_native_workflow_shadows_a_cline_one(tmp_path, cline_home, monkeypatch) -> None:
    workspace = tmp_path / "ws"
    monkeypatch.setenv("CODE_AI_WORKFLOWS_DIR", str(tmp_path / "own-workflows"))
    _write(tmp_path / "own-workflows" / "deploy.md", "Mine.")
    _write(workspace / ".clinerules" / "workflows" / "deploy.md", "Theirs.")

    service = WorkflowService(sources=workflow_sources(workspace))
    records = service.load()

    assert [(record.body, record.origin) for record in records] == [("Mine.", "code-ai")]
