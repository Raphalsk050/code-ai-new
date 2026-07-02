from __future__ import annotations

from collections.abc import AsyncIterator

from code_ai.bootstrap import build_tool_registry
from code_ai.config.models import AppConfig
from code_ai.core.subagents.profiles import default_profile_registry
from code_ai.core.subagents.runtime import SubagentRuntime
from code_ai.providers.models import (
    FinishReason,
    ModelRequest,
    ModelResponse,
    ProviderCapabilities,
    ProviderEvent,
    ToolCall,
)
from code_ai.tools.base import ToolCapability
from code_ai.util.paths import WorkspacePolicy


def _config(tmp_path, **overrides) -> AppConfig:
    data = {
        "api_mode": "ollama",
        "workspace": str(tmp_path),
        "model": "fake",
        "permission_mode": "ask",
        "memories_dir": str(tmp_path / "memories"),
    }
    data.update(overrides)
    return AppConfig.from_mapping(data)


class _BaseProvider:
    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            streaming=True, tool_calling=True, provider_reported_usage=False
        )

    async def complete(self, request: ModelRequest) -> ModelResponse:
        async for event in self.stream(request):
            if event.response:
                return event.response
        return ModelResponse()

    async def close(self) -> None:
        return None


def _runtime(tmp_path, config=None) -> SubagentRuntime:
    config = config or _config(tmp_path)
    return SubagentRuntime(
        config=config,
        provider=_BaseProvider(),
        workspace=WorkspacePolicy.from_path(config.workspace),
        base_registry=build_tool_registry(),
        rules_text="",
    )


def test_explorer_registry_is_read_only(tmp_path) -> None:
    built = _runtime(tmp_path).build(default_profile_registry().get("explorer"))
    registry = built.orchestrator.tool_registry
    names = set(registry.names())

    assert "read_file" in names
    assert "search_code" in names
    # No writers, no process, no interactive terminal, no dispatch, no completion.
    assert "write_file" not in names
    assert "edit_code" not in names
    assert "execute_command" not in names
    assert "start_terminal" not in names
    assert "complete_task" not in names
    assert "ask_user" not in names
    for name in names:
        caps = registry.capabilities(name)
        assert caps <= frozenset({ToolCapability.LOCAL_READ, ToolCapability.WEB})


def test_coder_registry_has_write_and_process_but_no_singletons(tmp_path) -> None:
    built = _runtime(tmp_path).build(default_profile_registry().get("coder"))
    names = set(built.orchestrator.tool_registry.names())

    assert {"write_file", "edit_code", "execute_command", "read_file"} <= names
    # Mutable singletons and out-of-scope tools stay out.
    assert "start_terminal" not in names
    assert "move_mouse" not in names
    assert "remember" not in names
    assert "submit_plan" not in names


def test_built_subagent_is_isolated_from_parent(tmp_path) -> None:
    config = _config(tmp_path)
    built = _runtime(tmp_path, config).build(default_profile_registry().get("coder"))
    orch = built.orchestrator

    # No planner, no durable/failure memory, its own usage ledger.
    assert orch.planner is None
    assert orch.failure_memory is None
    assert orch.memory is None
    assert orch.usage is built.usage
    assert orch.usage.total_tokens == 0
    # Internal bypass so parallel agents never block on approval prompts, while
    # the parent config is untouched.
    assert orch.config.permission_mode == "bypass"
    assert config.permission_mode == "ask"
    # Budgets scoped to the profile.
    assert orch.config.budgets.max_model_steps == 60
    assert built.timeout_seconds == config.budgets.subagent_worker_timeout_s


def test_system_prompt_carries_role_and_workspace(tmp_path) -> None:
    config = _config(tmp_path)
    built = _runtime(tmp_path, config).build(default_profile_registry().get("explorer"))
    system = built.orchestrator.conversation.messages[0].content

    assert "exploration sub-agent" in system
    assert str(tmp_path) in system
    # Sub-agents cannot delegate further.
    assert "cannot" in system.lower()


class ReadThenReportProvider(_BaseProvider):
    """Reads a file, then reports what it found - a minimal explorer run."""

    def __init__(self) -> None:
        self.calls = 0
        self.tool_feedback = ""

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        self.calls += 1
        if self.calls == 1:
            yield ProviderEvent(
                kind="completed",
                response=ModelResponse(
                    tool_calls=[
                        ToolCall(id="r1", name="read_file", arguments={"path": "note.txt"})
                    ],
                    finish_reason=FinishReason.TOOL_CALLS,
                ),
            )
            return
        self.tool_feedback = "".join(m.content for m in request.messages if m.role == "tool")
        yield ProviderEvent(
            kind="completed",
            response=ModelResponse(
                text="The note contains the launch code.", finish_reason=FinishReason.STOP
            ),
        )


async def test_subagent_runs_isolated_loop_and_reports(tmp_path) -> None:
    (tmp_path / "note.txt").write_text("launch code: 42\n", encoding="utf-8")
    config = _config(tmp_path)
    provider = ReadThenReportProvider()
    runtime = SubagentRuntime(
        config=config,
        provider=provider,
        workspace=WorkspacePolicy.from_path(config.workspace),
        base_registry=build_tool_registry(),
    )
    built = runtime.build(default_profile_registry().get("explorer"))

    child_events: list[str] = []
    built.event_bus.subscribe(lambda e: child_events.append(e.event_type))

    result = await built.orchestrator.run_turn("What does note.txt say?")

    assert result.error is None
    assert result.text == "The note contains the launch code."
    assert "launch code: 42" in provider.tool_feedback
    # The sub-agent's activity landed on its own bus, not the parent's.
    assert "tool.call.completed" in child_events
    assert built.usage is built.orchestrator.usage
