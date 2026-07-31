from __future__ import annotations

import json

import pytest

from code_ai.config.models import AppConfig
from code_ai.providers.models import (
    ImageContent,
    Message,
    ModelRequest,
    ProviderCapabilities,
    ToolCall,
)
from code_ai.providers.ollama import (
    NativeOllamaProvider,
    _ollama_reasoning_delta,
    _ollama_usage,
    messages_to_ollama,
    normalize_native_ollama_base_url,
)
from code_ai.providers.openai_completions import (
    OpenAIChatCompletionsProvider,
    assemble_streamed_tool_call_fragments,
)
from code_ai.providers.openai_responses import (
    OpenAIResponsesProvider,
    _responses_input,
    normalize_responses_output_item,
)
from code_ai.providers.translation import parse_arguments, tools_to_chat, tools_to_responses
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
    provider._config = AppConfig()
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


class _FakeChatCompletionsResource:
    def __init__(self, chunks: list[dict[str, object]]) -> None:
        self._chunks = chunks

    async def create(self, **kwargs: object):
        async def stream():
            for chunk in self._chunks:
                yield chunk

        return stream()


class _FakeChatResource:
    def __init__(self, chunks: list[dict[str, object]]) -> None:
        self.completions = _FakeChatCompletionsResource(chunks)


class _FakeChatClient:
    def __init__(self, chunks: list[dict[str, object]]) -> None:
        self.chat = _FakeChatResource(chunks)


def _chat_provider(chunks: list[dict[str, object]]) -> OpenAIChatCompletionsProvider:
    provider = object.__new__(OpenAIChatCompletionsProvider)
    provider._client = _FakeChatClient(chunks)
    provider._config = AppConfig()
    provider._stream_options_supported = False
    provider._sampling_supported = False
    provider._capabilities = ProviderCapabilities()
    return provider


def _tool_call_chunk(arguments: str) -> dict[str, object]:
    return {
        "usage": None,
        "choices": [
            {
                "finish_reason": "tool_calls",
                "delta": {
                    "content": "",
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call_1",
                            "function": {"name": "create_skill", "arguments": arguments},
                        }
                    ],
                },
            }
        ],
    }


def _chat_request() -> ModelRequest:
    return ModelRequest(model="test-model", messages=[Message(role="user", content="hi")])


async def test_chat_completions_recovers_tool_call_with_trailing_extra_data() -> None:
    # qwen via ollama emitted a valid arguments object followed by stray tokens.
    # A bare json.loads raised "Extra data: ..." and took the whole session down.
    arguments = '{"name": "appropriate-abstraction", "overwrite": true}{"name"'
    provider = _chat_provider([_tool_call_chunk(arguments)])

    events = [event async for event in provider.stream(_chat_request())]

    assert all(event.kind != "warning" for event in events)
    completed = events[-1]
    assert completed.kind == "completed"
    assert completed.response is not None
    assert completed.response.tool_calls == [
        ToolCall(
            id="call_1",
            name="create_skill",
            arguments={"name": "appropriate-abstraction", "overwrite": True},
        )
    ]


async def test_chat_completions_drops_unparseable_tool_call_without_failing() -> None:
    # Truncated arguments are unrecoverable; the call is dropped with a warning
    # rather than crashing the session with a fatal "request failed".
    arguments = '{"name": "x", "instructions": "# big'
    provider = _chat_provider([_tool_call_chunk(arguments)])

    events = [event async for event in provider.stream(_chat_request())]

    warnings = [event for event in events if event.kind == "warning"]
    assert warnings and "create_skill" in str(warnings[0].warning)
    completed = events[-1]
    assert completed.kind == "completed"
    assert completed.response is not None
    assert completed.response.tool_calls == []


def _tool_call_chunk_fragment(name: str, arguments: str) -> dict[str, object]:
    return {
        "usage": None,
        "choices": [
            {
                "finish_reason": None,
                "delta": {
                    "content": "",
                    "tool_calls": [
                        {"index": 0, "id": "", "function": {"name": name, "arguments": arguments}}
                    ],
                },
            }
        ],
    }


async def test_chat_completions_streams_tool_call_argument_deltas() -> None:
    # While write_file's content streams in, the provider must surface partial
    # progress so the UI isn't frozen until the whole call has arrived.
    provider = _chat_provider(
        [
            _tool_call_chunk_fragment("write_file", '{"path": "a.py", "content": "'),
            _tool_call_chunk_fragment("", 'print(1)\\nprint(2)"}'),
        ]
    )

    events = [event async for event in provider.stream(_chat_request())]

    deltas = [event for event in events if event.kind == "tool_call_delta"]
    assert len(deltas) >= 2
    assert deltas[-1].tool_call_name == "write_file"
    assert deltas[-1].tool_call_arguments.endswith('print(2)"}')
    assert len(deltas[-1].tool_call_arguments) > len(deltas[0].tool_call_arguments)
    completed = events[-1]
    assert completed.kind == "completed"
    assert completed.response is not None
    assert completed.response.tool_calls[0].name == "write_file"


async def test_responses_streams_tool_call_argument_deltas() -> None:
    events_in: list[dict[str, object]] = [
        {
            "type": "response.output_item.added",
            "item": {"type": "function_call", "id": "fc_1", "name": "edit_code"},
        },
        {
            "type": "response.function_call_arguments.delta",
            "item_id": "fc_1",
            "delta": '{"path": "a.py",',
        },
        {
            "type": "response.function_call_arguments.delta",
            "item_id": "fc_1",
            "delta": ' "content": "x"}',
        },
        {
            "type": "response.completed",
            "response": {
                "id": "r1",
                "output": [
                    {
                        "type": "function_call",
                        "call_id": "fc_1",
                        "name": "edit_code",
                        "arguments": '{"path": "a.py", "content": "x"}',
                    }
                ],
            },
        },
    ]
    provider = _responses_provider(events_in)

    events = [event async for event in provider.stream(_chat_request())]

    deltas = [event for event in events if event.kind == "tool_call_delta"]
    assert len(deltas) == 2
    assert deltas[0].tool_call_name == "edit_code"
    assert deltas[-1].tool_call_arguments == '{"path": "a.py", "content": "x"}'


def test_parse_arguments_tolerates_trailing_extra_data() -> None:
    assert parse_arguments('{"a": 1}{"b": 2}') == {"a": 1}


def test_parse_arguments_blank_string_is_empty_object() -> None:
    assert parse_arguments("   ") == {}


def test_parse_arguments_truncated_object_raises() -> None:
    with pytest.raises(ValueError):
        parse_arguments('{"a": 1')


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


class _FakeStreamResponse:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    def raise_for_status(self) -> None:
        return None

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _FakeStreamContext:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    async def __aenter__(self) -> _FakeStreamResponse:
        return _FakeStreamResponse(self._lines)

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _FakeHttpxClient:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    def stream(self, *args: object, **kwargs: object) -> _FakeStreamContext:
        return _FakeStreamContext(self._lines)


def _ollama_provider(lines: list[str]) -> NativeOllamaProvider:
    provider = object.__new__(NativeOllamaProvider)
    provider._client = _FakeHttpxClient(lines)
    provider._config = AppConfig()
    provider._base_url = "http://localhost:11434/"
    provider._capabilities = ProviderCapabilities(streaming=True, tool_calling=True)
    return provider


async def test_ollama_announces_each_whole_tool_call() -> None:
    # The native API hands a tool call over in one piece, so the live code view
    # has this single chance to learn what is about to be written.
    lines = [
        json.dumps({"message": {"content": "writing it now"}}),
        json.dumps(
            {
                "message": {
                    "tool_calls": [
                        {
                            "function": {
                                "name": "write_file",
                                "arguments": {"path": "a.py", "content": "x = 1\n"},
                            }
                        }
                    ]
                },
                "done": True,
            }
        ),
    ]
    provider = _ollama_provider(lines)

    events = [
        event
        async for event in provider.stream(
            ModelRequest(model="fake", messages=[Message(role="user", content="go")])
        )
    ]

    deltas = [event for event in events if event.kind == "tool_call_delta"]
    assert len(deltas) == 1
    assert deltas[0].tool_call_name == "write_file"
    assert json.loads(deltas[0].tool_call_arguments) == {"path": "a.py", "content": "x = 1\n"}
    assert events[-1].response.tool_calls[0].name == "write_file"


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


def test_ollama_user_message_carries_images_as_raw_base64() -> None:
    messages = messages_to_ollama(
        [
            Message(
                role="user",
                content="what is in this screenshot? [Image #1]",
                images=[ImageContent(data="aGVsbG8=")],
            )
        ]
    )
    assert messages[0]["images"] == ["aGVsbG8="]
    assert messages[0]["content"] == "what is in this screenshot? [Image #1]"


def test_chat_completions_message_with_images_is_multipart() -> None:
    data = Message(
        role="user",
        content="describe [Image #1]",
        images=[ImageContent(data="aGVsbG8=")],
    ).to_dict()
    assert data["content"] == [
        {"type": "text", "text": "describe [Image #1]"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,aGVsbG8="}},
    ]


def test_chat_completions_message_without_images_keeps_plain_content() -> None:
    assert Message(role="user", content="ola").to_dict()["content"] == "ola"


def test_responses_input_carries_images_as_input_image_parts() -> None:
    items = _responses_input(
        ModelRequest(
            model="m",
            messages=[
                Message(
                    role="user",
                    content="describe [Image #1]",
                    images=[ImageContent(data="aGVsbG8=")],
                )
            ],
        )
    )
    assert items[0]["content"] == [
        {"type": "input_text", "text": "describe [Image #1]"},
        {"type": "input_image", "image_url": "data:image/png;base64,aGVsbG8="},
    ]


def test_responses_input_emits_image_only_messages() -> None:
    # A message whose whole content is the image must not be silently dropped.
    items = _responses_input(
        ModelRequest(
            model="m",
            messages=[Message(role="user", content="", images=[ImageContent(data="aGVsbG8=")])],
        )
    )
    assert items[0]["content"] == [
        {"type": "input_image", "image_url": "data:image/png;base64,aGVsbG8="}
    ]


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

    kinds = [event.kind for event in events]
    # Argument deltas must never pollute the chat as visible answer text; they
    # are surfaced only as tool-call progress.
    assert "text_delta" not in kinds
    assert "tool_call_delta" in kinds
    assert kinds[-1] == "completed"
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


def test_normalize_chat_messages_is_a_noop_for_a_wellformed_request() -> None:
    from code_ai.providers.translation import normalize_chat_messages

    messages = [
        Message(role="system", content="system prompt"),
        Message(role="user", content="hi"),
        Message(role="assistant", content="hello"),
    ]

    # Same list object: the common path must not rebuild anything.
    assert normalize_chat_messages(messages) is messages


def test_normalize_chat_messages_folds_a_stray_system_message_into_the_leading_one() -> None:
    from code_ai.providers.translation import normalize_chat_messages

    normalized = normalize_chat_messages(
        [
            Message(role="system", content="system prompt"),
            Message(role="system", content="compressed summary"),
            Message(role="user", content="carry on"),
        ]
    )

    # Chat templates raise "System message must be at the beginning." for a
    # system message anywhere but index 0, and the engine turns that into a 400
    # for the whole request.
    assert [m.role for m in normalized] == ["system", "user"]
    assert normalized[0].content == "system prompt\n\ncompressed summary"


def test_normalize_chat_messages_adds_a_user_turn_when_none_survives() -> None:
    from code_ai.providers.translation import normalize_chat_messages

    normalized = normalize_chat_messages(
        [
            Message(role="system", content="system prompt"),
            Message(role="assistant", content="reading a file", tool_calls=[]),
            Message(role="tool", content="file contents", tool_call_id="c1", name="read_file"),
        ]
    )

    # Otherwise: "No user query found in messages." — an unrecoverable 400.
    assert normalized[-1].role == "user"
    assert normalized[-1].content
    assert [m.role for m in normalized[:-1]] == ["system", "assistant", "tool"]


def test_normalize_chat_messages_counts_ollama_tool_results_as_user_turns() -> None:
    from code_ai.providers.translation import normalize_chat_messages

    messages = [
        Message(role="system", content="system prompt"),
        Message(role="tool", content="file contents", tool_call_id="c1", name="read_file"),
    ]

    # messages_to_ollama replays tool results as user turns, so nothing is
    # missing and the list must be handed over untouched.
    assert normalize_chat_messages(messages, tool_results_are_user_turns=True) is messages
    assert normalize_chat_messages(messages)[-1].role == "user"


def test_normalize_chat_messages_leaves_an_empty_request_alone() -> None:
    from code_ai.providers.translation import normalize_chat_messages

    assert normalize_chat_messages([]) == []


async def test_chat_completions_payload_never_carries_a_misplaced_system_message() -> None:
    provider = _chat_provider([])
    sent: dict[str, object] = {}
    original = provider._client.chat.completions.create

    async def _record(**kwargs: object):
        sent.update(kwargs)
        return await original(**kwargs)

    provider._client.chat.completions.create = _record
    request = ModelRequest(
        model="test-model",
        messages=[
            Message(role="system", content="system prompt"),
            Message(role="system", content="Compressed context summary"),
            Message(role="assistant", content="working"),
            Message(role="tool", content="result", tool_call_id="c1", name="read_file"),
        ],
    )

    [event async for event in provider.stream(request)]

    roles = [message["role"] for message in sent["messages"]]
    assert roles.count("system") == 1
    assert roles[0] == "system"
    assert "user" in roles


async def test_ollama_payload_never_carries_a_misplaced_system_message() -> None:
    from code_ai.providers.translation import normalize_chat_messages

    messages = messages_to_ollama(
        normalize_chat_messages(
            [
                Message(role="system", content="system prompt"),
                Message(role="system", content="Compressed context summary"),
                Message(role="user", content="carry on"),
            ],
            tool_results_are_user_turns=True,
        )
    )

    roles = [message["role"] for message in messages]
    assert roles.count("system") == 1
    assert roles[0] == "system"
