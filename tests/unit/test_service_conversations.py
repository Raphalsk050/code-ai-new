from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from code_ai.app.conversation_store import ConversationStore
from code_ai.app.service import CodeAIApplication
from code_ai.context.conversation import ConversationState
from code_ai.core.state import AgentState
from code_ai.providers.models import Message


class _FakeBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def emit(self, event_type: str, payload: dict | None = None, *, source: str = "") -> None:
        self.events.append((event_type, payload or {}))


class _FakeOrchestrator:
    def __init__(self, conversation: ConversationState) -> None:
        self.conversation = conversation
        self.states: list[AgentState] = []

    async def set_state(self, state: AgentState, phase: str = "") -> None:
        self.states.append(state)


def _app(tmp_path: Path, conversation: ConversationState) -> CodeAIApplication:
    return CodeAIApplication(
        session=SimpleNamespace(config=None, state=AgentState.READY),
        event_bus=_FakeBus(),
        orchestrator=_FakeOrchestrator(conversation),
        provider=None,
        compressor=None,
        conversation_store=ConversationStore(tmp_path / "conversations"),
    )


async def test_reset_conversation_tags_id_and_keeps_system(tmp_path: Path) -> None:
    conv = ConversationState(
        messages=[Message(role="system", content="SYS"), Message(role="user", content="old")]
    )
    app = _app(tmp_path, conv)

    await app.reset_conversation(conversation_id="c-1")

    assert app.conversation_id == "c-1"
    assert [m.role for m in conv.messages] == ["system"]


async def test_reset_without_id_generates_one(tmp_path: Path) -> None:
    app = _app(tmp_path, ConversationState(messages=[Message(role="system", content="SYS")]))
    await app.reset_conversation()
    assert app.conversation_id  # a fresh id was minted


async def test_persist_then_resume_round_trips_history(tmp_path: Path) -> None:
    conv = ConversationState(messages=[Message(role="system", content="SYS")])
    app = _app(tmp_path, conv)
    await app.reset_conversation(conversation_id="c-42")

    conv.add_user("remember the number 7")
    conv.add_assistant("Noted: 7.")
    app._persist_conversation()

    # A fresh session (new system prompt) resumes the saved thread.
    conv2 = ConversationState(messages=[Message(role="system", content="NEW-SYS")])
    app2 = _app(tmp_path, conv2)
    # Share the same on-disk store directory.
    app2.conversation_store = app.conversation_store

    result = await app2.load_conversation("c-42")

    assert app2.conversation_id == "c-42"
    # Current system prompt preserved, saved turns appended.
    assert conv2.messages[0].content == "NEW-SYS"
    assert [m.role for m in conv2.messages] == ["system", "user", "assistant"]
    assert conv2.messages[1].content == "remember the number 7"
    assert result["messages"][0]["content"] == "remember the number 7"


async def test_load_missing_conversation_raises(tmp_path: Path) -> None:
    app = _app(tmp_path, ConversationState(messages=[Message(role="system", content="SYS")]))
    try:
        await app.load_conversation("does-not-exist")
    except ValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected ValueError for unknown conversation")


async def test_delete_clears_current_id(tmp_path: Path) -> None:
    conv = ConversationState(messages=[Message(role="system", content="SYS")])
    app = _app(tmp_path, conv)
    await app.reset_conversation(conversation_id="c-9")
    conv.add_user("hi")
    app._persist_conversation()

    assert await app.delete_conversation("c-9") is True
    assert app.conversation_id is None
    assert app.conversation_store.load("c-9") is None
