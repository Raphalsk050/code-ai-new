"""Turns that end in prose must surface that prose as a real answer message.

Regression suite for the observed failure where a mutation-classified task
ended in prose: the text had streamed only on the "working" channel (dim
trace), no assistant.final was ever emitted, and the user saw no answer at
all - the turn just stopped.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from code_ai.bootstrap import build_application
from code_ai.config.models import AppConfig
from code_ai.events.models import EventEnvelope
from code_ai.providers.models import (
    FinishReason,
    ModelRequest,
    ModelResponse,
    ProviderCapabilities,
    ProviderEvent,
    ToolCall,
)

FIRST_PROSE = "Ainda estou analisando o que fazer."
FINAL_PROSE = "Preciso de mais detalhes para prosseguir com a mudança."


def _config(tmp_path, **overrides) -> AppConfig:
    data = {
        "api_mode": "ollama",
        "workspace": str(tmp_path),
        "model": "fake",
        "permission_mode": "bypass",
        "memories_dir": str(tmp_path / "memories"),
        # These tests script exact provider call sequences; the post-turn
        # reflection meta-call would add calls the scripts do not expect.
        "memory": {"reflection_enabled": False},
    }
    data.update(overrides)
    return AppConfig.from_mapping(data)


class _BaseProvider:
    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            streaming=True, tool_calling=True, provider_reported_usage=False
        )

    async def complete(self, request: ModelRequest) -> ModelResponse:
        async for event in self.stream(request):
            if event.response:
                return event.response
        return ModelResponse()

    async def close(self) -> None:
        return None


class ProseOnlyProvider(_BaseProvider):
    """Streams prose and never calls a tool, whatever the runtime asks."""

    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        self.calls += 1
        text = FIRST_PROSE if self.calls == 1 else FINAL_PROSE
        yield ProviderEvent(kind="text_delta", text_delta=text)
        yield ProviderEvent(
            kind="completed",
            response=ModelResponse(text=text, finish_reason=FinishReason.STOP),
        )


async def test_working_channel_prose_is_announced_as_final_answer(tmp_path) -> None:
    provider = ProseOnlyProvider()
    app = build_application(config=_config(tmp_path), provider=provider)
    events: list[EventEnvelope] = []
    app.subscribe(events.append)

    await app.start()
    result = await app.submit_user_message("crie um arquivo config.json no projeto")
    await app.close()

    # Mutation task: one corrective nudge, then the prose ends the turn.
    assert provider.calls == 2
    assert result.text == FINAL_PROSE

    # The prose only ever streamed as dim "working" trace...
    channels = {
        str(e.payload.get("channel"))
        for e in events
        if e.event_type == "model.stream.delta"
    }
    assert channels == {"working"}

    # ...so the finish path must announce it as the turn's actual answer.
    finals = [e for e in events if e.event_type == "assistant.final"]
    assert [str(e.payload.get("text")) for e in finals] == [FINAL_PROSE]


class InspectsThenAnswersProvider(_BaseProvider):
    """submit_plan, read a file, then answer in prose - never declares steps."""

    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        self.calls += 1
        if self.calls == 1:
            response = ModelResponse(
                tool_calls=[
                    ToolCall(
                        id="p1",
                        name="submit_plan",
                        arguments={"steps": ["Ler o arquivo", "Responder ao usuário"]},
                    )
                ],
                finish_reason=FinishReason.TOOL_CALLS,
            )
            yield ProviderEvent(kind="completed", response=response)
            return
        if self.calls == 2:
            response = ModelResponse(
                tool_calls=[
                    ToolCall(
                        id="r1", name="read_file", arguments={"path": "note.txt"}
                    )
                ],
                finish_reason=FinishReason.TOOL_CALLS,
            )
            yield ProviderEvent(kind="completed", response=response)
            return
        text = "O arquivo define a configuração padrão do serviço."
        yield ProviderEvent(kind="text_delta", text_delta=text)
        yield ProviderEvent(
            kind="completed",
            response=ModelResponse(text=text, finish_reason=FinishReason.STOP),
        )


async def test_prose_answer_backed_by_evidence_completes_the_plan(tmp_path) -> None:
    # Regression: the delivered answer left the sidebar at "0/N, waiting for
    # you" because the model never called complete_plan_step. With gathered
    # evidence the plan must settle as completed, not pause on the user.
    (tmp_path / "note.txt").write_text("config: default\n", encoding="utf-8")
    provider = InspectsThenAnswersProvider()
    app = build_application(config=_config(tmp_path), provider=provider)
    events: list[EventEnvelope] = []
    app.subscribe(events.append)

    await app.start()
    result = await app.submit_user_message("leia o note.txt e me diga o que ele faz")
    await app.close()

    assert result.error is None
    assert "configuração" in result.text
    event_types = [e.event_type for e in events]
    assert "planning.plan.completed" in event_types
    assert "planning.plan.waiting" not in event_types


async def test_answer_channel_prose_is_not_announced_twice(tmp_path) -> None:
    provider = ProseOnlyProvider()
    app = build_application(config=_config(tmp_path), provider=provider)
    events: list[EventEnvelope] = []
    app.subscribe(events.append)

    await app.start()
    result = await app.submit_user_message("ola, tudo bem?")
    await app.close()

    # Conversation: the prose streams live on the answer channel and the turn
    # ends on the first response, so a duplicate final block must not appear.
    assert provider.calls == 1
    assert result.text == FIRST_PROSE
    channels = {
        str(e.payload.get("channel"))
        for e in events
        if e.event_type == "model.stream.delta"
    }
    assert channels == {"answer"}
    assert not [e for e in events if e.event_type == "assistant.final"]
