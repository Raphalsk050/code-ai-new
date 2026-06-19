from __future__ import annotations

from code_ai.providers.models import Message, ToolCall
from code_ai.providers.ollama import (
    _ollama_reasoning_delta,
    _ollama_usage,
    messages_to_ollama,
    normalize_native_ollama_base_url,
)
from code_ai.providers.openai_completions import assemble_streamed_tool_call_fragments
from code_ai.providers.openai_responses import normalize_responses_output_item


def test_chat_completions_streamed_tool_argument_assembly() -> None:
    calls = assemble_streamed_tool_call_fragments(
        [
            {"index": 0, "id": "call_", "function": {"name": "read_", "arguments": '{"path"'}},
            {"index": 0, "id": "1", "function": {"name": "file", "arguments": ': "a.txt"}'}},
        ]
    )
    assert calls == [ToolCall(id="call_1", name="read_file", arguments={"path": "a.txt"})]


def test_responses_output_item_tool_call_normalization() -> None:
    item = {
        "type": "function_call",
        "call_id": "abc",
        "name": "system_information",
        "arguments": '{"commands": ["python"]}',
    }
    normalized = normalize_responses_output_item(item)
    assert isinstance(normalized, ToolCall)
    assert normalized.arguments == {"commands": ["python"]}


def test_ollama_base_url_and_usage_normalization() -> None:
    assert (
        normalize_native_ollama_base_url("http://localhost:11434/v1") == "http://localhost:11434/"
    )
    usage = _ollama_usage({"prompt_eval_count": 10, "eval_count": 7})
    assert usage is not None
    assert usage.total_tokens == 17
    assert usage.exact


def test_ollama_tool_results_are_visible_as_text_messages() -> None:
    messages = messages_to_ollama(
        [
            Message(role="system", content="system"),
            Message(
                role="tool",
                name="execute_command",
                tool_call_id="call_1",
                content='{"stdout": "/workspace\\n", "cwd": "/workspace"}',
            ),
        ]
    )
    assert messages[1]["role"] == "user"
    assert "Tool result from execute_command" in messages[1]["content"]
    assert "/workspace" in messages[1]["content"]


def test_ollama_public_thinking_field_is_normalized() -> None:
    assert _ollama_reasoning_delta({}, {"thinking": "checking workspace"}) == "checking workspace"
    assert _ollama_reasoning_delta({"reasoning_content": "planning"}, {}) == "planning"
