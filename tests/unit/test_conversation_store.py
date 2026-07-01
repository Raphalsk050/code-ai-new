from __future__ import annotations

from pathlib import Path

from code_ai.app.conversation_store import ConversationStore
from code_ai.providers.models import Message, ToolCall


def _store(tmp_path: Path) -> ConversationStore:
    return ConversationStore(tmp_path / "conversations")


def test_save_then_load_round_trips_messages(tmp_path: Path) -> None:
    store = _store(tmp_path)
    messages = [
        Message(role="user", content="list files"),
        Message(
            role="assistant",
            content="running",
            tool_calls=[ToolCall(id="t1", name="list_files", arguments={"path": "."})],
        ),
        Message(role="tool", content="a.py\nb.py", tool_call_id="t1", name="list_files"),
    ]
    store.save(conversation_id="c-1", messages=messages, previous_response_id="resp-9")

    loaded = store.load_messages("c-1")
    assert [m.role for m in loaded] == ["user", "assistant", "tool"]
    assert loaded[1].tool_calls[0].name == "list_files"
    assert loaded[1].tool_calls[0].arguments == {"path": "."}
    assert loaded[2].tool_call_id == "t1"

    record = store.load("c-1")
    assert record is not None
    assert record["previous_response_id"] == "resp-9"


def test_title_derives_from_first_user_message(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.save(
        conversation_id="c-2",
        messages=[Message(role="user", content="  Fix   the login bug  ")],
    )
    (entry,) = store.list()
    assert entry["id"] == "c-2"
    assert entry["title"] == "Fix the login bug"
    assert entry["message_count"] == 1


def test_list_is_newest_first_and_skips_empty(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.save(conversation_id="old", messages=[Message(role="user", content="one")])
    store.save(conversation_id="new", messages=[Message(role="user", content="two")])
    # Empty conversations never make the list.
    store.save(conversation_id="blank", messages=[])

    ids = [c["id"] for c in store.list()]
    assert ids == ["new", "old"]


def test_save_preserves_created_at_across_updates(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.save(conversation_id="c", messages=[Message(role="user", content="first")])
    created = store.load("c")["created_at"]
    store.save(
        conversation_id="c",
        messages=[Message(role="user", content="first"), Message(role="assistant", content="hi")],
    )
    updated = store.load("c")
    assert updated["created_at"] == created
    assert updated["updated_at"] >= created
    assert len(updated["messages"]) == 2


def test_delete_removes_the_conversation(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.save(conversation_id="c", messages=[Message(role="user", content="hi")])
    assert store.delete("c") is True
    assert store.load("c") is None
    assert store.delete("c") is False


def test_id_with_path_separators_cannot_escape_directory(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.save(conversation_id="../../evil", messages=[Message(role="user", content="hi")])
    # The file lands inside the store directory, not in a parent.
    files = list((tmp_path / "conversations").glob("*.json"))
    assert len(files) == 1
    assert store.load("../../evil") is not None
