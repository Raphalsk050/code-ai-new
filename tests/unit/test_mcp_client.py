"""The MCP client against a real server process (tests/unit/mcp_fake_server.py)."""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from code_ai.mcp.client import ClientHandlers, McpClient
from code_ai.mcp.jsonrpc import McpCancelled, McpError
from code_ai.mcp.transports import StdioTransport, child_environment

SERVER = Path(__file__).with_name("mcp_fake_server.py")


def _transport(*flags: str) -> StdioTransport:
    return StdioTransport(
        command=sys.executable,
        args=[str(SERVER), *flags],
        env=child_environment({}, ssl_verification=False),
        cwd=SERVER.parent,
    )


async def _connect(*flags: str, handlers: ClientHandlers | None = None) -> McpClient:
    client = McpClient(
        _transport(*flags),
        client_name="test",
        client_version="0",
        handlers=handlers,
        request_timeout=10,
    )
    await client.connect(timeout=30)
    return client


@pytest.fixture
async def client() -> AsyncIterator[McpClient]:
    connected = await _connect()
    yield connected
    await connected.close()


async def test_the_handshake_reports_the_server() -> None:
    client = await _connect("--instructions", "Use echo for echoes.")
    try:
        identity = client.identity
        assert identity.name == "fake"
        assert identity.title == "Fake Server"
        assert identity.protocol_version == "2025-06-18"
        assert identity.instructions == "Use echo for echoes."
        assert identity.supports("tools")
        assert identity.supports("resources")
    finally:
        await client.close()


async def test_the_tool_listing_follows_every_page(client: McpClient) -> None:
    names = [tool["name"] for tool in await client.list_tools()]

    assert names[:3] == ["echo", "add", "slow"]
    assert "shout" in names
    assert len(names) == len(set(names))


async def test_a_call_returns_content_and_structured_output(client: McpClient) -> None:
    result = await client.call_tool("add", {"a": 2, "b": 3}, timeout=10)

    assert result["content"][0]["text"] == "5"
    assert result["structuredContent"] == {"sum": 5.0}


async def test_progress_reaches_the_callback(client: McpClient) -> None:
    seen: list[tuple[float, float | None]] = []

    async def progress(done: float, total: float | None, message: str) -> None:
        seen.append((done, total))

    result = await client.call_tool("slow", {"steps": 3}, timeout=10, progress=progress)

    assert result["content"][0]["text"] == "done"
    assert seen[-1] == (3.0, 3.0)


async def test_cancelling_a_call_stops_waiting_and_keeps_the_session(client: McpClient) -> None:
    cancel = asyncio.Event()
    call = asyncio.create_task(client.call_tool("slow", {"steps": 400}, timeout=30, cancel=cancel))
    await asyncio.sleep(0.3)
    cancel.set()

    with pytest.raises(McpCancelled):
        await asyncio.wait_for(call, 5)
    echoed = await client.call_tool("echo", {"text": "still here"}, timeout=10)
    assert echoed["content"][0]["text"] == "still here"


async def test_a_timeout_is_an_error_rather_than_a_hang(client: McpClient) -> None:
    with pytest.raises(McpError, match="no answer"):
        await client.call_tool("slow", {"steps": 400}, timeout=0.3)


async def test_the_server_can_ask_for_our_roots() -> None:
    async def roots() -> list[dict[str, str]]:
        return [{"uri": "file:///work", "name": "work"}]

    client = await _connect(handlers=ClientHandlers(roots=roots))
    try:
        result = await client.call_tool("ask_roots", {}, timeout=10)
    finally:
        await client.close()

    assert json.loads(result["content"][0]["text"]) == [{"uri": "file:///work", "name": "work"}]


async def test_an_unknown_tool_is_the_servers_error(client: McpClient) -> None:
    with pytest.raises(McpError) as caught:
        await client.call_tool("nope", {}, timeout=10)

    assert caught.value.code == -32602


async def test_resources_and_prompts(client: McpClient) -> None:
    resources = await client.list_resources()
    templates = await client.list_resource_templates()
    contents = await client.read_resource("file:///notes.txt")
    prompts = await client.list_prompts()
    prompt = await client.get_prompt("review", {"path": "a.py"})

    assert resources[0]["uri"] == "file:///notes.txt"
    assert templates[0]["uriTemplate"] == "file:///{path}"
    assert contents[0]["text"] == "remember the milk"
    assert prompts[0]["name"] == "review"
    assert prompt["messages"][0]["content"]["text"] == "Review a.py closely."


async def test_a_banner_on_stdout_does_not_break_the_connection() -> None:
    client = await _connect("--banner")
    try:
        echoed = await client.call_tool("echo", {"text": "hi"}, timeout=10)
        assert echoed["content"][0]["text"] == "hi"
        assert "fake server starting up" in client.transport.diagnostics()
    finally:
        await client.close()


async def test_a_crash_fails_the_call_and_ends_the_connection() -> None:
    client = await _connect()
    try:
        with pytest.raises(McpError):
            await client.call_tool("crash", {}, timeout=10)
        reason = await asyncio.wait_for(client.wait_closed(), 5)
    finally:
        await client.close()

    assert "exited with code 3" in reason
    assert client.closed


async def test_a_missing_program_says_so() -> None:
    transport = StdioTransport(
        command="surely-not-an-installed-mcp-server",
        args=[],
        env=child_environment({}, ssl_verification=False),
        cwd=SERVER.parent,
    )
    client = McpClient(transport, client_name="test", client_version="0")

    with pytest.raises(McpError, match="not found on PATH"):
        await client.connect(timeout=5)


def test_a_server_gets_the_network_settings_but_not_our_secrets(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy:8080")

    env = child_environment({"GITHUB_TOKEN": "gh"}, ssl_verification=False)
    checked = child_environment({}, ssl_verification=True)

    assert "OPENAI_API_KEY" not in env
    assert env["HTTPS_PROXY"] == "http://proxy:8080"
    assert env["GITHUB_TOKEN"] == "gh"
    assert env["PATH"]
    # The company network re-signs TLS: npx and pip have to accept that too.
    assert env["NODE_TLS_REJECT_UNAUTHORIZED"] == "0"
    assert "NODE_TLS_REJECT_UNAUTHORIZED" not in checked
