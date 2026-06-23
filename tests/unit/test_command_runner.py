from __future__ import annotations

import asyncio
import shlex
import sys

import pytest

from code_ai.config.models import AppConfig
from code_ai.core.errors import ToolArgumentError, ToolExecutionError
from code_ai.events.bus import AsyncEventBus
from code_ai.tools.base import ToolContext
from code_ai.tools.process import ExecuteCommandTool
from code_ai.util.paths import WorkspacePolicy


def make_context(tmp_path) -> ToolContext:
    config = AppConfig.from_mapping({"api_mode": "ollama", "workspace": str(tmp_path)})
    return ToolContext(
        config=config,
        workspace=WorkspacePolicy.from_path(tmp_path),
        event_bus=AsyncEventBus(session_id="session"),
        cancel_event=asyncio.Event(),
    )


def test_execute_command_schema_exposes_simple_command_string() -> None:
    schema = ExecuteCommandTool.input_schema

    assert schema["properties"]["command"]["type"] == "string"
    assert schema["properties"]["command"]["description"]
    # strict-mode: every declared property is required; optionals are nullable.
    assert set(schema["required"]) == set(schema["properties"])
    assert schema["properties"]["cwd"]["type"] == ["string", "null"]
    assert schema["properties"]["timeout"]["type"] == ["number", "null"]
    assert "argv" not in schema["properties"]
    assert "env" not in schema["properties"]
    assert "shell" not in schema["properties"]


async def test_execute_command_separates_stdout_stderr(tmp_path) -> None:
    context = make_context(tmp_path)
    tool = ExecuteCommandTool()
    script = "import sys; print('out'); print('err', file=sys.stderr)"
    result = await tool.execute(
        {"command": f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}"},
        context,
    )
    assert result["exit_code"] == 0
    assert "out" in result["stdout"]
    assert "err" in result["stderr"]


async def test_execute_command_defaults_to_workspace(tmp_path) -> None:
    context = make_context(tmp_path)
    tool = ExecuteCommandTool()
    result = await tool.execute({"command": "pwd"}, context)
    assert result["cwd"] == str(tmp_path)
    assert result["stdout"].strip() == str(tmp_path)


async def test_execute_command_keeps_legacy_argv_execution(tmp_path) -> None:
    context = make_context(tmp_path)
    tool = ExecuteCommandTool()

    result = await tool.execute({"argv": ["pwd"]}, context)

    assert result["exit_code"] == 0
    assert result["stdout"].strip() == str(tmp_path)


async def test_execute_command_rejects_empty_command(tmp_path) -> None:
    context = make_context(tmp_path)
    tool = ExecuteCommandTool()

    with pytest.raises(ToolArgumentError, match="command"):
        await tool.execute({"command": ""}, context)


async def test_execute_command_rejects_shell_mode(tmp_path) -> None:
    context = make_context(tmp_path)
    tool = ExecuteCommandTool()

    with pytest.raises(ToolArgumentError, match="shell"):
        await tool.execute({"command": "pwd", "shell": True}, context)


async def test_execute_command_does_not_inherit_api_key(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "secret-value")
    context = make_context(tmp_path)
    tool = ExecuteCommandTool()
    script = "import os; print(os.environ.get('OPENAI_API_KEY', 'missing'))"
    result = await tool.execute(
        {"command": f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}"},
        context,
    )
    assert "secret-value" not in result["stdout"]
    assert "missing" in result["stdout"]


async def test_execute_command_timeout(tmp_path) -> None:
    context = make_context(tmp_path)
    tool = ExecuteCommandTool()
    with pytest.raises(ToolExecutionError):
        await tool.execute(
            {
                "command": (
                    f"{shlex.quote(sys.executable)} -c "
                    f"{shlex.quote('import time; time.sleep(2)')}"
                ),
                "timeout": 0.1,
            },
            context,
        )


async def test_execute_command_missing_binary_is_tool_error(tmp_path) -> None:
    context = make_context(tmp_path)
    tool = ExecuteCommandTool()
    with pytest.raises(ToolExecutionError, match="failed to start"):
        await tool.execute({"command": "definitely-missing-code-ai-binary"}, context)
