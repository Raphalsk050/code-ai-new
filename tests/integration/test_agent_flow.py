from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from typing import Any

from code_ai.bootstrap import build_application
from code_ai.config.models import AppConfig
from code_ai.providers.models import (
    FinishReason,
    ModelRequest,
    ModelResponse,
    ProviderCapabilities,
    ProviderEvent,
    ToolCall,
)
from code_ai.tools.base import ToolContext
from code_ai.tools.filesystem import ListFilesTool
from code_ai.tools.internal import CompleteTaskTool, FinishDiscoveryTool
from code_ai.tools.registry import ToolRegistry


class FakeProvider:
    def __init__(self) -> None:
        self.calls = 0

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            streaming=True, tool_calling=True, provider_reported_usage=False
        )

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        self.calls += 1
        assert request.messages[0].role == "system"
        assert "Configured workspace:" in request.messages[0].content
        if self.calls == 1:
            yield ProviderEvent(
                kind="completed",
                response=ModelResponse(
                    tool_calls=[
                        ToolCall(id="call_1", name="read_file", arguments={"path": "note.txt"})
                    ],
                    finish_reason=FinishReason.TOOL_CALLS,
                ),
            )
            return
        assert any(
            message.role == "tool" and "hello" in message.content for message in request.messages
        )
        yield ProviderEvent(kind="text_delta", text_delta="done")
        yield ProviderEvent(
            kind="completed",
            response=ModelResponse(text="done", finish_reason=FinishReason.STOP),
        )

    async def complete(self, request: ModelRequest) -> ModelResponse:
        text = ""
        completed = None
        async for event in self.stream(request):
            if event.kind == "text_delta":
                text += event.text_delta
            elif event.response:
                completed = event.response
        return completed or ModelResponse(text=text)

    async def close(self) -> None:
        return None


async def test_agent_tool_loop_completes_turn(tmp_path) -> None:
    (tmp_path / "note.txt").write_text("hello\n", encoding="utf-8")
    config = AppConfig.from_mapping(
        {"api_mode": "ollama", "workspace": str(tmp_path), "model": "fake"}
    )
    provider = FakeProvider()
    app = build_application(config=config, provider=provider)
    events = []
    app.subscribe(lambda event: events.append(event.event_type))

    await app.start()
    result = await app.submit_user_message("read the note")
    await app.close()

    assert result.text == "done"
    assert provider.calls == 2
    assert "tool.call.completed" in events
    assert "turn.completed" in events


class FakeWebSearchTool:
    name = "web_search"
    description = "Search the public web for current facts."
    input_schema = {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
        "additionalProperties": True,
    }

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        self.calls.append(dict(arguments))
        return {
            "query": arguments["query"],
            "results": [
                {
                    "title": "FIFA World Cup 2026 matches today",
                    "url": "https://www.fifa.com/en/tournaments/mens/worldcup",
                    "snippet": "Official FIFA match schedule for today.",
                    "source": "test",
                }
            ],
        }


class FakeCurrentAnswerProvider:
    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            streaming=True, tool_calling=True, provider_reported_usage=False
        )

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        self.requests.append(request)
        assert any(
            message.role == "user" and "Host-executed web_search" in message.content
            for message in request.messages
        )
        yield ProviderEvent(kind="text_delta", text_delta="grounded answer")
        yield ProviderEvent(
            kind="completed",
            response=ModelResponse(text="grounded answer", finish_reason=FinishReason.STOP),
        )

    async def complete(self, request: ModelRequest) -> ModelResponse:
        async for event in self.stream(request):
            if event.response:
                return event.response
        return ModelResponse()

    async def close(self) -> None:
        return None


async def test_current_question_runs_web_search_before_model(tmp_path) -> None:
    config = AppConfig.from_mapping(
        {"api_mode": "ollama", "workspace": str(tmp_path), "model": "fake"}
    )
    provider = FakeCurrentAnswerProvider()
    web_search = FakeWebSearchTool()
    app = build_application(config=config, provider=provider)
    registry = ToolRegistry()
    registry.register(web_search)
    app.orchestrator.tool_registry = registry

    await app.start()
    result = await app.submit_user_message("quem vai jogar no jogo da copa de hoje")
    await app.close()

    assert result.text == "grounded answer"
    assert web_search.calls
    assert "hoje" in web_search.calls[0]["query"]


async def test_explicit_web_research_still_runs_web_search_before_model(tmp_path) -> None:
    config = AppConfig.from_mapping(
        {"api_mode": "ollama", "workspace": str(tmp_path), "model": "fake"}
    )
    provider = FakeCurrentAnswerProvider()
    web_search = FakeWebSearchTool()
    app = build_application(config=config, provider=provider)
    registry = ToolRegistry()
    registry.register(web_search)
    app.orchestrator.tool_registry = registry

    await app.start()
    result = await app.submit_user_message("pesquise na internet a versao atual do pytest")
    await app.close()

    assert result.text == "grounded answer"
    assert web_search.calls
    assert "pytest" in web_search.calls[0]["query"]


class FakeToolCallingProvider:
    def __init__(self) -> None:
        self.calls = 0

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            streaming=True, tool_calling=True, provider_reported_usage=False
        )

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        self.calls += 1
        if self.calls == 1:
            yield ProviderEvent(
                kind="completed",
                response=ModelResponse(
                    tool_calls=[
                        ToolCall(
                            id="call_1",
                            name="web_search",
                            arguments={"query": "copa do mundo"},
                        )
                    ],
                    finish_reason=FinishReason.TOOL_CALLS,
                ),
            )
            return
        yield ProviderEvent(
            kind="completed",
            response=ModelResponse(text="done", finish_reason=FinishReason.STOP),
        )

    async def complete(self, request: ModelRequest) -> ModelResponse:
        async for event in self.stream(request):
            if event.response:
                return event.response
        return ModelResponse()

    async def close(self) -> None:
        return None


async def test_web_search_tool_call_inherits_recent_current_context(tmp_path) -> None:
    config = AppConfig.from_mapping(
        {"api_mode": "ollama", "workspace": str(tmp_path), "model": "fake"}
    )
    provider = FakeToolCallingProvider()
    web_search = FakeWebSearchTool()
    app = build_application(config=config, provider=provider)
    registry = ToolRegistry()
    registry.register(web_search)
    app.orchestrator.tool_registry = registry

    await app.start()
    app.orchestrator.conversation.add_user("quem vai jogar no jogo da copa de hoje")
    result = await app.submit_user_message("copa do mundo")
    await app.close()

    assert result.text == "done"
    assert web_search.calls
    query = web_search.calls[0]["query"]
    assert "quem vai jogar no jogo da copa de hoje" in query
    assert "Copa do Mundo FIFA 2026" in query


class FakeCodeBlockThenToolsProvider:
    def __init__(self, workspace: Any) -> None:
        self.calls = 0
        self.workspace = workspace

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            streaming=True, tool_calling=True, provider_reported_usage=False
        )

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        self.calls += 1
        if self.calls == 1:
            text = "```python\ndef answer():\n    return 42\n```"
            yield ProviderEvent(kind="text_delta", text_delta=text)
            yield ProviderEvent(
                kind="completed",
                response=ModelResponse(text=text, finish_reason=FinishReason.STOP),
            )
            return
        if self.calls == 2:
            yield ProviderEvent(
                kind="completed",
                response=ModelResponse(
                    tool_calls=[
                        ToolCall(
                            id="write_1",
                            name="write_file",
                            arguments={
                                "path": "src/example.py",
                                "content": "def answer():\n    return 42\n",
                                "expected_new_file": True,
                                "create_dirs": True,
                            },
                        )
                    ],
                    finish_reason=FinishReason.TOOL_CALLS,
                ),
            )
            return
        if self.calls == 3:
            yield ProviderEvent(
                kind="completed",
                response=ModelResponse(
                    tool_calls=[
                        ToolCall(
                            id="verify_1",
                            name="execute_command",
                            arguments={
                                "argv": [
                                    sys.executable,
                                    "-c",
                                    "from src.example import answer; assert answer() == 42",
                                ],
                                "timeout": 10,
                            },
                        )
                    ],
                    finish_reason=FinishReason.TOOL_CALLS,
                ),
            )
            return
        completion_args = {
            "outcome": "success",
            "summary": "Created src/example.py and verified answer().",
            "acceptance_evidence": {
                "file created": ["write_1"],
                "verification": ["verify_1"],
            },
            "verification_summary": "Python import assertion passed.",
            "changed_paths": ["src/example.py"],
            "double_check_acknowledged": self.calls >= 5,
        }
        yield ProviderEvent(
            kind="completed",
            response=ModelResponse(
                tool_calls=[
                    ToolCall(
                        id=f"complete_{self.calls}",
                        name="complete_task",
                        arguments=completion_args,
                    )
                ],
                finish_reason=FinishReason.TOOL_CALLS,
            ),
        )

    async def complete(self, request: ModelRequest) -> ModelResponse:
        async for event in self.stream(request):
            if event.response:
                return event.response
        return ModelResponse()

    async def close(self) -> None:
        return None


async def test_mutation_task_rejects_code_block_until_tools_verify_and_complete(tmp_path) -> None:
    config = AppConfig.from_mapping(
        {"api_mode": "ollama", "workspace": str(tmp_path), "model": "fake"}
    )
    provider = FakeCodeBlockThenToolsProvider(tmp_path)
    app = build_application(config=config, provider=provider)
    events = []
    app.subscribe(lambda event: events.append(event.event_type))

    await app.start()
    result = await app.submit_user_message(
        "Create src/example.py with a function that returns 42 and test it."
    )
    await app.close()

    assert (tmp_path / "src" / "example.py").read_text(encoding="utf-8") == (
        "def answer():\n    return 42\n"
    )
    assert "Created src/example.py" in result.text
    assert "agent.corrective_prompt.injected" in events
    assert "planning.completion.rejected" in events
    assert "assistant.final" in events


class FakeEarlyWebForLocalBugProvider:
    def __init__(self) -> None:
        self.calls = 0

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            streaming=True, tool_calling=True, provider_reported_usage=False
        )

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        self.calls += 1
        if self.calls == 1:
            yield ProviderEvent(
                kind="completed",
                response=ModelResponse(
                    tool_calls=[
                        ToolCall(
                            id="web_1",
                            name="web_search",
                            arguments={"query": "authentication bug examples"},
                        )
                    ],
                    finish_reason=FinishReason.TOOL_CALLS,
                ),
            )
            return
        yield ProviderEvent(
            kind="completed",
            response=ModelResponse(
                tool_calls=[
                    ToolCall(
                        id="blocked_1",
                        name="complete_task",
                        arguments={
                            "outcome": "blocked",
                            "summary": "Blocked after early web search was denied.",
                            "remaining_issues": ["Need local inspection first."],
                        },
                    )
                ],
                finish_reason=FinishReason.TOOL_CALLS,
            ),
        )

    async def complete(self, request: ModelRequest) -> ModelResponse:
        async for event in self.stream(request):
            if event.response:
                return event.response
        return ModelResponse()

    async def close(self) -> None:
        return None


async def test_workspace_bug_denies_early_web_search_without_backend_call(tmp_path) -> None:
    config = AppConfig.from_mapping(
        {"api_mode": "ollama", "workspace": str(tmp_path), "model": "fake"}
    )
    provider = FakeEarlyWebForLocalBugProvider()
    web_search = FakeWebSearchTool()
    app = build_application(config=config, provider=provider)
    registry = ToolRegistry()
    registry.register(ListFilesTool())
    registry.register(web_search)
    registry.register(CompleteTaskTool())
    app.orchestrator.tool_registry = registry
    events = []
    app.subscribe(lambda event: events.append(event.event_type))

    await app.start()
    result = await app.submit_user_message("Fix the authentication bug in this repository.")
    await app.close()

    assert "Blocked after early web search was denied" in result.text
    assert web_search.calls == []
    assert "planning.policy.denied" in events


class FakeGenericGapThenWebProvider:
    def __init__(self) -> None:
        self.calls = 0
        self.requests: list[ModelRequest] = []

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            streaming=True, tool_calling=True, provider_reported_usage=False
        )

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        self.calls += 1
        self.requests.append(request)
        tool_names = {tool.name for tool in request.tools}
        assert "web_search" not in tool_names
        if self.calls == 1:
            yield ProviderEvent(
                kind="completed",
                response=ModelResponse(
                    tool_calls=[
                        ToolCall(
                            id="finish_1",
                            name="finish_discovery",
                            arguments={
                                "summary": "The configured workspace was listed.",
                                "external_knowledge_gaps": [
                                    {
                                        "question": (
                                            "Which public repository should this project map to?"
                                        ),
                                        "why_local_files_are_insufficient": (
                                            "Need external evidence because local files are "
                                            "insufficient."
                                        ),
                                        "decision_depends_on": (
                                            "External information from the public web."
                                        ),
                                    }
                                ],
                            },
                        )
                    ],
                    finish_reason=FinishReason.TOOL_CALLS,
                ),
            )
            return
        if self.calls == 2:
            yield ProviderEvent(
                kind="completed",
                response=ModelResponse(
                    tool_calls=[
                        ToolCall(
                            id="web_1",
                            name="web_search",
                            arguments={"query": "test_agent GitHub project"},
                        )
                    ],
                    finish_reason=FinishReason.TOOL_CALLS,
                ),
            )
            return
        assert any(
            message.role == "tool"
            and message.name == "web_search"
            and "Tool policy denied" in message.content
            for message in request.messages
        )
        yield ProviderEvent(
            kind="completed",
            response=ModelResponse(
                text="O workspace local foi inspecionado; nao usei web_search.",
                finish_reason=FinishReason.STOP,
            ),
        )

    async def complete(self, request: ModelRequest) -> ModelResponse:
        async for event in self.stream(request):
            if event.response:
                return event.response
        return ModelResponse()

    async def close(self) -> None:
        return None


async def test_local_project_today_rejects_generic_gap_and_web_search(tmp_path) -> None:
    config = AppConfig.from_mapping(
        {"api_mode": "ollama", "workspace": str(tmp_path), "model": "fake"}
    )
    provider = FakeGenericGapThenWebProvider()
    web_search = FakeWebSearchTool()
    app = build_application(config=config, provider=provider)
    registry = ToolRegistry()
    registry.register(ListFilesTool())
    registry.register(FinishDiscoveryTool())
    registry.register(web_search)
    app.orchestrator.tool_registry = registry
    events = []
    app.subscribe(lambda event: events.append(event.event_type))

    await app.start()
    result = await app.submit_user_message("O que temos no projeto hoje?")
    await app.close()

    assert "workspace local" in result.text
    assert web_search.calls == []
    assert "planning.external_gap.rejected" in events
    assert "planning.policy.denied" in events


async def test_plan_mode_policy_bypass_rejects_direct_write_tool(tmp_path) -> None:
    config = AppConfig.from_mapping(
        {"api_mode": "ollama", "workspace": str(tmp_path), "model": "fake"}
    )
    provider = FakeProvider()
    app = build_application(config=config, provider=provider)
    events = []
    app.subscribe(lambda event: events.append(event.event_type))

    await app.start()
    await app.orchestrator.planner.set_mode("plan")
    await app.orchestrator.planner.begin_turn("Create a.txt", provider_supports_tools=True)
    result = await app.orchestrator._execute_tool(
        "bypass_1",
        "write_file",
        {"path": "a.txt", "content": "x\n"},
        None,
    )
    await app.close()

    assert result.is_error
    assert not (tmp_path / "a.txt").exists()
    assert "planning.policy.denied" in events


class FakeDirectGreetingProvider:
    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            streaming=True, tool_calling=True, provider_reported_usage=False
        )

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        self.requests.append(request)
        assert request.tools == []
        assert not any(
            "Runtime task state" in message.content for message in request.messages
        )
        yield ProviderEvent(kind="text_delta", text_delta="Olá! Como posso ajudar?")
        yield ProviderEvent(
            kind="completed",
            response=ModelResponse(
                text="Olá! Como posso ajudar?",
                finish_reason=FinishReason.STOP,
            ),
        )

    async def complete(self, request: ModelRequest) -> ModelResponse:
        async for event in self.stream(request):
            if event.response:
                return event.response
        return ModelResponse()

    async def close(self) -> None:
        return None


async def test_direct_greeting_does_not_expose_tools_or_require_complete_task(tmp_path) -> None:
    config = AppConfig.from_mapping(
        {"api_mode": "ollama", "workspace": str(tmp_path), "model": "fake"}
    )
    provider = FakeDirectGreetingProvider()
    app = build_application(config=config, provider=provider)
    events = []
    app.subscribe(lambda event: events.append(event.event_type))

    await app.start()
    result = await app.submit_user_message("Olá")
    await app.close()

    assert result.text == "Olá! Como posso ajudar?"
    assert provider.requests
    assert "tool.call.requested" not in events
