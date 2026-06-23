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
    _responses_input,
    normalize_responses_output_item,
)
from code_ai.providers.translation import tools_to_chat, tools_to_responses
from code_ai.tools.process import ExecuteCommandTool
from code_ai.tools.registry import ToolRegistry


class _FakeResponsesResource:
    def __init__(self, events: list[dict[str, object]]) -> None:
        self.events = events

    async def create(self, **kwargs: object):
        async def stream():
            for event in self.events:
                yield event

        return stream()


class _FakeOpenAIClient:
    def __init__(self, events: list[dict[str, object]]) -> None:
        self.responses = _FakeResponsesResource(events)


def _responses_provider(events: list[dict[str, object]]) -> OpenAIResponsesProvider:
    provider = object.__new__(OpenAIResponsesProvider)
    provider._client = _FakeOpenAIClient(events)
    provider._remote_state_supported = False
    provider._sampling_supported = False
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


def test_provider_tool_payloads_wrap_execute_command_as_function() -> None:
    registry = ToolRegistry()
    registry.register(ExecuteCommandTool())
    definition = registry.definitions({"execute_command"})[0]

    responses_payload = tools_to_responses([definition])[0]
    assert responses_payload["type"] == "function"
    assert responses_payload["name"] == "execute_command"
    assert responses_payload["parameters"]["type"] == "object"
    assert responses_payload["parameters"]["properties"]["command"]["type"] == "string"
    assert "strict" not in responses_payload
    assert "argv" not in responses_payload["parameters"]["properties"]

    chat_payload = tools_to_chat([definition])[0]
    assert chat_payload["type"] == "function"
    assert chat_payload["function"]["name"] == "execute_command"
    assert chat_payload["function"]["parameters"]["type"] == "object"
    assert chat_payload["function"]["parameters"]["properties"]["command"]["type"] == "string"
    assert "strict" not in chat_payload["function"]
    assert "argv" not in chat_payload["function"]["parameters"]["properties"]


def test_provider_tool_payloads_set_strict_when_requested() -> None:
    registry = ToolRegistry()
    registry.register(ExecuteCommandTool())
    definition = registry.definitions({"execute_command"})[0]

    responses_payload = tools_to_responses([definition], strict=True)[0]
    assert responses_payload["strict"] is True

    chat_payload = tools_to_chat([definition], strict=True)[0]
    assert chat_payload["function"]["strict"] is True


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


def test_ollama_replays_tool_calls_structurally_not_as_text() -> None:
    messages = messages_to_ollama(
        [
            Message(
                role="assistant",
                content="editing now",
                tool_calls=[
                    ToolCall(id="call_1", name="edit_code", arguments={"path": "a.py"})
                ],
            ),
        ]
    )
    assert messages[0]["role"] == "assistant"
    assert messages[0]["content"] == "editing now"
    assert messages[0]["tool_calls"] == [
        {"function": {"name": "edit_code", "arguments": {"path": "a.py"}}}
    ]


def test_responses_input_replays_tool_calls_as_function_call_items() -> None:
    import json

    items = _responses_input(
        ModelRequest(
            model="m",
            messages=[
                Message(
                    role="assistant",
                    content="editing now",
                    tool_calls=[
                        ToolCall(
                            id="call_1",
                            name="edit_code",
                            arguments={"path": "a.py", "new_text": "x=1"},
                        )
                    ],
                ),
                Message(
                    role="tool",
                    name="edit_code",
                    tool_call_id="call_1",
                    content='{"path": "a.py"}',
                ),
            ],
        )
    )
    # text first, then a structured function_call, then its output — never the
    # call serialized into visible assistant text.
    assert items[0]["role"] == "assistant"
    assert items[1]["type"] == "function_call"
    assert items[1]["call_id"] == "call_1"
    assert items[1]["name"] == "edit_code"
    assert json.loads(items[1]["arguments"]) == {"path": "a.py", "new_text": "x=1"}
    assert items[2]["type"] == "function_call_output"
    assert items[2]["call_id"] == "call_1"


def test_responses_input_types_content_by_role() -> None:
    # The Responses API discriminates content parts by role: user/system input is
    # "input_text", assistant (model output) must be "output_text". Mixing them up
    # fails strict schema validation (invalid_union) on servers like LM Studio.
    items = _responses_input(
        ModelRequest(
            model="m",
            messages=[
                Message(role="system", content="be helpful"),
                Message(role="user", content="ola"),
                Message(role="assistant", content="Olá!"),
            ],
        )
    )
    assert items[0]["content"][0]["type"] == "input_text"
    assert items[1]["content"][0]["type"] == "input_text"
    assert items[2]["content"][0]["type"] == "output_text"


def test_chat_completions_message_carries_structured_tool_calls() -> None:
    import json

    data = Message(
        role="assistant",
        content="editing",
        tool_calls=[ToolCall(id="call_1", name="edit_code", arguments={"path": "a.py"})],
    ).to_dict()
    assert data["tool_calls"][0]["id"] == "call_1"
    assert data["tool_calls"][0]["type"] == "function"
    assert data["tool_calls"][0]["function"]["name"] == "edit_code"
    assert json.loads(data["tool_calls"][0]["function"]["arguments"]) == {"path": "a.py"}


def test_ollama_public_thinking_field_is_normalized() -> None:
    assert _ollama_reasoning_delta({}, {"thinking": "checking workspace"}) == "checking workspace"
    assert _ollama_reasoning_delta({"reasoning_content": "planning"}, {}) == "planning"


async def test_responses_function_call_argument_delta_is_not_visible_text() -> None:
    provider = _responses_provider(
        [
            {
                "type": "response.function_call_arguments.delta",
                "delta": '{"command":"pwd"}',
            },
            {
                "type": "response.function_call_arguments.done",
            },
            {
                "type": "response.completed",
                "response": {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_1",
                            "name": "execute_command",
                            "arguments": '{"command":"pwd"}',
                        }
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

    assert [event.kind for event in events] == ["completed"]
    assert events[-1].response is not None
    assert events[-1].response.text == ""
    assert events[-1].response.tool_calls == [
        ToolCall(id="call_1", name="execute_command", arguments={"command": "pwd"})
    ]


def _config(**overrides):
    from code_ai.config.models import AppConfig

    config = object.__new__(AppConfig)
    config.api_mode = overrides.get("api_mode", "completions")
    config.base_url = overrides.get("base_url", "https://api.example.com/v1")
    return config


def test_models_endpoint_openai_compatible():
    from code_ai.providers.model_listing import models_endpoint

    assert (
        models_endpoint(_config(base_url="https://api.example.com/v1"))
        == "https://api.example.com/v1/models"
    )
    # A missing trailing slash must still land on /v1/models, not clobber /v1.
    assert (
        models_endpoint(_config(base_url="https://api.example.com/v1/"))
        == "https://api.example.com/v1/models"
    )


def test_models_endpoint_native_ollama():
    from code_ai.providers.model_listing import models_endpoint

    assert (
        models_endpoint(_config(api_mode="ollama", base_url="http://localhost:11434/v1"))
        == "http://localhost:11434/api/tags"
    )


def test_extract_model_ids_openai_shape():
    from code_ai.providers.model_listing import extract_model_ids

    payload = {"object": "list", "data": [{"id": "gpt-x"}, {"id": "Alpha"}, {"id": "gpt-x"}]}
    assert extract_model_ids(payload) == ["Alpha", "gpt-x"]


def test_extract_model_ids_ollama_shape():
    from code_ai.providers.model_listing import extract_model_ids

    payload = {"models": [{"name": "llama3:latest"}, {"name": "qwen:7b"}]}
    assert extract_model_ids(payload) == ["llama3:latest", "qwen:7b"]


def test_extract_model_ids_handles_empty_and_bare_strings():
    from code_ai.providers.model_listing import extract_model_ids

    assert extract_model_ids({}) == []
    assert extract_model_ids(["b", "a", "a"]) == ["a", "b"]
