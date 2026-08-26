from __future__ import annotations

from collections.abc import AsyncIterator

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

# Marks the sub-agent system prompt uniquely (the main agent prompt merely
# mentions "sub-agents", so we key on the phrase only the child prompt carries).
_SUBAGENT_MARKER = "dispatched by the main Code-AI agent"


def _config(tmp_path, **overrides) -> AppConfig:
    data = {
        "api_mode": "ollama",
        "workspace": str(tmp_path),
        "model": "fake",
        # Bypass so the dispatch (a DELEGATE-capability tool) is not gated on an
        # interactive approval prompt in this headless test.
        "permission_mode": "bypass",
        "planner": {"enabled": False},
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


class OrchestratingProvider(_BaseProvider):
    """Drives a full delegation: the parent fans out two explorers, then answers.

    The single provider instance serves both roles. Sub-agent requests are
    recognized by their system prompt and answer their focused question directly;
    the parent dispatches on its first call and synthesizes the reports on the
    second.
    """

    def __init__(self) -> None:
        self.parent_calls = 0
        self.subagent_prompts: list[str] = []

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        system = request.messages[0].content if request.messages else ""
        if _SUBAGENT_MARKER in system:
            async for event in self._subagent(request):
                yield event
            return
        async for event in self._parent(request):
            yield event

    async def _subagent(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        # The last user message is the delegated task prompt.
        task = next(
            (m.content for m in reversed(request.messages) if m.role == "user"), ""
        )
        self.subagent_prompts.append(task)
        answer = f"Report for: {task[:40]}"
        yield ProviderEvent(
            kind="completed",
            response=ModelResponse(text=answer, finish_reason=FinishReason.STOP),
        )

    async def _parent(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        self.parent_calls += 1
        if self.parent_calls == 1:
            yield ProviderEvent(
                kind="completed",
                response=ModelResponse(
                    tool_calls=[
                        ToolCall(
                            id="d1",
                            name="dispatch_agent",
                            arguments={
                                "tasks": [
                                    {
                                        "agent_type": "explorer",
                                        "prompt": "Locate the config loader.",
                                    },
                                    {
                                        "agent_type": "explorer",
                                        "prompt": "Locate the event bus.",
                                    },
                                ]
                            },
                        )
                    ],
                    finish_reason=FinishReason.TOOL_CALLS,
                ),
            )
            return
        # Second call: the dispatch tool result is now in the conversation.
        tool_text = "".join(m.content for m in request.messages if m.role == "tool")
        self.last_tool_text = tool_text
        yield ProviderEvent(
            kind="completed",
            response=ModelResponse(
                text="Synthesized both explorer reports.", finish_reason=FinishReason.STOP
            ),
        )


async def test_parent_delegates_to_parallel_subagents_end_to_end(tmp_path) -> None:
    provider = OrchestratingProvider()
    app = build_application(config=_config(tmp_path), provider=provider)
    events: list[str] = []
    app.subscribe(lambda e: events.append(e.event_type))

    await app.start()
    result = await app.submit_user_message("Investigate the config loader and the event bus.")
    await app.close()

    # The parent's final answer is surfaced, not the raw tool result.
    assert result.error is None
    assert result.text == "Synthesized both explorer reports."

    # Two sub-agents actually ran, each with its own focused prompt.
    assert sorted(provider.subagent_prompts) == [
        "Locate the config loader.",
        "Locate the event bus.",
    ]
    # Their reports were fed back to the parent for synthesis.
    assert "Report for: Locate the config loader." in provider.last_tool_text
    assert "Report for: Locate the event bus." in provider.last_tool_text
    assert '"status": "completed"' in provider.last_tool_text

    # Lifecycle events reached the application bus.
    assert "subagent.dispatch.requested" in events
    assert events.count("subagent.started") == 2
    assert events.count("subagent.completed") == 2


class UnknownTypeProvider(_BaseProvider):
    """Parent asks for a non-existent agent type, then recovers on the rejection."""

    def __init__(self) -> None:
        self.parent_calls = 0

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        system = request.messages[0].content if request.messages else ""
        if _SUBAGENT_MARKER in system:  # pragma: no cover - never dispatched
            yield ProviderEvent(
                kind="completed",
                response=ModelResponse(text="unexpected", finish_reason=FinishReason.STOP),
            )
            return
        self.parent_calls += 1
        if self.parent_calls == 1:
            yield ProviderEvent(
                kind="completed",
                response=ModelResponse(
                    tool_calls=[
                        ToolCall(
                            id="d1",
                            name="dispatch_agent",
                            arguments={"tasks": [{"agent_type": "wizard", "prompt": "cast"}]},
                        )
                    ],
                    finish_reason=FinishReason.TOOL_CALLS,
                ),
            )
            return
        self.last_tool_text = "".join(m.content for m in request.messages if m.role == "tool")
        yield ProviderEvent(
            kind="completed",
            response=ModelResponse(
                text="I'll handle it myself.", finish_reason=FinishReason.STOP
            ),
        )


async def test_unknown_agent_type_degrades_without_crashing(tmp_path) -> None:
    provider = UnknownTypeProvider()
    app = build_application(config=_config(tmp_path), provider=provider)

    await app.start()
    result = await app.submit_user_message("Do something with a wizard.")
    await app.close()

    # The bad delegation surfaced as a structured rejection the model read, and
    # the turn completed normally rather than raising.
    assert result.error is None
    assert result.text == "I'll handle it myself."
    assert '"status": "rejected"' in provider.last_tool_text
    assert "Unknown sub-agent type" in provider.last_tool_text
