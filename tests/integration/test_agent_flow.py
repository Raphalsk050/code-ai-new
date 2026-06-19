from __future__ import annotations

from collections.abc import AsyncIterator

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
