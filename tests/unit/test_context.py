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
