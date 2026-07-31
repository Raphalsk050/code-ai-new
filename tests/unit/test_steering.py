"""Messages typed while a turn is running (steering)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable

from code_ai.bootstrap import build_application
from code_ai.config.models import AppConfig
from code_ai.events.models import EventEnvelope
from code_ai.providers.models import (
    FinishReason,
    ModelRequest,
    ModelResponse,
    ProviderCapabilities,
    ProviderEvent,
    ToolCall,
)
from code_ai.ui.terminal.view_models import TerminalViewModel


def _config(tmp_path) -> AppConfig:
    return AppConfig.from_mapping(
        {
            "api_mode": "ollama",
            "workspace": str(tmp_path),
            "model": "fake",
            "permission_mode": "bypass",
            "memories_dir": str(tmp_path / "memories"),
        }
    )


class _ScriptedProvider:
    """Calls a tool on the first step, answers on the second.

    ``on_step`` runs at the start of each step, standing in for the user typing
    while the agent works.
    """

    def __init__(self, on_step: Callable[[int], object] | None = None) -> None:
        self.on_step = on_step
        self.steps = 0
        # (role, content) per message, per request, so a test can see exactly
        # what the model was shown and when.
        self.requests: list[list[tuple[str, str]]] = []

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(streaming=True, tool_calling=True)

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        self.steps += 1
        self.requests.append([(m.role, m.content) for m in request.messages])
        if self.on_step is not None:
            result = self.on_step(self.steps)
            if hasattr(result, "__await__"):
                await result
        if self.steps == 1:
            yield ProviderEvent(
                kind="completed",
                response=ModelResponse(
                    tool_calls=[ToolCall(id="c1", name="list_files", arguments={"path": "."})],
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


def _contents(messages: list[tuple[str, str]]) -> str:
    return "\n".join(content for role, content in messages if role == "user")


async def test_a_message_typed_mid_turn_reaches_the_very_next_step(tmp_path) -> None:
    # The point of steering: the message lands on the next model request, right
    # after the tool call in flight finishes - not once the whole turn is over.
    app = build_application(config=_config(tmp_path), provider=None)
    provider = _ScriptedProvider()
    app.orchestrator.provider = provider

    def steer(step: int) -> None:
        if step == 1:
            app.orchestrator.queue_user_message("actually, use pytest")

    provider.on_step = steer

    await app.start()
    await app.submit_user_message("write the tests")
    await app.close()

    assert provider.steps >= 2
    assert "actually, use pytest" not in _contents(provider.requests[0])
    assert "actually, use pytest" in _contents(provider.requests[1])


async def test_steering_does_not_start_a_second_turn(tmp_path) -> None:
    # It joins the running turn. Submitting while busy must not raise, and must
    # not run the message as a turn of its own.
    app = build_application(config=_config(tmp_path), provider=None)
    provider = _ScriptedProvider()
    app.orchestrator.provider = provider
    queued: list[EventEnvelope] = []
    app.subscribe(lambda e: queued.append(e) if e.event_type == "user.message.queued" else None)

    async def steer(step: int) -> None:
        if step == 1:
            result = await app.submit_user_message("and keep it short")
            assert result.queued is True

    provider.on_step = steer

    await app.start()
    await app.submit_user_message("write the tests")
    await app.close()

    assert len(queued) == 1
    assert "and keep it short" in _contents(provider.requests[1])
    # One turn, not two: the second message rode along inside the first.
    user_turns = [e for e in queued if e.event_type == "user.message.queued"]
    assert len(user_turns) == 1


async def test_a_message_the_loop_never_read_gets_its_own_turn(tmp_path) -> None:
    # Queued so late that the turn ended first. Rather than being lost with it,
    # it runs as a turn of its own with the whole conversation still behind it.
    app = build_application(config=_config(tmp_path), provider=None)
    provider = _ScriptedProvider()
    app.orchestrator.provider = provider

    def steer(step: int) -> None:
        # Step 2 is the answering step: nothing will drain the queue after it.
        if step == 2:
            app.orchestrator.queue_user_message("one more thing")

    provider.on_step = steer

    await app.start()
    await app.submit_user_message("write the tests")
    await app.close()

    assert provider.steps >= 3, "the late message must get a turn of its own"
    assert "one more thing" in _contents(provider.requests[2])
    assert not app.orchestrator.has_queued_messages()


async def test_cancelling_drops_the_queue(tmp_path) -> None:
    # Ctrl+C means stop - not "stop, then run what I queued".
    app = build_application(config=_config(tmp_path), provider=None)
    provider = _ScriptedProvider()
    app.orchestrator.provider = provider

    async def steer(step: int) -> None:
        if step == 1:
            app.orchestrator.queue_user_message("never mind")
            await app.cancel_current_turn()

    provider.on_step = steer

    await app.start()
    result = await app.submit_user_message("write the tests")
    await app.close()

    assert result.cancelled is True
    assert not app.orchestrator.has_queued_messages()


def test_queue_ignores_blank_messages() -> None:
    from code_ai.core.orchestration import AgentOrchestrator

    orchestrator = object.__new__(AgentOrchestrator)
    orchestrator._queued_user_messages = []

    orchestrator.queue_user_message("   ")
    orchestrator.queue_user_message("")
    assert not orchestrator.has_queued_messages()

    orchestrator.queue_user_message("  real  ")
    assert orchestrator.take_queued_messages() == ["real"]
    assert not orchestrator.has_queued_messages()


def _event(event_type: str, payload: dict[str, object]) -> EventEnvelope:
    return EventEnvelope.create(
        event_type=event_type, session_id="test", sequence=0, payload=payload
    )


def test_queued_message_shows_without_resetting_the_turn_panels() -> None:
    # user.message clears the plan/agents/code panels because it starts a turn.
    # A steered message joins the running turn, so those must survive.
    vm = TerminalViewModel()
    vm.plan_visible = True
    vm.subagents_visible = True
    vm.code_stream_visible = True

    vm.apply(_event("user.message.queued", {"text": "use pytest"}))

    assert vm.conversation[-2] == "you> use pytest"
    assert vm.conversation[-1].startswith("queued> ")
    assert vm.plan_visible is True
    assert vm.subagents_visible is True
    assert vm.code_stream_visible is True


def test_delivery_replaces_the_waiting_note() -> None:
    vm = TerminalViewModel()
    vm.apply(_event("user.message.queued", {"text": "use pytest"}))
    vm.apply(_event("user.message.delivered", {"text": "use pytest"}))

    assert vm.conversation.count("you> use pytest") == 1
    waiting = [line for line in vm.conversation if line.startswith("queued> ")]
    assert waiting == ["queued> delivered to the model"]
