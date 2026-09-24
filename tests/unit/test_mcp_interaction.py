"""What a server may ask of the session: a model completion, or the user."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from code_ai.app.commands import SessionCommands
from code_ai.config.models import AppConfig
from code_ai.core.approval import ApprovalDecision, ApprovalRequest
from code_ai.events.bus import AsyncEventBus
from code_ai.events.models import EventEnvelope
from code_ai.mcp import manager as manager_module
from code_ai.mcp.elicitation import ElicitationBroker, coerce, fields_of
from code_ai.mcp.jsonrpc import McpError
from code_ai.mcp.manager import McpManager
from code_ai.mcp.sampling import USER_REJECTED, SamplingBroker
from code_ai.providers.models import FinishReason, ModelRequest, ModelResponse
from code_ai.tools.registry import ToolRegistry
from code_ai.ui.terminal.view_models import TerminalViewModel

SERVER = Path(__file__).with_name("mcp_fake_server.py")

SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "title": "Name", "minLength": 2},
        "age": {"type": "integer", "minimum": 0},
        "plan": {"type": "string", "enum": ["free", "pro"]},
        "newsletter": {"type": "boolean"},
    },
    "required": ["name"],
}


def _config(tmp_path: Path, **values: Any) -> AppConfig:
    return AppConfig.from_mapping(
        {"api_mode": "ollama", "workspace": str(tmp_path), "model": "fake", **values}
    )


class _Provider:
    def __init__(self, text: str = "four") -> None:
        self.text = text
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return ModelResponse(text=self.text, finish_reason=FinishReason.STOP)


class _Gateway:
    def __init__(self, *decisions: ApprovalDecision) -> None:
        self.decisions = list(decisions)
        self.requests: list[ApprovalRequest] = []

    async def request_approval(self, request: ApprovalRequest) -> ApprovalDecision:
        self.requests.append(request)
        return self.decisions.pop(0)


def _sampling(
    tmp_path: Path, gateway: _Gateway, mode: str = "ask"
) -> tuple[SamplingBroker, _Provider, list[EventEnvelope]]:
    bus = AsyncEventBus()
    events: list[EventEnvelope] = []
    bus.subscribe(events.append)
    provider = _Provider()
    broker = SamplingBroker(
        config=_config(tmp_path, permission_mode=mode),
        provider=provider,
        event_bus=bus,
        gateway=lambda: gateway,
    )
    return broker, provider, events


def _ask(text: str = "2+2?") -> dict[str, Any]:
    return {
        "messages": [{"role": "user", "content": {"type": "text", "text": text}}],
        "systemPrompt": "Answer in one word.",
        "maxTokens": 100000,
    }


# -- sampling ------------------------------------------------------------------


async def test_sampling_asks_first_then_answers_with_the_session_model(tmp_path) -> None:
    gateway = _Gateway(ApprovalDecision.allow_once(), ApprovalDecision.allow_once())
    broker, provider, events = _sampling(tmp_path, gateway)

    answer = await broker("github", _ask())
    await broker("github", _ask())

    assert answer == {
        "role": "assistant",
        "content": {"type": "text", "text": "four"},
        "model": "fake",
        "stopReason": "endTurn",
    }
    assert len(gateway.requests) == 2, "an allow-once asks again next time"
    card = gateway.requests[0]
    assert card.tool_name == "mcp_sampling (github)"
    assert card.arguments["system_prompt"] == "Answer in one word."
    assert card.arguments["messages"] == [{"role": "user", "text": "2+2?"}]
    request = provider.requests[0]
    assert [m.role for m in request.messages] == ["system", "user"]
    assert request.max_output_tokens == 4096, "a server cannot ask for a book"
    kinds = [e.event_type for e in events]
    assert kinds.count("tool.approval.requested") == 2
    assert "mcp.sampling.completed" in kinds


async def test_allowing_for_the_session_stops_the_questions(tmp_path) -> None:
    gateway = _Gateway(ApprovalDecision.allow_session())
    broker, _, _ = _sampling(tmp_path, gateway)

    await broker("github", _ask())
    await broker("github", _ask())

    assert len(gateway.requests) == 1


async def test_a_refusal_goes_back_to_the_server_as_user_rejected(tmp_path) -> None:
    broker, provider, _ = _sampling(tmp_path, _Gateway(ApprovalDecision.deny("no thanks")))

    with pytest.raises(McpError) as caught:
        await broker("github", _ask())

    assert caught.value.code == USER_REJECTED
    assert "no thanks" in caught.value.message
    assert provider.requests == []


async def test_auto_mode_samples_without_asking(tmp_path) -> None:
    gateway = _Gateway()
    broker, provider, _ = _sampling(tmp_path, gateway, mode="auto")

    await broker("github", _ask())

    assert gateway.requests == []
    assert len(provider.requests) == 1


async def test_images_in_a_sampling_request_reach_the_model(tmp_path) -> None:
    broker, provider, _ = _sampling(tmp_path, _Gateway(), mode="bypass")
    params = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "what is this?"},
                    {"type": "image", "data": "AAA", "mimeType": "image/jpeg"},
                ],
            }
        ]
    }

    await broker("s", params)

    message = provider.requests[0].messages[0]
    assert message.content == "what is this?"
    assert message.images[0].media_type == "image/jpeg"
    with pytest.raises(McpError):
        await broker("s", {"messages": []})


# -- elicitation ---------------------------------------------------------------


def test_the_schema_becomes_form_fields() -> None:
    fields = fields_of(SCHEMA)

    assert [f["name"] for f in fields] == ["name", "age", "plan", "newsletter"]
    assert fields[0]["required"] is True
    assert fields[0]["title"] == "Name"
    assert fields[2]["enum"] == ["free", "pro"]
    assert fields[1]["type"] == "integer"


def test_answers_are_typed_and_checked() -> None:
    fields = fields_of(SCHEMA)

    assert coerce(fields, {"name": "Rafa", "age": "30", "plan": "pro", "newsletter": "sim"}) == {
        "name": "Rafa",
        "age": 30,
        "plan": "pro",
        "newsletter": True,
    }
    assert coerce(fields, {"name": "Rafa", "age": ""}) == {"name": "Rafa"}
    for bad, why in (
        ({}, "required"),
        ({"name": "R"}, "characters"),
        ({"name": "Rafa", "age": "3.5"}, "integer"),
        ({"name": "Rafa", "age": "-1"}, "at least"),
        ({"name": "Rafa", "plan": "gold"}, "one of"),
        ({"name": "Rafa", "newsletter": "maybe"}, "boolean"),
    ):
        with pytest.raises(ValueError, match=why):
            coerce(fields, bad)


async def test_a_question_waits_for_the_answer_and_keeps_waiting_on_a_bad_one() -> None:
    bus = AsyncEventBus()
    events: list[EventEnvelope] = []
    bus.subscribe(events.append)
    broker = ElicitationBroker(event_bus=bus, interactive=lambda: True)

    asked = asyncio.create_task(
        broker("github", {"message": "Who are you?", "requestedSchema": SCHEMA})
    )
    await asyncio.sleep(0.01)
    (request,) = broker.pending()

    assert request["message"] == "Who are you?"
    assert "required" in broker.answer(request["id"], "accept", {"age": 3})
    assert broker.pending(), "a bad answer leaves the question open"
    assert broker.answer(request["id"], "accept", {"name": "Rafa", "age": "40"}) == ""
    assert await asyncio.wait_for(asked, 2) == {
        "action": "accept",
        "content": {"name": "Rafa", "age": 40},
    }
    assert broker.pending() == []
    kinds = [e.event_type for e in events]
    assert kinds == ["mcp.elicitation.requested", "mcp.elicitation.resolved"]
    assert broker.answer(request["id"], "accept", {}) != "", "a settled question stays settled"


async def test_nobody_watching_declines_and_a_timeout_cancels() -> None:
    bus = AsyncEventBus()
    unattended = ElicitationBroker(event_bus=bus, interactive=lambda: False)
    impatient = ElicitationBroker(event_bus=bus, interactive=lambda: True, timeout_s=0.05)

    assert await unattended("s", {"message": "?"}) == {"action": "decline"}
    assert await impatient("s", {"message": "?"}) == {"action": "cancel"}


async def test_closing_the_session_cancels_open_questions() -> None:
    broker = ElicitationBroker(event_bus=AsyncEventBus(), interactive=lambda: True)
    asked = asyncio.create_task(broker("s", {"message": "?"}))
    await asyncio.sleep(0.01)

    broker.close()

    assert await asyncio.wait_for(asked, 2) == {"action": "cancel"}


async def test_mcp_answer_reads_pairs_a_bare_value_or_decline() -> None:
    broker = ElicitationBroker(event_bus=AsyncEventBus(), interactive=lambda: True)
    app = SimpleNamespace(mcp=SimpleNamespace(elicitation=broker))
    commands = SessionCommands(app)  # type: ignore[arg-type]

    assert not commands.mcp_answer("x=1").ok, "nothing is waiting yet"

    first = asyncio.create_task(broker("github", {"message": "?", "requestedSchema": SCHEMA}))
    await asyncio.sleep(0.01)
    assert "name=..." in commands.mcp_answer("just words").message
    assert commands.mcp_answer('name="Rafael A" plan=free').ok
    assert (await first)["content"] == {"name": "Rafael A", "plan": "free"}

    one_field = {"type": "object", "properties": {"city": {"type": "string"}}}
    second = asyncio.create_task(broker("maps", {"message": "?", "requestedSchema": one_field}))
    await asyncio.sleep(0.01)
    assert commands.mcp_answer("Rio de Janeiro").ok
    assert (await second)["content"] == {"city": "Rio de Janeiro"}

    third = asyncio.create_task(broker("maps", {"message": "?"}))
    await asyncio.sleep(0.01)
    assert commands.mcp_answer("decline").message == "maps: declined."
    assert await third == {"action": "decline"}


# -- through a real server -------------------------------------------------------


async def test_a_server_samples_and_asks_through_the_manager(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(manager_module, "_BACKOFF_S", 0.05)
    config = _config(
        tmp_path,
        permission_mode="auto",
        mcp_servers={"fake": {"command": sys.executable, "args": [str(SERVER)]}},
    )
    bus = AsyncEventBus()
    manager = McpManager(
        config=config, registry=ToolRegistry(), event_bus=bus, client_version="test"
    )
    manager.sampling_handler = SamplingBroker(
        config=config, provider=_Provider("Paris"), event_bus=bus, gateway=lambda: _Gateway()
    )
    broker = ElicitationBroker(event_bus=bus, interactive=lambda: True)
    manager.elicitation = broker
    await manager.start()
    await manager.wait_ready(20)
    try:
        sampled = await manager.call_tool("fake", "ask_model", {"prompt": "Capital of France?"})
        asked = asyncio.create_task(manager.call_tool("fake", "ask_user", {}))
        for _ in range(100):
            if broker.pending():
                break
            await asyncio.sleep(0.02)
        (request,) = broker.pending()
        assert request["server"] == "fake"
        assert broker.answer(request["id"], "accept", {"name": "Rafa", "plan": "pro"}) == ""
        answered = await asyncio.wait_for(asked, 10)
    finally:
        await manager.close()

    assert sampled["content"][0]["text"] == "Paris"
    assert json.loads(answered["content"][0]["text"]) == {
        "action": "accept",
        "content": {"name": "Rafa", "plan": "pro"},
    }


# -- the terminal ------------------------------------------------------------------


def _event(kind: str, payload: dict[str, Any]) -> EventEnvelope:
    return EventEnvelope.create(event_type=kind, session_id="s", sequence=0, payload=payload)


def test_the_terminal_shows_servers_progress_and_questions() -> None:
    vm = TerminalViewModel()

    vm.apply(_event("mcp.server.status", {"name": "github", "status": "connecting"}))
    vm.apply(
        _event("mcp.server.status", {"name": "github", "status": "connected", "tools": [1, 2]})
    )
    vm.apply(
        _event("mcp.server.status", {"name": "github", "status": "connected", "tools": [1, 2]})
    )
    vm.apply(
        _event(
            "mcp.server.status",
            {"name": "repo", "status": "needs_approval", "source_path": ".mcp.json"},
        )
    )
    vm.apply(_event("mcp.server.log", {"server": "github", "level": "info", "message": "quiet"}))
    vm.apply(
        _event("mcp.server.log", {"server": "github", "level": "error", "message": "rate limit"})
    )
    for step in (1, 2):
        vm.apply(
            _event(
                "mcp.tool.progress",
                {"name": "mcp__github__search", "progress": step, "total": 4, "message": "pages"},
            )
        )
    vm.apply(
        _event(
            "mcp.elicitation.requested",
            {"id": "a", "server": "github", "message": "Which org?", "fields": fields_of(SCHEMA)},
        )
    )
    vm.apply(
        _event("mcp.elicitation.resolved", {"id": "a", "server": "github", "action": "decline"})
    )

    lines = vm.conversation
    assert lines[0] == "mcp> github: connected (2 tools)"
    assert lines[1] == "mcp> repo (from .mcp.json) is waiting: /mcp approve repo"
    assert lines[2] == "mcp> [github] rate limit"
    assert lines[3] == "tool~ mcp__github__search: 2/4 pages", "progress updates in place"
    assert lines[4].startswith("mcp> github asks: Which org?")
    assert "plan (one of free, pro)" in lines[4]
    assert "/mcp answer name=... age=... plan=... newsletter=..." in lines[4]
    assert lines[5] == "mcp> github: declined"
    assert len(lines) == 6
