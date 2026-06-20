from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from code_ai.bootstrap import build_application
from code_ai.config.models import AppConfig
from code_ai.providers.models import (
    FinishReason,
    ModelRequest,
    ModelResponse,
    ProviderCapabilities,
    ProviderEvent,
    ToolCall,
)
from code_ai.tools.base import ToolContext
from code_ai.tools.registry import ToolRegistry


class FakeProvider:
    def __init__(self) -> None:
        self.calls = 0

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            streaming=True, tool_calling=True, provider_reported_usage=False
        )

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        self.calls += 1
        assert request.messages[0].role == "system"
        assert "Configured workspace:" in request.messages[0].content
        if self.calls == 1:
            yield ProviderEvent(
                kind="completed",
                response=ModelResponse(
                    tool_calls=[
                        ToolCall(id="call_1", name="read_file", arguments={"path": "note.txt"})
                    ],
                    finish_reason=FinishReason.TOOL_CALLS,
                ),
            )
            return
        assert any(
            message.role == "tool" and "hello" in message.content for message in request.messages
        )
        yield ProviderEvent(kind="text_delta", text_delta="done")
        yield ProviderEvent(
            kind="completed",
            response=ModelResponse(text="done", finish_reason=FinishReason.STOP),
        )

    async def complete(self, request: ModelRequest) -> ModelResponse:
        text = ""
        completed = None
        async for event in self.stream(request):
            if event.kind == "text_delta":
                text += event.text_delta
            elif event.response:
                completed = event.response
        return completed or ModelResponse(text=text)

    async def close(self) -> None:
        return None


async def test_agent_tool_loop_completes_turn(tmp_path) -> None:
    (tmp_path / "note.txt").write_text("hello\n", encoding="utf-8")
    config = AppConfig.from_mapping(
        {"api_mode": "ollama", "workspace": str(tmp_path), "model": "fake"}
    )
    provider = FakeProvider()
    app = build_application(config=config, provider=provider)
    events = []
    app.subscribe(lambda event: events.append(event.event_type))

    await app.start()
    result = await app.submit_user_message("read the note")
    await app.close()

    assert result.text == "done"
    assert provider.calls == 2
    assert "tool.call.completed" in events
    assert "turn.completed" in events


class FakeWebSearchTool:
    name = "web_search"
    description = "Search the public web for current facts."
    input_schema = {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
        "additionalProperties": True,
    }

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        self.calls.append(dict(arguments))
        return {
            "query": arguments["query"],
            "results": [
                {
                    "title": "FIFA World Cup 2026 matches today",
                    "url": "https://www.fifa.com/en/tournaments/mens/worldcup",
                    "snippet": "Official FIFA match schedule for today.",
                    "source": "test",
                }
            ],
        }


class FakeCurrentAnswerProvider:
    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            streaming=True, tool_calling=True, provider_reported_usage=False
        )

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        self.requests.append(request)
        assert any(
            message.role == "user" and "Host-executed web_search" in message.content
            for message in request.messages
        )
        yield ProviderEvent(kind="text_delta", text_delta="grounded answer")
        yield ProviderEvent(
            kind="completed",
            response=ModelResponse(text="grounded answer", finish_reason=FinishReason.STOP),
        )

    async def complete(self, request: ModelRequest) -> ModelResponse:
        async for event in self.stream(request):
            if event.response:
                return event.response
        return ModelResponse()

    async def close(self) -> None:
        return None


async def test_current_question_runs_web_search_before_model(tmp_path) -> None:
    config = AppConfig.from_mapping(
        {"api_mode": "ollama", "workspace": str(tmp_path), "model": "fake"}
    )
    provider = FakeCurrentAnswerProvider()
    web_search = FakeWebSearchTool()
    app = build_application(config=config, provider=provider)
    registry = ToolRegistry()
    registry.register(web_search)
    app.orchestrator.tool_registry = registry

    await app.start()
    result = await app.submit_user_message("quem vai jogar no jogo da copa de hoje")
    await app.close()

    assert result.text == "grounded answer"
    assert web_search.calls
    assert "hoje" in web_search.calls[0]["query"]


class FakeToolCallingProvider:
    def __init__(self) -> None:
        self.calls = 0

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            streaming=True, tool_calling=True, provider_reported_usage=False
        )

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        self.calls += 1
        if self.calls == 1:
            yield ProviderEvent(
                kind="completed",
                response=ModelResponse(
                    tool_calls=[
                        ToolCall(
                            id="call_1",
                            name="web_search",
                            arguments={"query": "copa do mundo"},
                        )
                    ],
                    finish_reason=FinishReason.TOOL_CALLS,
                ),
            )
            return
        yield ProviderEvent(
            kind="completed",
            response=ModelResponse(text="done", finish_reason=FinishReason.STOP),
        )

    async def complete(self, request: ModelRequest) -> ModelResponse:
        async for event in self.stream(request):
            if event.response:
                return event.response
        return ModelResponse()

    async def close(self) -> None:
        return None


async def test_web_search_tool_call_inherits_recent_current_context(tmp_path) -> None:
    config = AppConfig.from_mapping(
        {"api_mode": "ollama", "workspace": str(tmp_path), "model": "fake"}
    )
    provider = FakeToolCallingProvider()
    web_search = FakeWebSearchTool()
    app = build_application(config=config, provider=provider)
    registry = ToolRegistry()
    registry.register(web_search)
    app.orchestrator.tool_registry = registry

    await app.start()
    app.orchestrator.conversation.add_user("quem vai jogar no jogo da copa de hoje")
    result = await app.submit_user_message("copa do mundo")
    await app.close()

    assert result.text == "done"
    assert web_search.calls
    query = web_search.calls[0]["query"]
    assert "quem vai jogar no jogo da copa de hoje" in query
    assert "Copa do Mundo FIFA 2026" in query
