"""How much of the window a session uses, as the server counts it, and what a
compaction keeps."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from code_ai.bootstrap import build_application
from code_ai.config.models import AppConfig
from code_ai.context.compression import (
    _SUMMARY_HEADER,
    ContextCompressor,
)
from code_ai.context.conversation import ConversationState
from code_ai.context.token_counting import TokenCounter
from code_ai.events.bus import AsyncEventBus
from code_ai.providers.models import (
    FinishReason,
    Message,
    ModelRequest,
    ModelResponse,
    ProviderCapabilities,
    ProviderEvent,
    TokenUsage,
    ToolCall,
)

_PT = "Verifique a precisão da compactação e não perca decisões nem comandos. " * 10


class _Summarizer:
    """Stands in for the model's summary, and keeps what it was asked."""

    def __init__(self, summary: str = "") -> None:
        self.excerpts: list[str] = []
        self.summary = summary

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.excerpts.append(request.messages[-1].content)
        return ModelResponse(text=self.summary or f"summary #{len(self.excerpts)}")


def _compressor(provider=None, *, window: int = 20_000, reserve: int = 8_192) -> ContextCompressor:
    return ContextCompressor(
        counter=TokenCounter(model="qwen3.8-27b"),
        max_context_tokens=window,
        threshold=0.82,
        target=0.55,
        output_reserve=reserve,
        event_bus=AsyncEventBus(),
        provider=provider,
        model="m",
    )


def test_the_count_is_the_text_the_model_reads_not_its_json() -> None:
    counter = TokenCounter(model="qwen3.8-27b")
    text = counter.count_text(_PT).tokens

    counted = counter.count_request([Message(role="user", content=_PT)], []).tokens

    # The JSON wire form escaped every accented letter as \uXXXX: 70% over.
    assert text <= counted <= text + 20


def test_until_the_server_answers_the_whole_reserve_is_held_back() -> None:
    compressor = _compressor(window=128_000, reserve=32_768)

    assert compressor.budget == 128_000 - 32_768
    assert compressor.output_room(90_000) == 32_768

    compressor.observe(counted=10_000, reported=11_000)

    # Calibrated, the reserve is held back only when the prompt leaves room.
    assert compressor.budget == 128_000 - 8_192
    assert compressor.output_room(20_000) == 32_768
    room = compressor.output_room(100_000)
    assert 8_192 <= room < 32_768 and 100_000 + room <= 128_000


def test_the_servers_count_scales_what_is_counted_here() -> None:
    compressor = _compressor()
    messages = [Message(role="user", content=_PT)]
    _, counted = compressor.measure(messages, [])

    compressor.observe(counted=counted.tokens, reported=round(counted.tokens * 1.1))

    calibrated, _ = compressor.measure(messages, [])
    assert calibrated == round(counted.tokens * 1.1)


def test_an_absurd_server_count_cannot_swing_the_count_past_its_limits() -> None:
    compressor = _compressor()
    messages = [Message(role="user", content=_PT)]
    _, counted = compressor.measure(messages, [])

    compressor.observe(counted=counted.tokens, reported=counted.tokens * 50)

    assert compressor.measure(messages, [])[0] <= counted.tokens * 2


def _long_session(steps: int) -> ConversationState:
    messages = [
        Message(role="system", content="You are Code-AI."),
        Message(role="user", content=_PT),
    ]
    for i in range(steps):
        call = ToolCall(id=f"c{i}", name="read_file", arguments={"path": f"src/m{i}.py"})
        messages.append(Message(role="assistant", content="", tool_calls=[call]))
        result = Message(
            role="tool", content=f"{i} " + "x" * 3_000, tool_call_id=f"c{i}", name="read_file"
        )
        messages.append(result)
    return ConversationState(messages=messages)


async def test_an_earlier_summary_reaches_the_next_summary_whole() -> None:
    first = "Decisions: " + "; ".join(f"fact {i} about src/m{i}.py" for i in range(400))
    provider = _Summarizer(summary=first)
    compressor = _compressor(provider)
    conversation = _long_session(12)

    await compressor.ensure_capacity(conversation, [], force=True)
    conversation.messages.extend(_long_session(12).messages[2:])
    await compressor.ensure_capacity(conversation, [], force=True)

    # It used to be cut to its first 2000 characters: 180 facts in, 32 out.
    assert len(first) > 2_000
    assert first in provider.excerpts[1]


async def test_a_long_output_keeps_its_end_in_the_excerpt() -> None:
    provider = _Summarizer()
    compressor = _compressor(provider)
    conversation = _long_session(12)
    failure = "FAILED tests/unit/test_parser.py::test_timeout - AssertionError"
    conversation.messages[3] = Message(
        role="tool", content="ok\n" * 3_000 + failure, tool_call_id="c0", name="execute_command"
    )

    await compressor.ensure_capacity(conversation, [], force=True)

    assert failure in provider.excerpts[0]
    assert "characters left out" in provider.excerpts[0]


async def test_the_summary_request_never_outgrows_the_window() -> None:
    provider = _Summarizer()
    compressor = _compressor(provider)
    conversation = _long_session(200)
    earlier = Message(role="user", content=_SUMMARY_HEADER + "what the first compaction kept")
    conversation.messages.insert(1, earlier)

    await compressor.ensure_capacity(conversation, [], force=True)

    excerpt = provider.excerpts[0]
    counter = TokenCounter(model="qwen3.8-27b")
    assert counter.count_text(excerpt).tokens <= compressor.budget * 0.6 + 50
    assert "what the first compaction kept" in excerpt
    assert "earlier messages left out to fit" in excerpt


async def test_without_the_model_the_fallback_keeps_the_earlier_summary() -> None:
    class Broken:
        async def complete(self, request):
            raise RuntimeError("provider down")

    compressor = _compressor(Broken())
    conversation = _long_session(12)
    kept = "Task: fix the parser timeout. " * 40
    conversation.messages.insert(2, Message(role="user", content=_SUMMARY_HEADER + kept))

    await compressor.ensure_capacity(conversation, [], force=True)

    summary = next(
        m.content for m in conversation.messages if m.content.startswith(_SUMMARY_HEADER)
    )
    assert kept.strip() in summary


# --- through the orchestrator ------------------------------------------------------


class _Reporting:
    """Answers once, reporting a prompt size the way the server would."""

    def __init__(self, *, source: str, factor: float = 1.1) -> None:
        self.source = source
        self.factor = factor
        self.requests: list[ModelRequest] = []

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(streaming=True, tool_calling=True)

    async def complete(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(text="summary")

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        self.requests.append(request)
        counted = int(request.metadata["counted_tokens"])
        usage = TokenUsage.from_counts(
            input_tokens=round(counted * self.factor),
            output_tokens=3,
            exact=True,
            source=self.source,
        )
        yield ProviderEvent(
            kind="completed",
            response=ModelResponse(text="done", finish_reason=FinishReason.STOP, usage=usage),
        )

    async def close(self) -> None:
        return None


def _app(tmp_path, provider):
    config = AppConfig.from_mapping(
        {
            "api_mode": "completions",
            "workspace": str(tmp_path),
            "model": "qwen3.8-27b",
            "permission_mode": "bypass",
            "planner": {"enabled": False},
            "output_token_reserve": 32_768,
            "budgets": {"max_context_tokens": 128_000},
        }
    )
    return build_application(config=config, provider=provider)


@pytest.mark.parametrize("source,learns", [("openai", True), ("ollama", False)])
async def test_a_turn_teaches_the_count_only_from_a_whole_prompt_count(
    tmp_path, source, learns
) -> None:
    provider = _Reporting(source=source)
    app = _app(tmp_path, provider)
    await app.start()
    await app.submit_user_message("Oi")
    await app.close()

    compressor = app.orchestrator.compressor
    # Before the first answer the whole reserve is asked for, as always.
    assert provider.requests[0].max_output_tokens == 32_768
    # Ollama's count leaves out the prefix its cache reused: it teaches nothing.
    assert compressor._calibrated is learns
    if learns:
        assert compressor._scale == pytest.approx(1.1, rel=0.01)
