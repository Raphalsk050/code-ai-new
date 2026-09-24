"""The HTTP transports against scripted servers (httpx.MockTransport)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import httpx
import pytest

from code_ai.mcp.client import ClientHandlers, McpClient
from code_ai.mcp.jsonrpc import McpAuthRequired, McpConnectionError, McpError
from code_ai.mcp.sse import SseParser
from code_ai.mcp.transports import SseTransport, StreamableHttpTransport

Handler = Callable[[httpx.Request], Awaitable[httpx.Response]]


class _Streamable(StreamableHttpTransport):
    def __init__(self, handler: Handler) -> None:
        super().__init__(
            "https://mcp.example/mcp", headers={"X-Team": "core"}, verify=False, timeout=5.0
        )
        self._handler = handler

    def _open_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self._handler))


class _Legacy(SseTransport):
    def __init__(self, handler: Handler) -> None:
        super().__init__("https://legacy.example/sse", headers={}, verify=False, timeout=5.0)
        self._handler = handler

    def _open_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self._handler))


def _initialized(message_id: Any, version: str = "2025-06-18") -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": message_id,
        "result": {
            "protocolVersion": version,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "remote", "version": "2"},
        },
    }


def _events(*messages: dict[str, Any]) -> bytes:
    return "".join(f"event: message\ndata: {json.dumps(m)}\n\n" for m in messages).encode()


def _client(transport: Any, **handlers: Any) -> McpClient:
    return McpClient(
        transport,
        client_name="test",
        client_version="0",
        handlers=ClientHandlers(**handlers),
        request_timeout=5,
    )


class _Server:
    """Answers initialize as JSON with a session id, tools/list as an event stream."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.method == "GET":
            return httpx.Response(405)
        if request.method == "DELETE":
            return httpx.Response(200)
        body = json.loads(request.content)
        if "id" not in body:
            return httpx.Response(202)
        if body["method"] == "initialize":
            return httpx.Response(
                200, json=_initialized(body["id"]), headers={"Mcp-Session-Id": "session-7"}
            )
        if body["method"] == "tools/list":
            stream = _events(
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/message",
                    "params": {"level": "info", "data": "listing"},
                },
                {
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {"tools": [{"name": "lookup", "inputSchema": {"type": "object"}}]},
                },
            )
            return httpx.Response(
                200, content=stream, headers={"Content-Type": "text/event-stream"}
            )
        if body["method"] == "tools/call":
            answer = {"content": [{"type": "text", "text": "found"}]}
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": answer})
        if body["method"] == "ping":
            # An event stream that ends without the answer.
            return httpx.Response(
                200,
                content=_events({"jsonrpc": "2.0", "method": "notifications/message"}),
                headers={"Content-Type": "text/event-stream"},
            )
        return httpx.Response(404)


async def test_streamable_http_keeps_the_session_and_reads_event_streams() -> None:
    server = _Server()
    notes: list[str] = []

    async def note(method: str, params: dict[str, Any]) -> None:
        notes.append(method)

    client = _client(_Streamable(server), notification=note)
    await client.connect(timeout=5)
    tools = await client.list_tools()
    result = await client.call_tool("lookup", {}, timeout=5)
    await client.close()

    assert [tool["name"] for tool in tools] == ["lookup"]
    assert result["content"][0]["text"] == "found"
    assert "notifications/message" in notes
    posts = [request for request in server.requests if request.method == "POST"]
    assert "mcp-session-id" not in posts[0].headers
    assert all(request.headers["mcp-session-id"] == "session-7" for request in posts[1:])
    assert "mcp-protocol-version" not in posts[0].headers
    assert posts[-1].headers["mcp-protocol-version"] == "2025-06-18"
    assert all(request.headers["x-team"] == "core" for request in posts)
    assert any(request.method == "DELETE" for request in server.requests)


async def test_a_stream_that_ends_unanswered_fails_the_request_at_once() -> None:
    client = _client(_Streamable(_Server()))
    await client.connect(timeout=5)
    try:
        with pytest.raises(McpError, match="closed the stream"):
            await client.ping(timeout=5)
    finally:
        await client.close()


async def test_a_401_asks_for_sign_in_and_names_the_metadata() -> None:
    metadata = "https://mcp.example/.well-known/oauth-protected-resource"

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401, headers={"WWW-Authenticate": f'Bearer resource_metadata="{metadata}"'}
        )

    client = _client(_Streamable(handler))
    try:
        with pytest.raises(McpAuthRequired) as caught:
            await client.connect(timeout=5)
    finally:
        await client.close()

    assert caught.value.resource_metadata == metadata


async def test_a_session_the_server_forgot_ends_the_connection() -> None:
    initialized = False

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal initialized
        if request.method != "POST":
            return httpx.Response(405)
        body = json.loads(request.content)
        if body.get("method") == "initialize":
            initialized = True
            return httpx.Response(
                200, json=_initialized(body["id"]), headers={"Mcp-Session-Id": "s"}
            )
        if "id" not in body:
            return httpx.Response(202)
        return httpx.Response(404)

    client = _client(_Streamable(handler))
    await client.connect(timeout=5)
    try:
        with pytest.raises(McpConnectionError, match="ended the session"):
            await client.list_tools()
    finally:
        await client.close()
    assert initialized


class _LegacyServer:
    """The 2024-11-05 transport: answers go out on the GET stream, POSTs get 202."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self.post_urls: list[str] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200, headers={"Content-Type": "text/event-stream"}, content=self._stream()
            )
        self.post_urls.append(str(request.url))
        body = json.loads(request.content)
        if "id" in body:
            if body["method"] == "initialize":
                await self.queue.put(_initialized(body["id"], version="2024-11-05"))
            else:
                await self.queue.put({"jsonrpc": "2.0", "id": body["id"], "result": {"tools": []}})
        return httpx.Response(202)

    async def _stream(self) -> AsyncIterator[bytes]:
        yield b"event: endpoint\ndata: /messages?session=abc\n\n"
        while (message := await self.queue.get()) is not None:
            yield f"event: message\ndata: {json.dumps(message)}\n\n".encode()


async def test_the_legacy_sse_transport_posts_where_the_stream_says() -> None:
    server = _LegacyServer()
    client = _client(_Legacy(server))
    await client.connect(timeout=5)
    try:
        tools = await client.list_tools()
    finally:
        await server.queue.put(None)
        await client.close()

    assert client.identity.protocol_version == "2024-11-05"
    assert tools == []
    assert set(server.post_urls) == {"https://legacy.example/messages?session=abc"}


def test_the_sse_parser_joins_data_lines_and_skips_comments() -> None:
    parser = SseParser()
    lines = [": keep-alive", "event: message", "id: 4", 'data: {"a":', "data: 1}", ""]
    events = [event for line in lines if (event := parser.feed(line)) is not None]

    assert len(events) == 1
    assert events[0].event == "message"
    assert events[0].data == '{"a":\n1}'
    assert events[0].id == "4"
    assert parser.feed("") is None
    assert parser.feed("data: x") is None
    assert parser.feed("").event == "message"
