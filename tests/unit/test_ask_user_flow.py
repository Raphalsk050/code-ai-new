"""End-to-end behavior of blocking ask_user questions.

Regression suite for the observed failure where ask_user returned a "blocked"
sentinel that nothing enforced: the loop kept calling the model, the model's
"I'm waiting" prose streamed only as dim working trace, and the question never
reached the user as a real answer message.
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

QUESTION = (
    "Para começarmos o levantamento de requisitos, qual banco de dados "
    "o sistema deve usar?"
)


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


class AsksThenKeepsTalkingProvider(_BaseProvider):
    """Calls ask_user on the first step; any later step proves the bug.

    Before the fix the runtime fed the blocked sentinel back to the model,
    which produced further "I'm waiting" steps. The fixed loop must never
    request a second completion in this turn.
    """

    def __init__(self, *, choices: list[str] | None = None) -> None:
        self.calls = 0
        self.choices = choices

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        self.calls += 1
        if self.calls == 1:
            arguments: dict[str, object] = {"question": QUESTION}
            if self.choices is not None:
                arguments["choices"] = self.choices
            yield ProviderEvent(
                kind="completed",
                response=ModelResponse(
                    tool_calls=[ToolCall(id="q1", name="ask_user", arguments=arguments)],
                    finish_reason=FinishReason.TOOL_CALLS,
                ),
            )
            return
        yield ProviderEvent(
            kind="completed",
            response=ModelResponse(
                text="Estou aguardando sua resposta.", finish_reason=FinishReason.STOP
            ),
        )


async def test_ask_user_ends_the_turn_with_the_question_as_final_answer(tmp_path) -> None:
    provider = AsksThenKeepsTalkingProvider()
    app = build_application(config=_config(tmp_path), provider=provider)
    events: list[EventEnvelope] = []
    app.subscribe(events.append)

    await app.start()
    result = await app.submit_user_message(
        "implemente um sistema de compra e estoque de supermercado"
    )
    await app.close()

    # The turn ends at the question: no extra "I'm waiting" model steps.
    assert provider.calls == 1
    assert result.error is None
    assert result.text == QUESTION

    # The question reaches the user as a real answer message, not dim trace.
    finals = [e for e in events if e.event_type == "assistant.final"]
    assert [str(e.payload.get("text")) for e in finals] == [QUESTION]


async def test_ask_user_choices_render_as_numbered_options(tmp_path) -> None:
    provider = AsksThenKeepsTalkingProvider(choices=["SQLite", "Postgres"])
    app = build_application(config=_config(tmp_path), provider=provider)

    await app.start()
    result = await app.submit_user_message("crie o schema do banco do projeto")
    await app.close()

    assert provider.calls == 1
    assert result.text.startswith(QUESTION)
    assert "1. SQLite" in result.text
    assert "2. Postgres" in result.text
