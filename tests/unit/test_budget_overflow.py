from __future__ import annotations

from collections.abc import AsyncIterator

from code_ai.bootstrap import build_application
from code_ai.config.models import AppConfig
from code_ai.core.memory import FailureMemoryStore
from code_ai.providers.models import (
    FinishReason,
    ModelRequest,
    ModelResponse,
    ProviderCapabilities,
    ProviderEvent,
    TokenUsage,
)


def _config(tmp_path, **overrides) -> AppConfig:
    data = {
        "api_mode": "ollama",
        "workspace": str(tmp_path),
        "model": "fake",
        "permission_mode": "bypass",
        "planner": {"enabled": False},
    }
    data.update(overrides)
    return AppConfig.from_mapping(data)


class _BaseProvider:
    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(streaming=True, tool_calling=True)

    async def complete(self, request: ModelRequest) -> ModelResponse:
        async for event in self.stream(request):
            if event.response:
                return event.response
        return ModelResponse()

    async def close(self) -> None:
        return None


class BudgetOverflowThenAnswerProvider(_BaseProvider):
    """First reply burns the whole budget in reasoning; the retry answers."""

    def __init__(self, *, use_length_finish: bool) -> None:
        self.calls = 0
        self.use_length_finish = use_length_finish
        self.retry_feedback = ""

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        self.calls += 1
        if self.calls == 1:
            reasoning = "I should build the map system and then the NPCs and then..."
            if self.use_length_finish:
                finish = FinishReason.LENGTH
                usage = None
            else:
                # ollama/qwen mislabels truncation as "stop"; detection must fall
                # back to "output_tokens reached the requested cap".
                finish = FinishReason.STOP
                usage = TokenUsage.from_counts(
                    input_tokens=10, output_tokens=request.max_output_tokens or 0
                )
            yield ProviderEvent(
                kind="completed",
                response=ModelResponse(
                    text="", reasoning=reasoning, finish_reason=finish, usage=usage
                ),
            )
            return
        self.retry_feedback = "".join(
            m.content for m in request.messages if m.role == "user"
        )
        yield ProviderEvent(
            kind="completed",
            response=ModelResponse(text="map system created", finish_reason=FinishReason.STOP),
        )


async def test_budget_overflow_via_length_finish_is_retried(tmp_path) -> None:
    provider = BudgetOverflowThenAnswerProvider(use_length_finish=True)
    store = FailureMemoryStore(tmp_path / "mem")
    app = build_application(
        config=_config(tmp_path), provider=provider, failure_memory=store
    )
    events: list[str] = []
    app.subscribe(lambda event: events.append(event.event_type))

    await app.start()
    result = await app.submit_user_message("continue the game")
    await app.close()

    # The silent freeze is gone: the truncated turn re-prompts and finishes.
    assert provider.calls == 2
    assert "model.budget_overflow" in events
    assert result.text == "map system created"
    # The model got its own truncated thinking handed back, plus the nudge.
    assert "ran out of output budget" in provider.retry_feedback
    assert "I should build the map system" in provider.retry_feedback
    # And the lesson was persisted for future sessions.
    assert store.lessons() and store.lessons()[0].trigger == "token_budget_exceeded"


async def test_budget_overflow_detected_via_usage_cap(tmp_path) -> None:
    provider = BudgetOverflowThenAnswerProvider(use_length_finish=False)
    app = build_application(
        config=_config(tmp_path),
        provider=provider,
        failure_memory=FailureMemoryStore(tmp_path / "m"),
    )
    events: list[str] = []
    app.subscribe(lambda event: events.append(event.event_type))

    await app.start()
    result = await app.submit_user_message("continue the game")
    await app.close()

    assert provider.calls == 2
    assert "model.budget_overflow" in events
    assert result.text == "map system created"


class AlwaysOverflowProvider(_BaseProvider):
    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        self.calls += 1
        yield ProviderEvent(
            kind="completed",
            response=ModelResponse(
                text="", reasoning="thinking forever", finish_reason=FinishReason.LENGTH
            ),
        )


async def test_persistent_overflow_is_bounded_not_infinite(tmp_path) -> None:
    provider = AlwaysOverflowProvider()
    app = build_application(
        config=_config(tmp_path),
        provider=provider,
        failure_memory=FailureMemoryStore(tmp_path / "m"),
    )

    await app.start()
    result = await app.submit_user_message("continue the game")
    await app.close()

    # Bounded by _MAX_BUDGET_RETRIES (2): the initial call + two retries, then it
    # winds down instead of looping forever.
    assert provider.calls == 3
    assert result.cancelled is False


def test_output_token_reserve_defaults_to_32k() -> None:
    config = AppConfig.from_mapping({"workspace": "/tmp"})
    assert config.output_token_reserve == 32768
