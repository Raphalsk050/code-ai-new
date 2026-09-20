"""What happens to a message typed while the agent is already working.

Two answers, and the caller picks. Steering folds it into the running turn, so
the agent changes course mid-task. Waiting gives it a turn of its own, so a
second question gets a second answer instead of being absorbed into the first.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from code_ai.app.conversation_store import ConversationStore
from code_ai.app.service import CodeAIApplication
from code_ai.context.conversation import ConversationState
from code_ai.core.orchestration import TurnResult
from code_ai.core.state import AgentState
from code_ai.providers.models import Message


class _Bus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def emit(self, event_type: str, payload: dict | None = None, *, source: str = "") -> None:
        self.events.append((event_type, payload or {}))

    def types(self) -> list[str]:
        return [t for t, _ in self.events]


class _Orchestrator:
    """Records steering messages; the turn itself is driven by the test."""

    def __init__(self) -> None:
        self.conversation = ConversationState(messages=[Message(role="system", content="SYS")])
        self.queued: list[str] = []

    async def set_state(self, state: AgentState, phase: str = "") -> None:
        return None

    def queue_user_message(self, text: str) -> None:
        self.queued.append(text)

    def has_queued_messages(self) -> bool:
        return bool(self.queued)

    def take_queued_messages(self) -> list[str]:
        taken, self.queued = self.queued, []
        return taken


def _app(tmp_path: Path) -> tuple[CodeAIApplication, _Bus, _Orchestrator]:
    bus, orch = _Bus(), _Orchestrator()
    app = CodeAIApplication(
        session=SimpleNamespace(config=None, state=AgentState.READY),
        event_bus=bus,
        orchestrator=orch,
        provider=None,
        compressor=None,
        conversation_store=ConversationStore(tmp_path / "conversations"),
    )
    return app, bus, orch


def _busy(app: CodeAIApplication) -> asyncio.Task:
    """Make the app look mid-turn, the way a running turn does."""
    task: asyncio.Task = asyncio.create_task(asyncio.sleep(3600))
    app._current_task = task  # type: ignore[assignment]
    return task


async def test_steering_hands_the_message_to_the_running_turn(tmp_path: Path) -> None:
    app, bus, orch = _app(tmp_path)
    task = _busy(app)
    try:
        result = await app.submit_user_message("na verdade, em rust", steer=True)
    finally:
        task.cancel()

    assert result.queued is True
    assert orch.queued == ["na verdade, em rust"]
    assert "user.message.queued" in bus.types()
    assert app._followups == []


async def test_waiting_keeps_the_message_for_a_turn_of_its_own(tmp_path: Path) -> None:
    app, bus, orch = _app(tmp_path)
    task = _busy(app)
    try:
        result = await app.submit_user_message("e um hello world?", steer=False)
    finally:
        task.cancel()

    assert result.queued is True
    # Not handed to the running turn: that is the whole difference.
    assert orch.queued == []
    assert app._followups == ["e um hello world?"]
    assert "user.message.enqueued" in bus.types()


async def test_the_queue_reports_where_a_message_landed(tmp_path: Path) -> None:
    app, bus, _ = _app(tmp_path)
    task = _busy(app)
    try:
        await app.submit_user_message("primeira", steer=False)
        await app.submit_user_message("segunda", steer=False)
    finally:
        task.cancel()

    positions = [p["position"] for t, p in bus.events if t == "user.message.enqueued"]
    assert positions == [1, 2]
    assert app._followups == ["primeira", "segunda"]


async def test_blank_text_is_dropped_rather_than_queued(tmp_path: Path) -> None:
    app, bus, orch = _app(tmp_path)
    task = _busy(app)
    try:
        await app.submit_user_message("   ", steer=False)
        await app.submit_user_message("", steer=True)
    finally:
        task.cancel()

    assert app._followups == []
    assert orch.queued == []
    assert "user.message.enqueued" not in bus.types()


async def test_waiting_messages_each_get_their_own_turn(tmp_path: Path) -> None:
    app, _, _ = _app(tmp_path)
    ran: list[str] = []

    async def fake_turn(text, *, context, resume_plan, images):
        ran.append(text)
        # Two more arrive while the first turn runs, then nothing.
        if len(ran) == 1:
            app._followups.extend(["segunda", "terceira"])
        return TurnResult(text="ok", response=None)

    app._run_turn = fake_turn  # type: ignore[assignment]
    await app.submit_user_message("primeira")

    # One turn each, in the order typed - not one turn holding all three.
    assert ran == ["primeira", "segunda", "terceira"]
    assert app._followups == []


async def test_steering_leftovers_ride_together_on_one_turn(tmp_path: Path) -> None:
    app, _, orch = _app(tmp_path)
    ran: list[str] = []

    async def fake_turn(text, *, context, resume_plan, images):
        ran.append(text)
        if len(ran) == 1:
            # Queued too late for the loop to have read them.
            orch.queued.extend(["mais uma", "e outra"])
        return TurnResult(text="ok", response=None)

    app._run_turn = fake_turn  # type: ignore[assignment]
    await app.submit_user_message("primeira")

    assert ran == ["primeira", "mais uma\n\ne outra"]


async def test_cancelling_drops_both_queues(tmp_path: Path) -> None:
    app, _, orch = _app(tmp_path)

    async def fake_turn(text, *, context, resume_plan, images):
        app._followups.append("nunca roda")
        orch.queued.append("nem esta")
        return TurnResult(text="", response=None, cancelled=True)

    app._run_turn = fake_turn  # type: ignore[assignment]
    result = await app.submit_user_message("primeira")

    # Ctrl+C means stop, not "stop and then run the backlog".
    assert result.cancelled is True
    assert app._followups == []
    assert orch.queued == []
