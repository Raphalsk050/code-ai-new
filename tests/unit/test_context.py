from __future__ import annotations

from code_ai.context.compression import ContextCompressor
from code_ai.context.conversation import ConversationState
from code_ai.context.token_counting import TokenCounter
from code_ai.events.bus import AsyncEventBus
from code_ai.providers.models import Message, ToolCall


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
