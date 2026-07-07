from __future__ import annotations

import shlex
import sys
import time
from collections.abc import AsyncIterator
from typing import Any

from code_ai.bootstrap import build_application
from code_ai.config.models import AppConfig
from code_ai.core.orchestration import _TurnState
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
from code_ai.tools.internal import (
    CompleteTaskTool,
    FinishDiscoveryTool,
    RequestExternalGapTool,
)
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


class FakeWebSearchThenAnswerProvider:
    """Model that decides to call web_search itself, then answers from the result."""

    def __init__(self, query: str) -> None:
        self.query = query
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
                        ToolCall(id="web_1", name="web_search", arguments={"query": self.query})
                    ],
                    finish_reason=FinishReason.TOOL_CALLS,
                ),
            )
            return
        assert any(
            message.role == "tool" and message.name == "web_search"
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


async def test_current_question_lets_model_run_web_search(tmp_path) -> None:
    config = AppConfig.from_mapping(
        {"api_mode": "ollama", "workspace": str(tmp_path), "model": "fake"}
    )
    provider = FakeWebSearchThenAnswerProvider("quem vai jogar no jogo da copa de hoje")
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


async def test_explicit_web_research_lets_model_run_web_search(tmp_path) -> None:
    config = AppConfig.from_mapping(
        {"api_mode": "ollama", "workspace": str(tmp_path), "model": "fake"}
    )
    provider = FakeWebSearchThenAnswerProvider("versao atual do pytest")
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


async def test_web_search_tool_call_passes_model_query_unchanged(tmp_path) -> None:
    config = AppConfig.from_mapping(
        {"api_mode": "ollama", "workspace": str(tmp_path), "model": "fake"}
    )
    provider = FakeWebSearchThenAnswerProvider("copa do mundo")
    web_search = FakeWebSearchTool()
    app = build_application(config=config, provider=provider)
    registry = ToolRegistry()
    registry.register(web_search)
    app.orchestrator.tool_registry = registry

    await app.start()
    result = await app.submit_user_message("pesquise copa do mundo na internet")
    await app.close()

    assert result.text == "grounded answer"
    assert web_search.calls
    # The host no longer rewrites the model's query (no locale-specific enrichment).
    assert web_search.calls[0]["query"] == "copa do mundo"


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
                                "command": (
                                    f"{shlex.quote(sys.executable)} -c "
                                    "'from src.example import answer; assert answer() == 42'"
                                ),
                                "timeout": 10,
                            },
                        )
                    ],
                    finish_reason=FinishReason.TOOL_CALLS,
                ),
            )
            return
        completion_args = {
            "summary": "Created src/example.py and verified answer().",
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
        {
            "api_mode": "ollama",
            "workspace": str(tmp_path),
            "model": "fake",
            "permission_mode": "bypass",
        }
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
                            "summary": "Blocked after early web search.",
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


async def test_workspace_bug_allows_web_search_under_advisory_policy(tmp_path) -> None:
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

    assert "Blocked after early web search" in result.text
    # Advisory policy keeps web_search callable instead of hard-denying it.
    assert web_search.calls
    assert "planning.policy.denied" not in events


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
                            id="gap_1",
                            name="request_external_gap",
                            arguments={
                                "question": "Which public repository should this project map to?",
                                "reason": (
                                    "Need external evidence because local files are insufficient."
                                ),
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
            message.role == "tool" and message.name == "web_search"
            for message in request.messages
        )
        yield ProviderEvent(
            kind="completed",
            response=ModelResponse(
                text="O projeto local foi inspecionado com evidencia da busca.",
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


async def test_local_project_today_rejects_generic_gap_but_allows_web(tmp_path) -> None:
    config = AppConfig.from_mapping(
        {"api_mode": "ollama", "workspace": str(tmp_path), "model": "fake"}
    )
    provider = FakeGenericGapThenWebProvider()
    web_search = FakeWebSearchTool()
    app = build_application(config=config, provider=provider)
    registry = ToolRegistry()
    registry.register(ListFilesTool())
    registry.register(FinishDiscoveryTool())
    registry.register(RequestExternalGapTool())
    registry.register(web_search)
    app.orchestrator.tool_registry = registry
    events = []
    app.subscribe(lambda event: events.append(event.event_type))

    await app.start()
    result = await app.submit_user_message("O que temos no projeto hoje?")
    await app.close()

    assert "projeto local" in result.text
    # The generic external gap is still rejected as low-quality evidence...
    assert "planning.external_gap.rejected" in events
    # ...but advisory policy no longer hard-blocks the model's web_search call.
    assert web_search.calls
    assert "planning.policy.denied" not in events


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
    call = ToolCall(id="bypass_1", name="write_file", arguments={"path": "a.txt", "content": "x\n"})
    decision = app.orchestrator._policy_decision_for("write_file")
    state = _TurnState(cancel_event=None, deadline=time.monotonic() + 60)
    outcome = await app.orchestrator._execute_call(call, decision, state)
    await app.close()

    assert outcome.result.is_error
    assert outcome.denied
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
        # Fail-open: tools stay exposed even for a turn classified as chat, so a
        # misclassified implementation request is never handed a tool-less prompt
        # (which is what makes weak models print the call as text). The greeting
        # is still not pushed into the agentic task framing.
        assert request.tools, "tools should stay exposed (fail-open)"
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


async def test_direct_greeting_answers_directly_without_forcing_agentic_flow(tmp_path) -> None:
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


class FakePlanThenBlockingQuestionProvider:
    """Submits a checklist, then ends the turn asking the user a question."""

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
                            id="call_plan",
                            name="submit_plan",
                            arguments={
                                "steps": [
                                    "Inspect the project files",
                                    "Summarise what is implemented",
                                ]
                            },
                        )
                    ],
                    finish_reason=FinishReason.TOOL_CALLS,
                ),
            )
            return
        text = "Which module should I inspect first?"
        yield ProviderEvent(kind="text_delta", text_delta=text)
        yield ProviderEvent(
            kind="completed",
            response=ModelResponse(text=text, finish_reason=FinishReason.STOP),
        )

    async def complete(self, request: ModelRequest) -> ModelResponse:
        async for event in self.stream(request):
            if event.response:
                return event.response
        return ModelResponse()

    async def close(self) -> None:
        return None


async def test_turn_ending_in_a_question_pauses_the_checklist(tmp_path) -> None:
    # Regression: the turn ended in waiting_user with the checklist still ACTIVE
    # and its current step IN_PROGRESS, so the sidebar spinner ran forever while
    # nothing was executing. Every turn exit must pause an unsettled plan.
    config = AppConfig.from_mapping(
        {"api_mode": "ollama", "workspace": str(tmp_path), "model": "fake"}
    )
    provider = FakePlanThenBlockingQuestionProvider()
    app = build_application(config=config, provider=provider)
    events = []
    app.subscribe(lambda event: events.append(event))

    await app.start()
    result = await app.submit_user_message("leia o projeto e me diga o que falta")
    await app.close()

    assert "Which module" in result.text
    planner = app.orchestrator.planner
    assert planner is not None and planner.agent_plan is not None
    assert planner.agent_plan.status.value == "WAITING"
    waiting = [e for e in events if e.event_type == "planning.plan.waiting"]
    assert waiting, "turn end must emit the paused snapshot for the sidebar"
    assert waiting[-1].payload["status"] == "WAITING"
    assert waiting[-1].payload["current_step"] == "Inspect the project files"


class FakeOutsideWorkspaceEditProvider:
    """Tries write_file on an external path, falls back to a command, completes.

    Scripted by agentic step. Failure-memory reflection requests are answered
    with plain text and consume no step - their timing is asynchronous, so
    indexing by raw call count would make the script nondeterministic.
    """

    def __init__(self, outside_path: str) -> None:
        self.outside_path = outside_path
        self.agent_steps = 0
        self.write_file_result = ""

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            streaming=True, tool_calling=True, provider_reported_usage=False
        )

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        if any(
            message.role == "user" and "reviewing a failure" in message.content
            for message in request.messages
        ):
            yield ProviderEvent(
                kind="completed",
                response=ModelResponse(text="noted", finish_reason=FinishReason.STOP),
            )
            return
        self.agent_steps += 1
        for message in request.messages:
            if message.role == "tool" and message.tool_call_id == "write_1":
                # The boundary rejection the model saw for its write_file attempt.
                self.write_file_result = message.content
        completion_args = {
            "summary": f"Created {self.outside_path} via command.",
            "changed_paths": [self.outside_path],
        }
        script = {
            1: ToolCall(
                id="write_1",
                name="write_file",
                arguments={"path": self.outside_path, "content": "hello\n"},
            ),
            2: ToolCall(
                id="cmd_1",
                name="execute_command",
                arguments={"command": f"touch {self.outside_path}"},
            ),
            3: ToolCall(id="done_1", name="complete_task", arguments=completion_args),
            4: ToolCall(
                id="done_2",
                name="complete_task",
                arguments={**completion_args, "double_check_acknowledged": True},
            ),
        }
        call = script.get(self.agent_steps)
        if call is not None:
            yield ProviderEvent(
                kind="completed",
                response=ModelResponse(
                    tool_calls=[call], finish_reason=FinishReason.TOOL_CALLS
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


async def test_outside_workspace_edit_completes_without_workspace_evidence(tmp_path) -> None:
    # Regression: a mutation targeting a file outside the workspace could never
    # satisfy the completion gate (file tools are workspace-bound and the ledger
    # only hashes workspace files), so the model was pushed to fabricate
    # workspace files as evidence. The boundary rejection must teach the planner
    # the target is external, guide the model to execute_command, and command
    # evidence must then settle the completion.
    workspace = tmp_path / "ws"
    workspace.mkdir()
    outside = tmp_path / "outside" / "config.txt"
    outside.parent.mkdir()
    config = AppConfig.from_mapping(
        {
            "api_mode": "ollama",
            "workspace": str(workspace),
            "model": "fake",
            "permission_mode": "bypass",
        }
    )
    provider = FakeOutsideWorkspaceEditProvider(str(outside))
    app = build_application(config=config, provider=provider)
    events = []
    app.subscribe(lambda event: events.append(event))

    await app.start()
    result = await app.submit_user_message(
        f"edite o arquivo {outside} e garanta que ele exista"
    )
    await app.close()

    # The boundary error taught the model the right channel...
    assert "outside the workspace" in provider.write_file_result
    assert "execute_command" in provider.write_file_result
    # ...the planner learned the external target...
    planner = app.orchestrator.planner
    assert str(outside) in planner.external_targets
    # ...the command really ran and completion settled on its evidence.
    assert outside.exists()
    assert "planning.completion.accepted" in {e.event_type for e in events}
    assert str(outside) in result.text
    # No fabricated evidence files appeared inside the workspace.
    assert list(workspace.iterdir()) == []
