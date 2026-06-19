from __future__ import annotations

from code_ai.context.compression import ContextCompressor
from code_ai.context.conversation import ConversationState
from code_ai.context.token_counting import TokenCounter
from code_ai.events.bus import AsyncEventBus
from code_ai.providers.models import Message


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
