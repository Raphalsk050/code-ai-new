from __future__ import annotations

from code_ai.context.compression import ContextCompressor
from code_ai.context.conversation import ConversationState
from code_ai.context.token_counting import TokenCounter
from code_ai.events.bus import AsyncEventBus
from code_ai.providers.models import (
    ImageContent,
    Message,
    ModelRequest,
    ModelResponse,
    ToolCall,
)


class _StubProvider:
    """Minimal provider that records the summary request and returns canned text."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return ModelResponse(text=self.text)


def test_add_assistant_keeps_tool_calls_structured_not_text() -> None:
    conversation = ConversationState()
    call = ToolCall(
        id="call_1",
        name="edit_code",
        arguments={"path": "agent.py", "new_text": "print('hi')"},
    )

    conversation.add_assistant("I will edit the file.", [call])

    assert len(conversation.messages) == 1
    message = conversation.messages[0]
    assert message.role == "assistant"
    assert message.content == "I will edit the file."
    # The call must be preserved structurally and never flattened into content,
    # which is what previously taught the model to echo tool calls as text.
    assert message.tool_calls == [call]
    assert "Tool calls requested" not in message.content
    assert "new_text" not in message.content


def test_add_assistant_ignores_empty_turn() -> None:
    conversation = ConversationState()
    conversation.add_assistant("", None)
    assert conversation.messages == []


def test_images_are_counted_flat_not_by_base64_size() -> None:
    import base64

    counter = TokenCounter(model="unknown-local-model")
    # A realistic pasted screenshot: megabytes of base64.
    huge = ImageContent(data=base64.b64encode(b"\x89PNG" + b"\x00" * 4_000_000).decode("ascii"))
    text_only = counter.count_request([Message(role="user", content="look [Image #1]")], [])
    with_image = counter.count_request(
        [Message(role="user", content="look [Image #1]", images=[huge])], []
    )

    # The image adds a bounded visual-token estimate, never its payload size.
    added = with_image.tokens - text_only.tokens
    assert 0 < added <= 2000
    assert with_image.estimated


async def test_pasted_screenshot_does_not_overflow_the_context_budget() -> None:
    import base64

    # Regression: counting the base64 payload as prompt text made a single
    # pasted screenshot "not fit in the context window".
    bus = AsyncEventBus(session_id="session")
    conversation = ConversationState()
    huge = ImageContent(data=base64.b64encode(b"\x89PNG" + b"\x00" * 4_000_000).decode("ascii"))
    conversation.add_user("what is in this screenshot? [Image #1]", images=[huge])

    compressor = ContextCompressor(
        counter=TokenCounter(model="unknown-local-model"),
        max_context_tokens=256000,
        threshold=0.82,
        target=0.55,
        output_reserve=32768,
        event_bus=bus,
    )
    result = await compressor.ensure_capacity(conversation, [])

    assert not result.compressed
    assert conversation.messages[0].images == [huge]


async def test_compression_preserves_recent_request_and_resets_remote_state() -> None:
    bus = AsyncEventBus(session_id="session")
    conversation = ConversationState(previous_response_id="resp_1")
    for index in range(20):
        conversation.messages.append(
            Message(role="user", content=f"old message {index} " + ("x" * 200))
        )
    conversation.messages.append(Message(role="user", content="current request"))

    compressor = ContextCompressor(
        counter=TokenCounter(model="unknown-local-model"),
        max_context_tokens=4096,
        threshold=0.1,
        target=0.8,
        output_reserve=512,
        event_bus=bus,
    )
    result = await compressor.ensure_capacity(conversation, [])

    assert result.compressed
    assert conversation.previous_response_id is None
    assert conversation.messages[-1].content == "current request"
    assert any("Compressed context summary" in message.content for message in conversation.messages)


async def test_ensure_capacity_skips_below_threshold_without_force() -> None:
    bus = AsyncEventBus(session_id="session")
    conversation = ConversationState()
    for index in range(20):
        conversation.messages.append(Message(role="user", content=f"old message {index}"))
    conversation.messages.append(Message(role="user", content="current request"))

    compressor = ContextCompressor(
        counter=TokenCounter(model="unknown-local-model"),
        max_context_tokens=4096,
        threshold=0.9,
        target=0.8,
        output_reserve=512,
        event_bus=bus,
    )
    result = await compressor.ensure_capacity(conversation, [])

    assert not result.compressed
    assert result.previous_tokens == result.active_tokens


async def test_ensure_capacity_force_compresses_even_below_threshold() -> None:
    bus = AsyncEventBus(session_id="session")
    conversation = ConversationState()
    for index in range(20):
        conversation.messages.append(
            Message(role="user", content=f"old message {index} " + ("x" * 200))
        )
    conversation.messages.append(Message(role="user", content="current request"))

    compressor = ContextCompressor(
        counter=TokenCounter(model="unknown-local-model"),
        max_context_tokens=4096,
        threshold=0.9,
        target=0.8,
        output_reserve=512,
        event_bus=bus,
    )
    result = await compressor.ensure_capacity(conversation, [], force=True)

    assert result.compressed
    assert result.previous_tokens > result.active_tokens
    assert any("Compressed context summary" in message.content for message in conversation.messages)


async def test_ensure_capacity_force_is_noop_when_nothing_to_summarize() -> None:
    bus = AsyncEventBus(session_id="session")
    conversation = ConversationState()
    conversation.messages.append(Message(role="user", content="hello"))

    compressor = ContextCompressor(
        counter=TokenCounter(model="unknown-local-model"),
        max_context_tokens=4096,
        threshold=0.1,
        target=0.8,
        output_reserve=512,
        event_bus=bus,
    )
    result = await compressor.ensure_capacity(conversation, [], force=True)

    assert not result.compressed
    assert result.previous_tokens == result.active_tokens
    assert len(conversation.messages) == 1


async def test_compression_uses_model_summary_when_provider_available() -> None:
    bus = AsyncEventBus(session_id="session")
    conversation = ConversationState()
    for index in range(20):
        conversation.messages.append(
            Message(role="user", content=f"old message {index} " + ("x" * 200))
        )
    conversation.messages.append(Message(role="user", content="current request"))

    provider = _StubProvider(text="Task: build X. Files: a.py edited. Next: run tests.")
    compressor = ContextCompressor(
        counter=TokenCounter(model="unknown-local-model"),
        max_context_tokens=4096,
        threshold=0.1,
        target=0.8,
        output_reserve=512,
        event_bus=bus,
        provider=provider,
        model="unknown-local-model",
    )

    result = await compressor.ensure_capacity(conversation, [])

    assert result.compressed
    assert len(provider.requests) == 1
    summary = next(m for m in conversation.messages if "Compressed context summary" in m.content)
    # The model's summary text is retained verbatim, not the heuristic truncation.
    assert "Next: run tests." in summary.content
    assert conversation.messages[-1].content == "current request"


async def test_compression_falls_back_to_heuristic_when_summary_call_fails() -> None:
    bus = AsyncEventBus(session_id="session")
    conversation = ConversationState()
    for index in range(20):
        conversation.messages.append(Message(role="user", content=f"old {index} " + ("x" * 200)))
    conversation.messages.append(Message(role="user", content="current request"))

    class _FailingProvider:
        async def complete(self, request: ModelRequest) -> ModelResponse:
            raise RuntimeError("provider offline")

    compressor = ContextCompressor(
        counter=TokenCounter(model="unknown-local-model"),
        max_context_tokens=4096,
        threshold=0.1,
        target=0.8,
        output_reserve=512,
        event_bus=bus,
        provider=_FailingProvider(),
        model="unknown-local-model",
    )

    result = await compressor.ensure_capacity(conversation, [])

    # A provider failure must never block the turn — the heuristic summary stands in.
    assert result.compressed
    assert any("Compressed context summary" in m.content for m in conversation.messages)
    assert conversation.messages[-1].content == "current request"


def _tool_heavy_conversation() -> ConversationState:
    """A long turn whose recent window is nothing but assistant/tool traffic."""
    conversation = ConversationState()
    conversation.messages.append(Message(role="system", content="system prompt"))
    conversation.messages.append(Message(role="user", content="refactor the parser " + "x" * 400))
    for index in range(20):
        call = ToolCall(id=f"call_{index}", name="read_file", arguments={"path": f"f{index}.py"})
        conversation.messages.append(
            Message(role="assistant", content=f"reading f{index} " + "x" * 200, tool_calls=[call])
        )
        conversation.messages.append(
            Message(
                role="tool",
                content=f"contents of f{index} " + "x" * 200,
                tool_call_id=f"call_{index}",
                name="read_file",
            )
        )
    return conversation


def _only(conversation: ConversationState, marker: str) -> Message:
    """The single message carrying ``marker``, with a readable failure otherwise.

    A bare ``next(...)`` would raise StopIteration inside the coroutine, which
    Python re-reports as an unrelated RuntimeError and hides what actually broke.
    """
    matches = [message for message in conversation.messages if marker in message.content]
    assert len(matches) == 1, f"expected exactly one {marker!r} message, got {len(matches)}"
    return matches[0]


def _compressor(bus: AsyncEventBus) -> ContextCompressor:
    return ContextCompressor(
        counter=TokenCounter(model="unknown-local-model"),
        max_context_tokens=4096,
        threshold=0.1,
        target=0.8,
        output_reserve=512,
        event_bus=bus,
    )


async def test_compression_never_leaves_a_system_message_below_the_top() -> None:
    # Regression: the summary used to re-enter as a second system message, which
    # chat templates reject outright ("System message must be at the
    # beginning."). Because compression rewrites the history in place, that 400
    # then repeated on every later turn and the session never recovered.
    bus = AsyncEventBus(session_id="session")
    conversation = _tool_heavy_conversation()

    result = await _compressor(bus).ensure_capacity(conversation, [])

    assert result.compressed
    assert conversation.messages[0].role == "system"
    assert all(message.role != "system" for message in conversation.messages[1:])
    summary = _only(conversation, "Compressed context summary")
    assert summary.role == "user"


async def test_compression_keeps_a_user_turn_in_a_tool_only_window() -> None:
    # The other half of the same failure: summarizing pushed every user turn out
    # of the kept window, and the template then raised "No user query found in
    # messages." The standing request is restated so both the template and the
    # model still see what was asked.
    bus = AsyncEventBus(session_id="session")
    conversation = _tool_heavy_conversation()

    result = await _compressor(bus).ensure_capacity(conversation, [])

    assert result.compressed
    assert any(message.role == "user" for message in conversation.messages)
    carried = _only(conversation, "Standing user request")
    assert carried.role == "user"
    assert "refactor the parser" in carried.content
    # Restating it must never undo the compression that just ran.
    assert len(carried.content) < 2200


async def test_compression_does_not_restate_a_request_the_window_still_holds() -> None:
    bus = AsyncEventBus(session_id="session")
    conversation = ConversationState()
    conversation.messages.append(Message(role="system", content="system prompt"))
    for index in range(20):
        conversation.messages.append(
            Message(role="user", content=f"old message {index} " + ("x" * 200))
        )
    conversation.messages.append(Message(role="user", content="current request"))

    result = await _compressor(bus).ensure_capacity(conversation, [])

    assert result.compressed
    assert not any("Standing user request" in m.content for m in conversation.messages)
    assert conversation.messages[-1].content == "current request"


async def test_compression_keeps_images_out_of_the_restated_request() -> None:
    import base64

    bus = AsyncEventBus(session_id="session")
    conversation = _tool_heavy_conversation()
    huge = ImageContent(data=base64.b64encode(b"\x89PNG" + b"\x00" * 200_000).decode("ascii"))
    conversation.messages.insert(2, Message(role="user", content="see this", images=[huge]))

    result = await _compressor(bus).ensure_capacity(conversation, [])

    assert result.compressed
    carried = _only(conversation, "Standing user request")
    # Re-attaching the payload would inflate the context we just compressed.
    assert carried.images == []
