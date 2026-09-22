from __future__ import annotations

from pathlib import Path

from code_ai.app.conversation_store import ConversationStore
from code_ai.providers.models import ImageContent, Message, ToolCall


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


def test_save_then_load_round_trips_images(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.save(
        conversation_id="c-img",
        messages=[
            Message(
                role="user",
                content="what is this? [Image #1]",
                images=[ImageContent(data="aGVsbG8=", media_type="image/png")],
            )
        ],
    )
    (loaded,) = store.load_messages("c-img")
    assert loaded.images == [ImageContent(data="aGVsbG8=", media_type="image/png")]


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


def test_tokens_accumulate_across_saves(tmp_path: Path) -> None:
    store = _store(tmp_path)
    messages = [Message(role="user", content="hi")]
    store.save(conversation_id="c-tok", messages=messages, tokens_delta=1200)
    store.save(conversation_id="c-tok", messages=messages, tokens_delta=800)

    record = store.load("c-tok")
    assert record is not None
    assert record["total_tokens"] == 2000


def test_a_negative_delta_never_subtracts(tmp_path: Path) -> None:
    # The ledger is process-wide: a resumed thread can see it move backwards
    # relative to its own baseline, and that must not eat the stored total.
    store = _store(tmp_path)
    messages = [Message(role="user", content="hi")]
    store.save(conversation_id="c-neg", messages=messages, tokens_delta=500)
    store.save(conversation_id="c-neg", messages=messages, tokens_delta=-9000)

    assert store.load("c-neg")["total_tokens"] == 500


def test_the_summary_survives_a_later_save_but_goes_stale(tmp_path: Path) -> None:
    store = _store(tmp_path)
    messages = [Message(role="user", content="arruma o ranking")]
    store.save(conversation_id="c-sum", messages=messages)
    assert store.set_summary("c-sum", "Swapped the picker ordering.") is True

    fresh = store.load("c-sum")
    assert fresh["summary"] == "Swapped the picker ordering."
    assert fresh["summary_at"] == fresh["updated_at"]

    store.save(
        conversation_id="c-sum",
        messages=[*messages, Message(role="user", content="e os testes?")],
    )
    stale = store.load("c-sum")
    assert stale["summary"] == "Swapped the picker ordering."
    assert stale["summary_at"] < stale["updated_at"], "a new turn makes the summary stale"


def test_summarizing_a_conversation_that_is_gone_says_so(tmp_path: Path) -> None:
    assert _store(tmp_path).set_summary("nope", "x") is False


def test_list_carries_the_summary_and_the_token_total(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.save(
        conversation_id="c-list",
        messages=[Message(role="user", content="hi")],
        tokens_delta=4321,
    )
    store.set_summary("c-list", "Fez o que foi pedido.")

    (card,) = store.list()
    assert card["summary"] == "Fez o que foi pedido."
    assert card["total_tokens"] == 4321
    assert card["message_count"] == 1
