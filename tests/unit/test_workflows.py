from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from code_ai.config.models import AppConfig
from code_ai.core.errors import ToolExecutionError
from code_ai.core.workflows import (
    WorkflowService,
    WorkflowSource,
    native_workflow_sources,
    normalize_workflow_name,
    render_workflow_invocation,
)
from code_ai.events.bus import AsyncEventBus
from code_ai.prompts import build_system_prompt
from code_ai.tools.base import ToolContext
from code_ai.tools.workflows import UseWorkflowTool
from code_ai.util.paths import WorkspacePolicy


def _service(root: Path, *, scope: str = "project", origin: str = "code-ai") -> WorkflowService:
    return WorkflowService(sources=[WorkflowSource(root=root, scope=scope, origin=origin)])


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def make_context(tmp_path, *, workflows=None) -> ToolContext:
    config = AppConfig.from_mapping({"api_mode": "ollama", "workspace": str(tmp_path)})
    return ToolContext(
        config=config,
        workspace=WorkspacePolicy.from_path(tmp_path),
        event_bus=AsyncEventBus(session_id="session"),
        cancel_event=asyncio.Event(),
        workflows=workflows,
    )


def test_no_workflows_renders_empty_catalog(tmp_path) -> None:
    service = _service(tmp_path / "missing")
    assert service.load() == []
    assert service.render_for_prompt() == ""


def test_catalog_lists_names_and_descriptions_without_bodies(tmp_path) -> None:
    root = tmp_path / "workflows"
    _write(
        root / "release.md",
        "---\nname: release\ndescription: Cut a release.\n---\n\n1. Bump the version",
    )
    _write(root / "triage.md", "Triage an incoming bug report.\n\n1. Ask for repro")

    catalog = _service(root).render_for_prompt()

    assert "# Available workflows" in catalog
    assert "use_workflow" in catalog
    assert "- release: Cut a release." in catalog
    assert "- triage: Triage an incoming bug report." in catalog
    # Steps stay on disk and load on demand.
    assert "Bump the version" not in catalog


def test_frontmatter_name_wins_over_filename(tmp_path) -> None:
    root = tmp_path / "workflows"
    _write(root / "01-release.md", "---\nname: release\n---\n\nSteps.")

    assert [record.name for record in _service(root).load()] == ["release"]


def test_find_tolerates_slash_suffix_and_casing(tmp_path) -> None:
    root = tmp_path / "workflows"
    _write(root / "Deploy.md", "Ship it.")

    service = _service(root)
    for spelling in ("Deploy", "deploy", "/deploy", "deploy.md", "/Deploy.md "):
        record = service.find(spelling)
        assert record is not None, spelling
        assert record.name == "Deploy"
    assert service.find("nope") is None
    assert service.find("") is None


def test_empty_and_hidden_files_are_skipped(tmp_path) -> None:
    root = tmp_path / "workflows"
    _write(root / "blank.md", "---\nname: blank\n---\n\n")
    _write(root / ".hidden.md", "Secret steps.")
    _write(root / "notes.txt", "Not a workflow.")
    _write(root / "real.md", "Real steps.")

    assert [record.name for record in _service(root).load()] == ["real"]


def test_normalize_workflow_name() -> None:
    assert normalize_workflow_name("/Deploy.md") == "deploy"
    assert normalize_workflow_name(" triage ") == "triage"
    assert normalize_workflow_name("") == ""


def test_native_sources_cover_global_and_project(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CODE_AI_WORKFLOWS_DIR", str(tmp_path / "global"))
    sources = native_workflow_sources(tmp_path / "ws")

    assert [source.scope for source in sources] == ["global", "project"]
    assert sources[0].root == tmp_path / "global"
    assert sources[1].root.parts[-2:] == (".code-ai", "workflows")
    assert all(source.origin == "code-ai" for source in sources)


def test_render_workflow_invocation_carries_steps_and_extra_input(tmp_path) -> None:
    root = tmp_path / "workflows"
    _write(root / "release.md", "1. Bump the version\n2. Tag it")
    record = _service(root).find("release")

    prompt = render_workflow_invocation(record, "1.4.0")

    assert 'Run the "release" workflow' in prompt
    assert "1. Bump the version" in prompt
    assert "Additional input for this run: 1.4.0" in prompt

    without_argument = render_workflow_invocation(record)
    assert "Additional input" not in without_argument


async def test_use_workflow_lists_then_loads(tmp_path) -> None:
    root = tmp_path / "workflows"
    _write(root / "release.md", "---\nname: release\ndescription: Cut a release.\n---\n\nSteps.")
    context = make_context(tmp_path, workflows=_service(root))
    tool = UseWorkflowTool()

    listing = await tool.execute({"name": None}, context)
    assert listing["count"] == 1
    assert listing["workflows"][0]["name"] == "release"
    assert str(root) in listing["workflow_dirs"]

    loaded = await tool.execute({"name": "release.md"}, context)
    assert loaded["name"] == "release"
    assert loaded["description"] == "Cut a release."
    assert loaded["steps"] == "Steps."
    assert loaded["scope"] == "project"


async def test_use_workflow_unknown_name_raises(tmp_path) -> None:
    context = make_context(tmp_path, workflows=_service(tmp_path / "workflows"))
    with pytest.raises(ToolExecutionError):
        await UseWorkflowTool().execute({"name": "missing"}, context)


async def test_use_workflow_falls_back_to_native_dirs(tmp_path, monkeypatch) -> None:
    """An unwired context still finds Code-AI's own workflows."""

    monkeypatch.setenv("CODE_AI_WORKFLOWS_DIR", str(tmp_path / "global"))
    _write(tmp_path / "global" / "smoke.md", "Run the smoke test.")
    context = make_context(tmp_path)

    loaded = await UseWorkflowTool().execute({"name": "smoke"}, context)

    assert loaded["steps"] == "Run the smoke test."
    assert loaded["scope"] == "global"


def test_prompt_injects_workflow_catalog() -> None:
    catalog = "# Available workflows\n\n- release: Cut a release."
    prompt = build_system_prompt(
        workspace=Path("/tmp/ws"), language="en", workflows=catalog
    )

    assert "# Available workflows" in prompt
    assert "- release: Cut a release." in prompt
    assert "use_workflow" in prompt


def test_prompt_without_workflows_has_no_catalog_heading() -> None:
    prompt = build_system_prompt(workspace=Path("/tmp/ws"), language="en")

    assert "# Available workflows" not in prompt
    # The standing guidance still tells the model how to reach a workflow.
    assert "use_workflow" in prompt
