from __future__ import annotations

from code_ai.providers.models import Message, ModelRequest, ProviderCapabilities, ToolCall
from code_ai.providers.ollama import (
    _ollama_reasoning_delta,
    _ollama_usage,
    messages_to_ollama,
    normalize_native_ollama_base_url,
)
from code_ai.providers.openai_completions import assemble_streamed_tool_call_fragments
from code_ai.providers.openai_responses import (
    OpenAIResponsesProvider,
    normalize_responses_output_item,
)


class _FakeResponsesResource:
    def __init__(
        self,
        events: list[dict[str, object]],
        failures: list[Exception] | None = None,
    ) -> None:
        self.events = events
        self.failures = failures or []
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs: object):
        self.calls.append(kwargs)
        if self.failures:
            raise self.failures.pop(0)

        async def stream():
            for event in self.events:
                yield event

        return stream()


class _FakeOpenAIClient:
    def __init__(
        self,
        events: list[dict[str, object]],
        failures: list[Exception] | None = None,
    ) -> None:
        self.responses = _FakeResponsesResource(events, failures)


def _responses_provider(
    events: list[dict[str, object]],
    failures: list[Exception] | None = None,
) -> OpenAIResponsesProvider:
    provider = object.__new__(OpenAIResponsesProvider)
    provider._client = _FakeOpenAIClient(events, failures)
    provider._remote_state_supported = False
    provider._reasoning_summary_supported = True
    provider._capabilities = ProviderCapabilities(remote_conversation_state=False)
    return provider


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


async def test_responses_reasoning_delta_streams_separately_from_answer_text() -> None:
    provider = _responses_provider(
        [
            {
                "type": "response.reasoning_summary_text.delta",
                "delta": "checking facts",
            },
            {
                "type": "response.output_text.delta",
                "delta": "final answer",
            },
            {
                "type": "response.completed",
                "response": {"id": "resp_1", "output": []},
            },
        ]
    )

    events = [
        event
        async for event in provider.stream(
            ModelRequest(model="test-model", messages=[Message(role="user", content="hi")])
        )
    ]

    assert [event.kind for event in events] == ["reasoning_delta", "text_delta", "completed"]
    assert events[0].reasoning_delta == "checking facts"
    assert events[1].text_delta == "final answer"
    assert events[-1].response is not None
    assert events[-1].response.text == "final answer"
    assert events[-1].response.reasoning == "checking facts"


async def test_responses_requests_public_reasoning_summary_by_default() -> None:
    provider = _responses_provider(
        [
            {
                "type": "response.completed",
                "response": {"output": [{"type": "message", "content": "done"}]},
            },
        ]
    )

    events = [
        event
        async for event in provider.stream(
            ModelRequest(model="test-model", messages=[Message(role="user", content="hi")])
        )
    ]

    assert events[-1].response is not None
    assert provider._client.responses.calls[0]["reasoning"] == {
        "effort": "low",
        "summary": "auto",
    }


async def test_responses_retries_without_reasoning_when_endpoint_rejects_it() -> None:
    provider = _responses_provider(
        [
            {
                "type": "response.completed",
                "response": {"output": [{"type": "message", "content": "done"}]},
            },
        ],
        failures=[ValueError("unsupported reasoning parameter")],
    )

    events = [
        event
        async for event in provider.stream(
            ModelRequest(model="test-model", messages=[Message(role="user", content="hi")])
        )
    ]

    assert [event.kind for event in events] == ["warning", "completed"]
    assert provider._client.responses.calls[0]["reasoning"] == {
        "effort": "low",
        "summary": "auto",
    }
    assert "reasoning" not in provider._client.responses.calls[1]
    assert provider._reasoning_summary_supported is False


async def test_responses_metadata_thinking_streams_as_reasoning_delta() -> None:
    provider = _responses_provider(
        [
            {
                "type": "response.in_progress",
                "metadata": {"thinking": "planning with metadata"},
            },
            {
                "type": "response.completed",
                "response": {"output": [{"type": "message", "content": "done"}]},
            },
        ]
    )

    events = [
        event
        async for event in provider.stream(
            ModelRequest(model="test-model", messages=[Message(role="user", content="hi")])
        )
    ]

    assert events[0].kind == "reasoning_delta"
    assert events[0].reasoning_delta == "planning with metadata"
    assert events[-1].response is not None
    assert events[-1].response.text == "done"
    assert events[-1].response.reasoning == "planning with metadata"


async def test_responses_completed_reasoning_item_is_emitted_when_stream_has_no_delta() -> None:
    provider = _responses_provider(
        [
            {
                "type": "response.completed",
                "response": {
                    "output": [
                        {
                            "type": "reasoning",
                            "summary": [
                                {
                                    "type": "summary_text",
                                    "text": "looked up current fixtures",
                                }
                            ],
                        },
                        {
                            "type": "message",
                            "content": [{"type": "output_text", "text": "answer"}],
                        },
                    ]
                },
            },
        ]
    )

    events = [
        event
        async for event in provider.stream(
            ModelRequest(model="test-model", messages=[Message(role="user", content="hi")])
        )
    ]

    assert [event.kind for event in events] == ["reasoning_delta", "completed"]
    assert events[0].reasoning_delta == "looked up current fixtures"
    assert events[-1].response is not None
    assert events[-1].response.text == "answer"
    assert events[-1].response.reasoning == "looked up current fixtures"


async def test_responses_completed_metadata_thinking_still_emits_completion() -> None:
    provider = _responses_provider(
        [
            {
                "type": "response.completed",
                "response": {
                    "metadata": {"thinking": "metadata summary"},
                    "output": [{"type": "message", "content": "done"}],
                },
            },
        ]
    )

    events = [
        event
        async for event in provider.stream(
            ModelRequest(model="test-model", messages=[Message(role="user", content="hi")])
        )
    ]

    assert [event.kind for event in events] == ["reasoning_delta", "completed"]
    assert events[0].reasoning_delta == "metadata summary"
    assert events[-1].response is not None
    assert events[-1].response.text == "done"
    assert events[-1].response.reasoning == "metadata summary"
