"""/mcp, MCP prompts as slash commands, and switching MCP from the settings panel."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from code_ai.app.commands import SessionCommands, prompt_arguments
from code_ai.app.slash_catalog import catalog_payload
from code_ai.bootstrap import build_application
from code_ai.bridge.server import BridgeServer
from code_ai.config import loader as config_loader
from code_ai.config.models import AppConfig
from code_ai.mcp import manager as manager_module
from code_ai.providers.models import ModelResponse, ProviderCapabilities, ProviderEvent

SERVER = Path(__file__).with_name("mcp_fake_server.py")


def _server(*flags: str) -> dict[str, Any]:
    return {"command": sys.executable, "args": [str(SERVER), *flags]}


class _StubProvider:
    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(streaming=True, tool_calling=True)

    async def stream(self, request):  # noqa: ANN001
        yield ProviderEvent(kind="completed", response=ModelResponse(text=""))

    async def complete(self, request):  # noqa: ANN001
        return ModelResponse(text="")

    async def close(self) -> None:
        return None


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(manager_module, "_BACKOFF_S", 0.05)
    # The panel's toggle persists; it must never reach the real config.json.
    monkeypatch.setattr(config_loader, "default_config_path", lambda: tmp_path / "config.json")


async def _settled(app: Any, timeout: float = 20.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while any(state.status == "connecting" for state in app.mcp.servers.values()):
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("the MCP servers never settled")
        await asyncio.sleep(0.05)


@pytest.fixture
async def app(tmp_path) -> AsyncIterator[Any]:
    workspace = tmp_path / "work"
    workspace.mkdir()
    config = AppConfig.from_mapping(
        {
            "api_mode": "ollama",
            "workspace": str(workspace),
            "model": "fake",
            "index": {"enabled": False},
            "memory": {"reflection_enabled": False},
            "mcp_servers": {"fake": _server()},
        }
    )
    application = build_application(config=config, provider=_StubProvider())
    await application.start()
    await _settled(application)
    yield application
    await application.close()


def test_prompt_arguments_by_name_by_order_or_whole() -> None:
    two = [{"name": "path", "required": True}, {"name": "focus"}]
    one = [{"name": "topic", "required": True}]

    assert prompt_arguments(two, "focus=tests path=a.py") == {"focus": "tests", "path": "a.py"}
    assert prompt_arguments(two, 'a.py "error handling"') == {
        "path": "a.py",
        "focus": "error handling",
    }
    assert prompt_arguments(one, "the whole sentence here") == {"topic": "the whole sentence here"}
    with pytest.raises(ValueError, match="path"):
        prompt_arguments(two, "")


async def test_mcp_status_lists_each_server(app: Any) -> None:
    result = SessionCommands(app).mcp_status()

    assert result.ok
    assert result.message.startswith("fake: connected (stdio, ")


async def test_mcp_actions_act_on_one_server(app: Any) -> None:
    commands = SessionCommands(app)

    off = await commands.mcp_action("off", "fake")
    assert off.message == "fake: off."
    assert not app.orchestrator.tool_registry.has("mcp__fake__echo")

    on = await commands.mcp_action("on", "fake")
    assert on.ok
    await _settled(app)
    assert app.orchestrator.tool_registry.has("mcp__fake__echo")

    assert not (await commands.mcp_action("reconnect", "")).ok
    assert not (await commands.mcp_action("explode", "fake")).ok
    assert not (await commands.mcp_action("reconnect", "ghost")).ok


async def test_a_servers_prompts_become_slash_commands(app: Any) -> None:
    commands = SessionCommands(app)
    prompts = commands.mcp_prompts()

    assert prompts[0]["command"] == "/mcp:fake:review"
    assert "<path>" in prompts[0]["description"]
    catalog = catalog_payload(mcp_prompts=prompts)
    entry = next(item for item in catalog if item["command"] == "/mcp:fake:review")
    assert entry["kind"] == "mcp"
    assert any(item["command"] == "/mcp" for item in catalog)

    resolved = await commands.resolve_mcp_prompt("/mcp:fake:review", "src/app.py")
    assert resolved.ok
    assert "Review src/app.py closely." in resolved.data["prompt"]
    missing = await commands.resolve_mcp_prompt("/mcp:fake:review", "")
    assert not missing.ok
    assert "path" in missing.message
    assert not (await commands.resolve_mcp_prompt("/mcp:fake:nope", "")).ok


async def test_the_bridge_runs_an_mcp_prompt_as_a_turn(app: Any, monkeypatch) -> None:
    submitted: list[str] = []

    async def submit(text: str, **_: Any) -> None:
        submitted.append(text)

    monkeypatch.setattr(app, "submit_user_message", submit)
    bridge = BridgeServer(app, stdin=sys.stdin, stdout=sys.stdout)

    catalog = await bridge._h_list_slash_commands({})
    result = await bridge._h_run_asset({"token": "/mcp:fake:review", "argument": "a.py"})
    await asyncio.sleep(0.05)

    assert any(item["command"] == "/mcp:fake:review" for item in catalog["commands"])
    assert result["ok"] is True
    assert result["kind"] == "mcp"
    assert submitted and "Review a.py closely." in submitted[0]


async def test_the_panel_switches_mcp_off_and_on(app: Any) -> None:
    off = await app.update_settings({"mcp_enabled": False})

    assert off["errors"] == {}
    assert off["settings"]["mcp_enabled"] is False
    assert app.mcp.servers["fake"].status == "disabled"
    assert not app.orchestrator.tool_registry.has("mcp__fake__echo")
    # While MCP is off, a server's own switch cannot start it.
    await app.mcp.reconnect("fake")
    assert app.mcp.servers["fake"].status == "disabled"

    on = await app.update_settings({"mcp_enabled": True})
    await _settled(app)

    assert on["settings"]["mcp_enabled"] is True
    assert app.mcp.servers["fake"].status == "connected"
    assert app.orchestrator.tool_registry.has("mcp__fake__echo")


async def test_saving_unchanged_settings_leaves_the_servers_alone(app: Any) -> None:
    client = app.mcp.servers["fake"].client

    await app.update_settings({"mcp_enabled": True})

    assert app.mcp.servers["fake"].client is client
