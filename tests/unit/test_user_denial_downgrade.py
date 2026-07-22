"""A user denial of a mutating action drops the runtime's mutation demand.

Regression suite for the observed session where the question "pelo que voce
comecaria a implementar hoje?" was classified as an implementation task: the
user denied mkdir three times ("fiz apenas uma pergunta") while the runtime
kept demanding workspace evidence and even nudged the model back into
mutating after it had correctly fallen back to prose.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from code_ai.bootstrap import build_application
from code_ai.config.models import AppConfig
from code_ai.core.approval import ApprovalDecision, ApprovalRequest
from code_ai.events.models import EventEnvelope
from code_ai.providers.models import (
    FinishReason,
    ModelRequest,
    ModelResponse,
    ProviderCapabilities,
    ProviderEvent,
    ToolCall,
)

PROSE = "Eu começaria pelo contrato de domínio, sem tocar em arquivos por enquanto."


def _config(tmp_path, **overrides) -> AppConfig:
    data = {
        "api_mode": "ollama",
        "workspace": str(tmp_path),
        "model": "fake",
        "permission_mode": "ask",
        "memories_dir": str(tmp_path / "memories"),
        # These tests script exact provider call sequences; the post-turn
        # reflection meta-call would add calls the scripts do not expect.
        "memory": {"reflection_enabled": False},
    }
    data.update(overrides)
    return AppConfig.from_mapping(data)


class _DenyWithReasonGateway:
    """Interactive stand-in: the user denies every prompt with a reason."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        self.requests: list[ApprovalRequest] = []

    async def request_approval(self, request: ApprovalRequest) -> ApprovalDecision:
        self.requests.append(request)
        return ApprovalDecision.deny(self.reason)


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


class MkdirThenProseProvider(_BaseProvider):
    """Tries one command; once refused, answers the question in prose."""

    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        self.calls += 1
        if self.calls == 1:
            yield ProviderEvent(
                kind="completed",
                response=ModelResponse(
                    tool_calls=[
                        ToolCall(
                            id="c1",
                            name="execute_command",
                            arguments={"command": "mkdir -p docs/design", "cwd": "."},
                        )
                    ],
                    finish_reason=FinishReason.TOOL_CALLS,
                ),
            )
            return
        yield ProviderEvent(kind="text_delta", text_delta=PROSE)
        yield ProviderEvent(
            kind="completed",
            response=ModelResponse(text=PROSE, finish_reason=FinishReason.STOP),
        )


async def test_user_denial_drops_mutation_demand_and_prose_ends_the_turn(
    tmp_path,
) -> None:
    provider = MkdirThenProseProvider()
    app = build_application(config=_config(tmp_path), provider=provider)
    app.orchestrator.approval_gateway = _DenyWithReasonGateway("fiz apenas uma pergunta")
    events: list[EventEnvelope] = []
    app.subscribe(events.append)

    await app.start()
    result = await app.submit_user_message("implemente o modulo de estoque no projeto")
    await app.close()

    # The prose right after the denial ends the turn: no corrective nudge
    # pushing the model back into mutating (that nudge caused the third mkdir).
    assert provider.calls == 2
    assert result.text == PROSE

    planner = app.orchestrator.planner
    assert planner is not None
    assert planner.user_declined_mutation is True
    assert planner.requires_tool_for_progress() is False
    assert any(
        e.event_type == "planning.mutation_demand.dropped" for e in events
    )

    # With the demand dropped the answer streams on the answer channel (a real
    # message), not as dim working trace.
    channels = {
        str(e.payload.get("channel"))
        for e in events
        if e.event_type == "model.stream.delta"
    }
    assert channels == {"answer"}


async def test_headless_denyall_denial_does_not_downgrade(tmp_path) -> None:
    provider = MkdirThenProseProvider()
    app = build_application(config=_config(tmp_path), provider=provider)
    # No interactive gateway attached: the default DenyAllGateway refuses the
    # command, but that is an environment limitation, not the user speaking.

    await app.start()
    result = await app.submit_user_message("implemente o modulo de estoque no projeto")
    await app.close()

    # The mutation demand stays: the no-tool prose still gets one corrective
    # nudge, so the provider is called a third time.
    assert provider.calls == 3
    assert result.text == PROSE
    planner = app.orchestrator.planner
    assert planner is not None
    assert planner.user_declined_mutation is False
