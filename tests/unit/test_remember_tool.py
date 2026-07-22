from __future__ import annotations

import asyncio

from code_ai.config.models import AppConfig
from code_ai.core.memory import MemoryService, MemoryStore
from code_ai.events.bus import AsyncEventBus
from code_ai.tools.base import ToolContext
from code_ai.tools.memory import RememberTool
from code_ai.util.paths import WorkspacePolicy


def make_context(tmp_path) -> ToolContext:
    config = AppConfig.from_mapping({"api_mode": "ollama", "workspace": str(tmp_path)})
    memory = MemoryService(
        global_store=MemoryStore(tmp_path / "global"),
        project_store=MemoryStore(tmp_path / "project"),
    )
    return ToolContext(
        config=config,
        workspace=WorkspacePolicy.from_path(tmp_path),
        event_bus=AsyncEventBus(session_id="session"),
        cancel_event=asyncio.Event(),
        memory=memory,
    )


async def test_remember_saves_fact(tmp_path) -> None:
    context = make_context(tmp_path)
    result = await RememberTool().execute(
        {"kind": "feedback", "content": "Always run pytest -q."}, context
    )

    assert result == {"remembered": "Always run pytest -q.", "kind": "feedback"}
    assert "Always run pytest -q." in context.memory.render_for_prompt()


async def test_remember_replaces_retires_the_superseded_fact(tmp_path) -> None:
    context = make_context(tmp_path)
    tool = RememberTool()
    await tool.execute({"kind": "project", "content": "The stack is Flask."}, context)

    result = await tool.execute(
        {
            "kind": "project",
            "content": "The stack is FastAPI.",
            "replaces": "The stack is Flask.",
        },
        context,
    )

    assert result["replaced_previous"] is True
    rendered = context.memory.render_for_prompt()
    assert "FastAPI" in rendered
    assert "Flask" not in rendered


async def test_remember_replaces_own_content_is_harmless(tmp_path) -> None:
    # A model restating the fact it is saving must never delete it.
    context = make_context(tmp_path)
    result = await RememberTool().execute(
        {
            "kind": "feedback",
            "content": "Use ruff for linting.",
            "replaces": "Use ruff for linting.",
        },
        context,
    )

    assert result["replaced_previous"] is False
    assert "Use ruff for linting." in context.memory.render_for_prompt()


async def test_remember_replaces_missing_fact_reports_false(tmp_path) -> None:
    context = make_context(tmp_path)
    result = await RememberTool().execute(
        {
            "kind": "project",
            "content": "Tests live under tests/unit.",
            "replaces": "Some fact that was never saved.",
        },
        context,
    )

    assert result["replaced_previous"] is False
    assert "Tests live under tests/unit." in context.memory.render_for_prompt()
