"""The bridge end to end: JSON-RPC lines in, events and results out.

The unit tests drive ``BridgeServer`` with a stub application; this one runs a
real one (real orchestrator, real conversation store, scripted provider) over
the real read loop, so the wire contract the VS Code extension depends on is
exercised the way the extension exercises it - one JSON object per line.
"""

from __future__ import annotations

import asyncio
import io
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from code_ai.bootstrap import build_application
from code_ai.bridge.server import BridgeServer
from code_ai.config.models import AppConfig
from code_ai.providers.models import (
    FinishReason,
    ModelRequest,
    ModelResponse,
    ProviderCapabilities,
    ProviderEvent,
)


class ScriptedProvider:
    """Answers turns with plain text and one-off calls with a canned summary."""

    def __init__(self) -> None:
        self.turn_calls = 0
        self.oneoff_calls = 0
        self.last_request: ModelRequest | None = None

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            streaming=True, tool_calling=True, provider_reported_usage=False
        )

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        self.turn_calls += 1
        self.last_request = request
        text = "feito"
        yield ProviderEvent(kind="text_delta", text_delta=text)
        yield ProviderEvent(
            kind="completed",
            response=ModelResponse(text=text, finish_reason=FinishReason.STOP),
        )

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.oneoff_calls += 1
        self.last_request = request
        return ModelResponse(text="Trocou o ranking do picker e deixou a suíte verde.")

    async def close(self) -> None:
        return None


class _Pipe(io.StringIO):
    """A stdout the test can read back without closing the server."""

    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def write(self, data: str) -> int:  # type: ignore[override]
        if data.strip():
            self.lines.append(data.strip())
        return len(data)

    def flush(self) -> None:  # pragma: no cover - nothing to flush
        return None


def _app(tmp_path: Path, provider: ScriptedProvider):
    config = AppConfig.from_mapping(
        {
            "api_mode": "ollama",
            "workspace": str(tmp_path),
            "model": "fake",
            # One model call per turn keeps the transcript assertions readable.
            "planner": {"enabled": False},
        }
    )
    return build_application(config=config, provider=provider)


def _requests(lines: list[str], request_id: Any) -> list[dict]:
    out = []
    for line in lines:
        message = json.loads(line)
        if message.get("id") == request_id:
            out.append(message)
    return out


def _events(lines: list[str]) -> list[dict]:
    return [
        json.loads(line)["params"]
        for line in lines
        if json.loads(line).get("method") == "event"
    ]


async def _drive(server: BridgeServer, out: _Pipe, calls: list[dict]) -> None:
    """Feed the server whole JSON-RPC lines, as a client pipe would."""
    for call in calls:
        await server._handle_line(json.dumps(call))
        await asyncio.sleep(0)


async def test_a_turn_streams_events_and_persists_the_conversation(tmp_path) -> None:
    provider = ScriptedProvider()
    app = _app(tmp_path, provider)
    out = _Pipe()
    server = BridgeServer(app, stdin=io.StringIO(), stdout=out)
    app.orchestrator.approval_gateway = server._gateway
    app.subscribe(server._forward_event)
    await app.start()

    await _drive(
        server,
        out,
        [
            {"jsonrpc": "2.0", "id": 1, "method": "newConversation",
             "params": {"conversation_id": "c-e2e"}},
            {"jsonrpc": "2.0", "id": 2, "method": "submitUserMessage",
             "params": {"text": "resuma o ranking"}},
        ],
    )
    await asyncio.gather(*server._turn_tasks)

    types = [event["event_type"] for event in _events(out.lines)]
    assert "user.message" in types
    assert "assistant.final" in types
    assert "status.changed" in types

    # The extension's history reads this list, so it must include the thread and
    # the fields the cards render.
    out.lines.clear()
    await _drive(
        server, out, [{"jsonrpc": "2.0", "id": 3, "method": "listConversations", "params": {}}]
    )
    (response,) = _requests(out.lines, 3)
    conversations = response["result"]["conversations"]
    assert conversations, "the turn should have been persisted"
    card = conversations[0]
    assert card["id"] == "c-e2e"
    assert "summary" in card and "total_tokens" in card

    await app.close()


async def test_summarize_writes_the_card_and_skips_an_unchanged_thread(tmp_path) -> None:
    provider = ScriptedProvider()
    app = _app(tmp_path, provider)
    out = _Pipe()
    server = BridgeServer(app, stdin=io.StringIO(), stdout=out)
    app.subscribe(server._forward_event)
    await app.start()

    await _drive(
        server,
        out,
        [
            {"jsonrpc": "2.0", "id": 1, "method": "newConversation",
             "params": {"conversation_id": "c-sum"}},
            {"jsonrpc": "2.0", "id": 2, "method": "submitUserMessage",
             "params": {"text": "arruma o ranking"}},
        ],
    )
    await asyncio.gather(*server._turn_tasks)

    out.lines.clear()
    await _drive(
        server,
        out,
        [{"jsonrpc": "2.0", "id": 3, "method": "summarizeConversation",
          "params": {"conversation_id": "c-sum"}}],
    )
    await asyncio.gather(*server._turn_tasks)
    (first,) = _requests(out.lines, 3)
    assert first["result"]["summarized"] is True
    assert "ranking" in first["result"]["summary"]
    oneoffs = provider.oneoff_calls

    # Nothing happened since, so a second request must not spend a model call.
    out.lines.clear()
    await _drive(
        server,
        out,
        [{"jsonrpc": "2.0", "id": 4, "method": "summarizeConversation",
          "params": {"conversation_id": "c-sum"}}],
    )
    (second,) = _requests(out.lines, 4)
    assert second["result"]["summarized"] is False
    assert second["result"]["reason"] == "current"
    assert provider.oneoff_calls == oneoffs

    out.lines.clear()
    await _drive(
        server, out, [{"jsonrpc": "2.0", "id": 5, "method": "listConversations", "params": {}}]
    )
    (listing,) = _requests(out.lines, 5)
    assert listing["result"]["conversations"][0]["summary"]

    await app.close()


async def test_pasted_images_reach_the_model(tmp_path) -> None:
    provider = ScriptedProvider()
    app = _app(tmp_path, provider)
    out = _Pipe()
    server = BridgeServer(app, stdin=io.StringIO(), stdout=out)
    app.subscribe(server._forward_event)
    await app.start()

    # A 1x1 PNG, the smallest thing a clipboard can hand over.
    png = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM"
        "IQAAAABJRU5ErkJggg=="
    )
    await _drive(
        server,
        out,
        [
            {"jsonrpc": "2.0", "id": 1, "method": "submitUserMessage",
             "params": {
                 "text": "o que tem nessa tela?",
                 "images": [{"data": png, "media_type": "image/png"}, {"data": ""}],
             }},
        ],
    )
    await asyncio.gather(*server._turn_tasks)

    messages = [m for m in app.orchestrator.conversation.messages if m.role == "user"]
    assert messages, "the message should have reached the conversation"
    assert messages[-1].images, "the pasted image should travel with it"
    assert len(messages[-1].images) == 1, "the empty entry should have been dropped"

    await app.close()


async def test_the_catalog_reaches_the_client_with_availability(tmp_path) -> None:
    app = _app(tmp_path, ScriptedProvider())
    out = _Pipe()
    server = BridgeServer(app, stdin=io.StringIO(), stdout=out)
    await app.start()

    await _drive(
        server, out, [{"jsonrpc": "2.0", "id": 1, "method": "listSlashCommands", "params": {}}]
    )
    (response,) = _requests(out.lines, 1)
    commands = {entry["command"]: entry for entry in response["result"]["commands"]}
    assert commands["/goal start"]["available"] is True
    assert commands["/quit"]["available"] is False
    assert all(entry["completion"] for entry in commands.values())

    await app.close()


async def test_terminal_methods_drive_one_shared_session(tmp_path) -> None:
    app = _app(tmp_path, ScriptedProvider())
    out = _Pipe()
    server = BridgeServer(app, stdin=io.StringIO(), stdout=out)
    app.subscribe(server._forward_event)
    await app.start()

    await _drive(
        server, out, [{"jsonrpc": "2.0", "id": 1, "method": "terminalStart", "params": {}}]
    )
    (started,) = _requests(out.lines, 1)
    if not started["result"]["ok"]:
        # No PTY backend on this platform: the contract is still that it says so
        # rather than raising, which is what the panel renders.
        assert started["result"]["message"]
        await app.close()
        return

    out.lines.clear()
    await _drive(
        server,
        out,
        [
            {"jsonrpc": "2.0", "id": 2, "method": "terminalWrite", "params": {"text": "echo oi"}},
            {"jsonrpc": "2.0", "id": 3, "method": "terminalStatus", "params": {}},
            {"jsonrpc": "2.0", "id": 4, "method": "terminalKill", "params": {}},
        ],
    )
    assert _requests(out.lines, 2)[0]["result"]["ok"] is True
    assert _requests(out.lines, 4)[0]["result"]["ok"] is True
    screens = [e for e in _events(out.lines) if e["event_type"] == "terminal.screen.updated"]
    assert screens, "every terminal action publishes the screen every UI reads"
    assert screens[-1]["payload"]["closed"] is True

    await app.close()
