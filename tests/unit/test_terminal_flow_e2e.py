"""The terminal driving the real runtime, end to end.

Every other terminal test stubs the application out, so the path the user
actually walks - type, Enter, a task is spawned, the orchestrator runs, events
come back - was never exercised by a test. That is the path the old "it freezes
on waiting_user" bug lived on: the turn died on its way to the model and the
screen kept showing the state it was already in, because nothing had happened
yet and nobody was holding the failure.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from textual.widgets import TextArea

from code_ai.bootstrap import build_application
from code_ai.config.models import AppConfig
from code_ai.providers.models import (
    FinishReason,
    ModelRequest,
    ModelResponse,
    ProviderCapabilities,
    ProviderEvent,
)
from code_ai.ui.terminal.app import create_terminal_app


class ScriptedProvider:
    """Answers each model call from a list of async generators."""

    def __init__(self, *steps) -> None:
        self.steps = list(steps)
        self.calls = 0

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            streaming=True, tool_calling=True, provider_reported_usage=False
        )

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        step = self.steps[min(self.calls, len(self.steps) - 1)]
        self.calls += 1
        async for event in step(request):
            yield event

    async def complete(self, request: ModelRequest) -> ModelResponse:
        completed = None
        async for event in self.stream(request):
            if event.response:
                completed = event.response
        return completed or ModelResponse()

    async def close(self) -> None:
        return None


def answering(text: str):
    async def step(request):
        yield ProviderEvent(kind="text_delta", text_delta=text)
        yield ProviderEvent(
            kind="completed",
            response=ModelResponse(text=text, finish_reason=FinishReason.STOP),
        )

    return step


def _app(tmp_path, provider):
    config = AppConfig.from_mapping(
        {
            "api_mode": "ollama",
            "workspace": str(tmp_path),
            "model": "fake",
            "permission_mode": "bypass",
            "planner": {"enabled": False},
            "memory": {"reflection_enabled": False},
        }
    )
    return build_application(config=config, provider=provider)


async def _type(terminal_app, pilot, text: str) -> None:
    terminal_app.query_one("#input", TextArea).value = text
    await pilot.press("enter")


async def _settle(terminal_app, pilot, *, until, timeout: float = 10.0) -> bool:
    """Pump the UI until ``until()`` holds, or give up. False means it hung."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        await pilot.pause(0.05)
        if until():
            return True
    return False


async def test_a_typed_message_reaches_the_model_and_comes_back(tmp_path) -> None:
    provider = ScriptedProvider(answering("primeira"), answering("segunda"))
    application = _app(tmp_path, provider)
    terminal_app = create_terminal_app(application)
    try:
        async with terminal_app.run_test(size=(120, 40)) as pilot:
            await _type(terminal_app, pilot, "pergunta um")
            assert await _settle(
                terminal_app,
                pilot,
                until=lambda: provider.calls == 1 and terminal_app.vm.status == "READY",
            ), "the first turn never came back"

            # And the flow survives the hand-back: a second message runs too.
            await _type(terminal_app, pilot, "pergunta dois")
            assert await _settle(
                terminal_app,
                pilot,
                until=lambda: provider.calls == 2 and terminal_app.vm.status == "READY",
            ), "the flow died after the first turn settled"

            transcript = "\n".join(terminal_app.vm.conversation)
            assert "you> pergunta um" in transcript
            assert "ai> primeira" in transcript
            assert "you> pergunta dois" in transcript
            assert "ai> segunda" in transcript
            assert terminal_app.vm.phase == "waiting_user"
    finally:
        await application.close()


async def test_the_message_appears_even_when_the_preparation_is_slow(tmp_path) -> None:
    # The freeze looked like the message was never sent. Whatever the runtime
    # does before the model, the user's line and the working state land first.
    provider = ScriptedProvider(answering("ok"))
    application = _app(tmp_path, provider)
    slow = asyncio.Event()

    async def slow_baseline() -> None:
        await slow.wait()

    class SlowBaseline:
        async def capture(self) -> None:
            await slow_baseline()

        async def changed_paths(self):
            return ()

    application.orchestrator.git_baseline = SlowBaseline()
    terminal_app = create_terminal_app(application)
    try:
        async with terminal_app.run_test(size=(120, 40)) as pilot:
            await _type(terminal_app, pilot, "faca algo")
            assert await _settle(
                terminal_app,
                pilot,
                until=lambda: any(
                    line.startswith("you> faca algo") for line in terminal_app.vm.conversation
                ),
                timeout=5,
            ), "the typed message never reached the transcript"
            # Still preparing - but the screen already says so.
            assert provider.calls == 0
            assert terminal_app.vm.status != "READY"

            slow.set()
            assert await _settle(
                terminal_app,
                pilot,
                until=lambda: provider.calls == 1 and terminal_app.vm.status == "READY",
            )
    finally:
        slow.set()
        await application.close()


async def test_a_failure_on_the_way_to_the_model_is_reported_not_swallowed(tmp_path) -> None:
    # The task carrying the turn used to be created and dropped: nobody held it
    # and nobody read its exception, so a failure anywhere on that path was
    # invisible - the UI simply stopped. Now it lands in the transcript.
    provider = ScriptedProvider(answering("nunca chega"))
    application = _app(tmp_path, provider)

    async def explode(*args, **kwargs):
        raise RuntimeError("conversation store is on fire")

    application.submit_user_message = explode
    terminal_app = create_terminal_app(application)
    try:
        async with terminal_app.run_test(size=(120, 40)) as pilot:
            await _type(terminal_app, pilot, "pergunta")
            assert await _settle(
                terminal_app,
                pilot,
                until=lambda: any(
                    line.startswith("error> ") for line in terminal_app.vm.conversation
                ),
                timeout=5,
            ), f"the failure was swallowed: {terminal_app.vm.conversation}"
            reported = [
                line for line in terminal_app.vm.conversation if line.startswith("error> ")
            ][0]
            assert "conversation store is on fire" in reported
    finally:
        await application.close()


async def test_the_turn_task_is_held_not_left_to_the_garbage_collector(tmp_path) -> None:
    # asyncio keeps only a weak reference to a task nobody owns; a collection at
    # the wrong moment kills the turn outright. The screen owns them now.
    import gc

    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_step(request):
        started.set()
        await release.wait()
        yield ProviderEvent(
            kind="completed",
            response=ModelResponse(text="tarde", finish_reason=FinishReason.STOP),
        )

    provider = ScriptedProvider(slow_step)
    application = _app(tmp_path, provider)
    terminal_app = create_terminal_app(application)
    try:
        async with terminal_app.run_test(size=(120, 40)) as pilot:
            await _type(terminal_app, pilot, "tarefa longa")
            assert await _settle(terminal_app, pilot, until=started.is_set, timeout=5)
            assert terminal_app._tasks, "the turn is running on a task nobody holds"

            gc.collect()  # exactly where a dropped task disappears
            release.set()
            assert await _settle(
                terminal_app,
                pilot,
                until=lambda: provider.calls == 1 and terminal_app.vm.status == "READY",
            ), "the turn was collected mid-flight"
            assert any("ai> tarde" in line for line in terminal_app.vm.conversation)
            # The screen lets go of a task once it is done.
            assert not terminal_app._tasks
    finally:
        release.set()
        await application.close()


async def test_a_reflection_that_will_not_stop_does_not_freeze_the_screen(tmp_path) -> None:
    # The reported freeze, from the outside: the user types, and the screen is
    # expected to move on - not sit on waiting_user with the message gone.
    provider = ScriptedProvider(answering("respondido"))
    application = _app(tmp_path, provider)
    running = asyncio.Event()
    release = asyncio.Event()

    async def stubborn() -> None:
        running.set()
        while not release.is_set():
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                continue

    learning = asyncio.create_task(stubborn())
    application.orchestrator._learning_task = learning
    await running.wait()

    terminal_app = create_terminal_app(application)
    try:
        async with terminal_app.run_test(size=(120, 40)) as pilot:
            await _type(terminal_app, pilot, "e agora?")
            assert await _settle(
                terminal_app,
                pilot,
                until=lambda: provider.calls == 1 and terminal_app.vm.status == "READY",
                timeout=15,
            ), (
                "the screen froze on waiting_user: "
                f"status={terminal_app.vm.status} lines={terminal_app.vm.conversation}"
            )
            assert any("ai> respondido" in line for line in terminal_app.vm.conversation)
    finally:
        release.set()
        learning.cancel()
        await asyncio.wait({learning}, timeout=2)
        await application.close()


async def test_the_transcript_keeps_following_when_the_screen_cannot_draw(tmp_path) -> None:
    # A render that raises reached the bus, which logs it and moves on: the
    # transcript stopped following the agent while the runtime kept working,
    # and nothing on screen said why. The view model must take every event
    # regardless, and the failure has to be visible.
    provider = ScriptedProvider(answering("respondido"))
    application = _app(tmp_path, provider)
    terminal_app = create_terminal_app(application)
    try:
        async with terminal_app.run_test(size=(120, 40)) as pilot:
            broken = {"count": 0}
            original = terminal_app._render_event

            def exploding(event) -> None:
                if event.event_type == "model.stream.delta" and broken["count"] < 1:
                    broken["count"] += 1
                    raise RuntimeError("widget tree is broken")
                original(event)

            terminal_app._render_event = exploding

            await _type(terminal_app, pilot, "pergunta")
            assert await _settle(
                terminal_app,
                pilot,
                until=lambda: provider.calls == 1 and terminal_app.vm.status == "READY",
            ), "the turn stalled when a render failed"

            assert broken["count"] == 1
            # The agent's answer is still in the transcript...
            assert any("ai> respondido" in line for line in terminal_app.vm.conversation)
            # ...and the user was told a frame was lost.
            assert any(
                line.startswith("warning> the screen could not render")
                for line in terminal_app.vm.conversation
            ), terminal_app.vm.conversation
    finally:
        await application.close()


async def test_an_approval_dialog_that_vanishes_denies_instead_of_hanging(tmp_path) -> None:
    # The agent waits on the dialog's callback. A dialog that leaves the screen
    # any other way - torn down at shutdown, popped by something else, lost
    # while mounting - never calls it, and the await never returns: the agent
    # sits there waiting for a decision on a dialog nobody can see.
    from code_ai.core.approval import ApprovalRequest
    from code_ai.ui.terminal.approval import TerminalApprovalGateway

    provider = ScriptedProvider(answering("nada"))
    application = _app(tmp_path, provider)
    terminal_app = create_terminal_app(application)
    try:
        async with terminal_app.run_test(size=(120, 40)) as pilot:
            gateway = TerminalApprovalGateway(terminal_app, application.session.config)
            asked = asyncio.create_task(
                gateway.request_approval(
                    ApprovalRequest(
                        call_id="c1",
                        tool_name="write_file",
                        arguments={"path": "a.py", "content": "x"},
                        signature="write_file:a.py",
                    )
                )
            )
            assert await _settle(
                terminal_app, pilot, until=lambda: len(terminal_app.screen_stack) > 1, timeout=5
            )
            terminal_app.pop_screen()

            done, _pending = await asyncio.wait({asked}, timeout=5)
            assert asked in done, "the turn is still waiting on a dialog nobody can see"
            decision = await asked
            assert decision.approved is False
    finally:
        await application.close()


async def test_the_question_cards_can_open_again_after_a_lost_dialog(tmp_path) -> None:
    # The guard that keeps the cards from opening twice is only cleared by the
    # dialog's result callback. A dialog that goes away without it left the
    # latch closed for the rest of the session: no question ever reached the
    # cards again.
    provider = ScriptedProvider(answering("nada"))
    application = _app(tmp_path, provider)
    terminal_app = create_terminal_app(application)
    try:
        async with terminal_app.run_test(size=(120, 40)) as pilot:
            terminal_app.vm.pending_questions = [
                {"question": "Qual banco?", "header": "Banco", "options": ["Postgres"]}
            ]
            terminal_app.vm.status = "READY"
            terminal_app._open_questions_if_waiting()
            await pilot.pause(0.2)
            assert terminal_app._questions_open is True

            # Gone without the callback ever running.
            terminal_app.pop_screen()
            await pilot.pause(0.2)

            terminal_app.vm.pending_questions = [
                {"question": "E o cache?", "header": "Cache", "options": ["Redis"]}
            ]
            terminal_app._open_questions_if_waiting()
            await pilot.pause(0.2)
            assert terminal_app._questions_open is True, "the cards never opened again"
    finally:
        await application.close()
