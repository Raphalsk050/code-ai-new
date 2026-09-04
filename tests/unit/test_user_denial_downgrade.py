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


class _UnreachableGateway:
    """Interactive, but the dialog never produced an answer.

    The shape the terminal gateway returns when its modal leaves the screen
    without a choice, when it cannot be opened, or when the prompt raises.
    """

    async def request_approval(self, request: ApprovalRequest) -> ApprovalDecision:
        return ApprovalDecision.unavailable("The approval dialog closed without a choice.")


async def test_a_dialog_that_never_answered_does_not_downgrade_the_task(tmp_path) -> None:
    """An interactive gateway can fail without the user having refused anything.

    This used to be indistinguishable from a refusal: the check was "is the
    gateway the DenyAll one", so any other gateway saying no counted as a
    person saying no. A dialog torn down mid-turn therefore convinced the
    planner the user had declined, and from there every later write was
    rejected as an unrequested artifact.
    """

    provider = MkdirThenProseProvider()
    app = build_application(config=_config(tmp_path), provider=provider)
    app.orchestrator.approval_gateway = _UnreachableGateway()

    await app.start()
    await app.submit_user_message("implemente o modulo de estoque no projeto")
    await app.close()

    planner = app.orchestrator.planner
    assert planner is not None
    assert planner.user_declined_mutation is False


async def test_a_refusal_does_not_make_later_writes_look_unrequested(tmp_path) -> None:
    """The cascade that turned one denial into an agent that keeps asking.

    A refusal rightly stops the runtime demanding a file change. It used to do
    more than that: the same flag also answered "was this task ever about
    changing files", so the next write was rejected with "this task was
    classified as read-only" - which the model reads as a second denial, and
    the prompt tells it to stop and ask the user what is permitted.
    """

    app = build_application(config=_config(tmp_path), provider=MkdirThenProseProvider())
    planner = app.orchestrator.planner
    assert planner is not None
    await planner.begin_turn("implemente o modulo de estoque", provider_supports_tools=True)

    await planner.note_user_denial("write_file", "not that file")

    assert planner.user_declined_mutation is True
    assert planner.requires_tool_for_progress() is False  # demand dropped, as intended
    # But the task is still a build task, so writing is not an unrequested artifact.
    gap = planner.precondition_gap("write_file", {"path": "src/stock.py", "content": "x = 1\n"})
    assert gap is None or "classified as read-only" not in gap


async def test_continuing_after_a_refusal_starts_from_a_clean_slate(tmp_path) -> None:
    """"No" answers one call, not every turn that follows it."""

    app = build_application(config=_config(tmp_path), provider=MkdirThenProseProvider())
    planner = app.orchestrator.planner
    assert planner is not None
    await planner.begin_turn("implemente o modulo de estoque", provider_supports_tools=True)
    await planner.note_user_denial("write_file", "not that file")
    assert planner.user_declined_mutation is True

    await planner._resume_turn("continue")

    assert planner.user_declined_mutation is False
