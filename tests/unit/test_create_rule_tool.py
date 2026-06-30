from __future__ import annotations

import asyncio

import pytest

from code_ai.config.defaults import RULES_DIR_ENV, project_rules_dir
from code_ai.config.models import AppConfig
from code_ai.core.errors import ToolArgumentError, ToolExecutionError
from code_ai.core.rules import RulesService
from code_ai.events.bus import AsyncEventBus
from code_ai.tools.base import ToolContext
from code_ai.tools.rules import CreateRuleTool
from code_ai.util.paths import WorkspacePolicy


def make_context(workspace) -> ToolContext:
    config = AppConfig.from_mapping({"api_mode": "ollama", "workspace": str(workspace)})
    return ToolContext(
        config=config,
        workspace=WorkspacePolicy.from_path(workspace),
        event_bus=AsyncEventBus(session_id="session"),
        cancel_event=asyncio.Event(),
    )


async def test_create_project_rule_writes_into_workspace(tmp_path) -> None:
    context = make_context(tmp_path)
    result = await CreateRuleTool().execute(
        {"name": "Run Tests", "description": "verify", "content": "Sempre rode os testes."},
        context,
    )

    assert result["scope"] == "project"
    assert result["name"] == "run-tests"
    assert (project_rules_dir(tmp_path) / "run-tests.md").is_file()

    # The freshly written rule is discoverable by the loader.
    service = RulesService(
        global_dir=tmp_path / "none", project_dir=project_rules_dir(tmp_path)
    )
    assert any(record.name == "run-tests" for record in service.load())


async def test_create_global_rule_writes_into_global_dir(tmp_path, monkeypatch) -> None:
    global_dir = tmp_path / "global-rules"
    monkeypatch.setenv(RULES_DIR_ENV, str(global_dir))
    context = make_context(tmp_path)

    result = await CreateRuleTool().execute(
        {
            "name": "ptbr",
            "description": "lang",
            "content": "Responda em pt-BR.",
            "scope": "global",
        },
        context,
    )

    assert result["scope"] == "global"
    assert (global_dir / "ptbr.md").is_file()


async def test_create_rule_refuses_overwrite_without_flag(tmp_path) -> None:
    context = make_context(tmp_path)
    tool = CreateRuleTool()
    args = {"name": "dup", "description": "d", "content": "c"}
    await tool.execute(args, context)

    with pytest.raises(ToolExecutionError):
        await tool.execute(args, context)

    overwritten = await tool.execute({**args, "overwrite": True}, context)
    assert overwritten["overwritten"] is True


async def test_create_rule_validates_scope(tmp_path) -> None:
    context = make_context(tmp_path)
    with pytest.raises(ToolArgumentError):
        await CreateRuleTool().execute(
            {"name": "x", "description": "d", "content": "c", "scope": "team"}, context
        )
