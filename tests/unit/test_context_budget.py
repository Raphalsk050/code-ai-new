"""How much of the window a session uses, as the server counts it, and what a
compaction keeps."""

from __future__ import annotations

import json
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
        self.requests: list[ModelRequest] = []
        self.summary = summary

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        self.excerpts.append(request.messages[-1].content)
        return ModelResponse(text=self.summary or f"summary #{len(self.excerpts)}")


def _compressor(
    provider=None, *, window: int = 20_000, reserve: int = 8_192, effort: str | None = None
) -> ContextCompressor:
    return ContextCompressor(
        counter=TokenCounter(model="qwen3.8-27b"),
        max_context_tokens=window,
        threshold=0.82,
        target=0.55,
        output_reserve=reserve,
        event_bus=AsyncEventBus(),
        provider=provider,
        model="m",
        summary_effort=(lambda: effort) if effort else None,
    )


def _summary_of(conversation: ConversationState) -> str:
    return next(m.content for m in conversation.messages if m.content.startswith(_SUMMARY_HEADER))


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


async def test_a_later_compaction_leaves_the_earlier_summary_as_it_was() -> None:
    first = "Task: fix the parser timeout; the default is 45 seconds."
    provider = _Summarizer(summary=first)
    compressor = _compressor(provider)
    conversation = _long_session(12)

    await compressor.ensure_capacity(conversation, [], force=True)
    provider.summary = "The tokenizer replaced the regex."
    conversation.messages.extend(_long_session(12).messages[2:])
    await compressor.ensure_capacity(conversation, [], force=True)

    # Written once and appended to: rewriting it every time is what wore the
    # oldest facts away. The model is asked only for what came after it.
    summary = _summary_of(conversation)
    assert summary.index(first) < summary.index("The tokenizer replaced the regex.")
    assert "do not repeat it" in provider.requests[1].messages[0].content


async def test_a_summary_past_its_share_of_the_window_is_folded_again() -> None:
    first = "Decisions: " + "; ".join(f"fact {i} about src/m{i}.py" for i in range(400))
    provider = _Summarizer(summary=first)
    compressor = _compressor(provider)
    conversation = _long_session(12)

    await compressor.ensure_capacity(conversation, [], force=True)
    provider.summary = "merged"
    conversation.messages.extend(_long_session(12).messages[2:])
    await compressor.ensure_capacity(conversation, [], force=True)

    assert _summary_of(conversation) == _SUMMARY_HEADER + "merged"
    assert "carry every fact in it forward" in provider.requests[1].messages[0].content


async def test_the_users_own_words_reach_the_summary_whole() -> None:
    provider = _Summarizer()
    compressor = _compressor(provider)
    conversation = _long_session(12)
    rules = "Regra do parser numero tal. " * 150
    spec = rules + "O timeout padrao deve ser 45 segundos. " + rules
    conversation.messages[1] = Message(role="user", content=spec)

    await compressor.ensure_capacity(conversation, [], force=True)

    # It was cut to its first 1200 and last 800 characters.
    assert "O timeout padrao deve ser 45 segundos." in provider.excerpts[0]


async def test_a_decision_mid_answer_reaches_the_summary_when_there_is_room() -> None:
    provider = _Summarizer()
    compressor = _compressor(provider, window=64_000)
    conversation = _long_session(12)
    reasoning = "Analisei o fluxo do parser. " * 60
    answer = reasoning + "Decidi trocar o regex por um tokenizer manual. " + reasoning
    conversation.messages.insert(2, Message(role="assistant", content=answer))

    await compressor.ensure_capacity(conversation, [], force=True)

    assert "Decidi trocar o regex por um tokenizer manual." in provider.excerpts[0]


async def test_when_the_excerpt_must_shrink_the_users_words_are_the_last_to_go() -> None:
    provider = _Summarizer()
    compressor = _compressor(provider)
    conversation = _long_session(200)

    await compressor.ensure_capacity(conversation, [], force=True)

    excerpt = provider.excerpts[0]
    assert "earlier messages left out to fit" in excerpt
    # The oldest message, and the one that says what the whole session is for.
    assert "Verifique a precisão da compactação" in excerpt


@pytest.mark.parametrize("effort,thinks", [("none", False), ("low", True), (None, True)])
async def test_the_summary_has_room_to_think_unless_thinking_is_off(effort, thinks) -> None:
    provider = _Summarizer()
    compressor = _compressor(provider, window=128_000, effort=effort)

    await compressor.ensure_capacity(_long_session(12), [], force=True)

    room = provider.requests[0].max_output_tokens
    assert (room > 8192) is thinks


async def test_a_summary_cut_off_mid_thought_is_not_kept_as_the_record() -> None:
    class CutOff:
        async def complete(self, request: ModelRequest) -> ModelResponse:
            return ModelResponse(
                text="Hmm, let me think about which files matter here",
                finish_reason=FinishReason.LENGTH,
            )

    compressor = _compressor(CutOff(), effort="low")
    conversation = _long_session(12)

    await compressor.ensure_capacity(conversation, [], force=True)

    summary = _summary_of(conversation)
    assert "let me think" not in summary
    # The plain record of the messages stands in, as when the model fails.
    assert "- tool:" in summary


async def test_a_restated_request_is_restated_once_and_never_from_the_summary() -> None:
    compressor = _compressor(_Summarizer())
    conversation = _long_session(12)

    await compressor.ensure_capacity(conversation, [], force=True)
    conversation.messages.extend(_long_session(12).messages[2:])
    await compressor.ensure_capacity(conversation, [], force=True)

    restated = [m.content for m in conversation.messages if "Standing user request" in m.content]
    assert len(restated) == 1
    assert restated[0].count("Standing user request") == 1
    assert "Verifique a precisão da compactação" in restated[0]


async def test_compacting_with_nothing_new_to_fold_changes_nothing() -> None:
    provider = _Summarizer()
    compressor = _compressor(provider)
    conversation = _long_session(12)
    await compressor.ensure_capacity(conversation, [], force=True)
    before = list(conversation.messages)

    result = await compressor.ensure_capacity(conversation, [], force=True)

    assert not result.compressed
    assert conversation.messages == before
    assert len(provider.requests) == 1


# --- clearing old tool output ------------------------------------------------------


class _Bus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def emit(self, event_type: str, payload: dict | None = None, *, source: str = "") -> None:
        self.events.append((event_type, payload or {}))


def _output(i: int) -> str:
    return "\n".join(f"line {j} of the output of step {i}" for j in range(160))


def _busy_session(steps: int, name: str = "read_file") -> ConversationState:
    messages = [Message(role="system", content="You are Code-AI."), Message(role="user", content=_PT)]
    for i in range(steps):
        call = ToolCall(id=f"c{i}", name=name, arguments={"path": f"src/m{i}.py"})
        messages.append(Message(role="assistant", content="", tool_calls=[call]))
        messages.append(Message(role="tool", content=_output(i), tool_call_id=f"c{i}", name=name))
    return ConversationState(messages=messages)


def _clearing(provider=None) -> tuple[ContextCompressor, _Bus]:
    bus = _Bus()
    compressor = ContextCompressor(
        counter=TokenCounter(model="qwen3.8-27b"),
        max_context_tokens=64_000,
        threshold=0.82,
        target=0.55,
        output_reserve=8_192,
        event_bus=bus,
        provider=provider,
        model="m",
    )
    return compressor, bus


async def test_old_tool_output_goes_before_anything_is_summarized() -> None:
    provider = _Summarizer()
    compressor, bus = _clearing(provider)
    conversation = _busy_session(30)

    result = await compressor.ensure_capacity(conversation, [])

    results = [m for m in conversation.messages if m.role == "tool"]
    cleared = [m for m in results if m.content.startswith("[Old result cleared")]
    # No model call: the old output became a line each, the newest stayed whole.
    assert not result.compressed and provider.requests == []
    assert cleared and all(m.content == _output(int(m.tool_call_id[1:])) for m in results[-5:])
    assert "path=src/m0.py" in cleared[0].content
    assert any(name == "context.results.cleared" for name, _ in bus.events)


async def test_a_cleared_command_keeps_how_it_ended() -> None:
    compressor, _ = _clearing()
    conversation = _busy_session(30)
    run = json.dumps(
        {"exit_code": 1, "stdout": _output(0) + "\nFAILED tests/test_parser.py::test_quotes"}
    )
    conversation.messages[3] = Message(
        role="tool", content=run, tool_call_id="c0", name="execute_command"
    )

    await compressor.ensure_capacity(conversation, [])

    line = conversation.messages[3].content
    assert line.startswith("[Old result cleared")
    assert "exit_code=1" in line and "FAILED tests/test_parser.py::test_quotes" in line


async def test_the_users_answers_and_loaded_skills_are_never_cleared() -> None:
    compressor, _ = _clearing()
    conversation = _busy_session(30)
    conversation.messages[3] = Message(
        role="tool", content=_output(0), tool_call_id="c0", name="ask_user"
    )
    conversation.messages[5] = Message(
        role="tool", content=_output(1), tool_call_id="c1", name="use_skill"
    )

    await compressor.ensure_capacity(conversation, [])

    assert conversation.messages[3].content == _output(0)
    assert conversation.messages[5].content == _output(1)


async def test_clearing_waits_until_it_frees_enough_to_be_worth_the_cache() -> None:
    compressor, bus = _clearing()
    conversation = _busy_session(12)
    before = [m.content for m in conversation.messages]

    await compressor.ensure_capacity(conversation, [])

    assert [m.content for m in conversation.messages] == before
    assert not any(name == "context.results.cleared" for name, _ in bus.events)


async def test_an_old_file_written_whole_keeps_its_path_not_its_text() -> None:
    compressor, _ = _clearing()
    conversation = _busy_session(30)
    written = ToolCall(
        id="c0", name="write_file", arguments={"path": "src/new.py", "content": _output(0)}
    )
    conversation.messages[2] = Message(role="assistant", content="", tool_calls=[written])

    await compressor.ensure_capacity(conversation, [])

    arguments = conversation.messages[2].tool_calls[0].arguments
    assert arguments["path"] == "src/new.py"
    assert arguments["content"].endswith("characters cleared to free context]")


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


class _ReadsThenAnswers:
    """Calls read_file, then answers in pieces; reports usage as vLLM does."""

    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(streaming=True, tool_calling=True)

    async def complete(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(text="summary")

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        self.requests.append(request)
        if len(self.requests) == 1:
            arguments = '{"path": "a.py"}'
            yield ProviderEvent(
                kind="tool_call_delta",
                tool_call_name="read_file",
                tool_call_arguments=arguments,
                tool_call_index=0,
            )
            call = ToolCall(id="c1", name="read_file", arguments={"path": "a.py"})
            usage = TokenUsage.from_counts(
                input_tokens=5_000, output_tokens=20, exact=True, source="openai"
            )
            done = ModelResponse(tool_calls=[call], finish_reason=FinishReason.TOOL_CALLS, usage=usage)
            yield ProviderEvent(kind="completed", response=done)
            return
        for piece in ("Li o arquivo. ", "Ele define a funcao parse. " * 30, "Pronto."):
            yield ProviderEvent(kind="text_delta", text_delta=piece)
        usage = TokenUsage.from_counts(
            input_tokens=7_000, output_tokens=300, exact=True, source="openai"
        )
        done = ModelResponse(text="Pronto.", finish_reason=FinishReason.STOP, usage=usage)
        yield ProviderEvent(kind="completed", response=done)

    async def close(self) -> None:
        return None


async def test_the_context_meter_moves_while_the_model_streams_and_tools_land(
    tmp_path, monkeypatch
) -> None:
    from code_ai.core import orchestration

    monkeypatch.setattr(orchestration, "_METER_INTERVAL_S", 0.0)
    (tmp_path / "a.py").write_text("def parse(text):\n    return text\n" * 200, encoding="utf-8")
    app = _app(tmp_path, _ReadsThenAnswers())
    meter: list[tuple[int, bool]] = []
    app.subscribe(
        lambda event: meter.append(
            (event.payload["active_context_tokens"], event.payload["active_context_estimated"])
        )
        if event.event_type == "usage.updated" and "active_context_tokens" in event.payload
        else None
    )
    await app.start()
    await app.submit_user_message("Leia a.py")
    await app.close()

    values = [tokens for tokens, _ in meter]
    first = meter.index((5_020, False))
    # The server's count once the call is in, then the file it read on top.
    assert values[first + 1] > 5_020
    # The answer streams the meter up, and the server's count closes it.
    answered = meter.index((7_300, False))
    streamed = values[first + 2 : answered]
    assert len(streamed) >= 2 and streamed == sorted(streamed)
