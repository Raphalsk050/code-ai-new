"""A message the user deletes is gone, with everything it led to and nothing else."""

from __future__ import annotations

from code_ai.context.conversation import ConversationState
from code_ai.providers.models import ToolCall, ToolResult


def _conversation() -> ConversationState:
    conversation = ConversationState(previous_response_id="resp_1")
    conversation.add_user("[Editor context] Active file: a.py (python)")
    conversation.add_user("first question")
    conversation.add_assistant(
        "", [ToolCall(id="t1", name="read_file", arguments={"path": "a.py"})]
    )
    conversation.add_tool_result(ToolResult(tool_call_id="t1", name="read_file", content="{}"))
    conversation.add_user("[runtime] a nudge from the runtime")
    conversation.add_assistant("first answer")
    conversation.add_user("[Editor context] Active file: b.py (python)")
    conversation.add_user("second question")
    conversation.add_assistant("second answer")
    conversation.add_user("third question")
    conversation.add_assistant("third answer")
    return conversation


def _said(conversation: ConversationState) -> list[str]:
    return [message.content for message in conversation.messages if message.content]


def test_a_message_goes_with_its_answer_and_its_editor_context() -> None:
    conversation = _conversation()

    assert conversation.drop_exchange("second question", from_end=1)

    assert _said(conversation) == [
        "[Editor context] Active file: a.py (python)",
        "first question",
        "{}",
        "[runtime] a nudge from the runtime",
        "first answer",
        "third question",
        "third answer",
    ]
    # A server holding the thread by id holds the one before the cut.
    assert conversation.previous_response_id is None


def test_the_tool_calls_and_notes_of_an_exchange_go_with_it() -> None:
    conversation = _conversation()

    assert conversation.drop_exchange("first question", from_end=2)

    assert _said(conversation) == [
        "[Editor context] Active file: b.py (python)",
        "second question",
        "second answer",
        "third question",
        "third answer",
    ]
    assert all(message.role != "tool" for message in conversation.messages)


def test_the_newest_message_can_go_and_so_can_several_at_once() -> None:
    newest = _conversation()
    assert newest.drop_exchange("third question")
    assert _said(newest)[-1] == "second answer"

    both = _conversation()
    assert both.drop_exchange("second question", from_end=0, count=2)
    assert _said(both)[-1] == "first answer"


def test_a_message_that_is_not_the_one_named_is_left_alone() -> None:
    conversation = _conversation()
    before = list(conversation.messages)

    # Folded into a summary since: the count the caller has is off by then.
    assert not conversation.drop_exchange("a question from before the summary", from_end=1)
    assert not conversation.drop_exchange("first question", from_end=7)
    assert not conversation.drop_exchange("[runtime] a nudge from the runtime", from_end=0)

    assert conversation.messages == before
    assert conversation.previous_response_id == "resp_1"


def test_the_newest_thing_the_user_said_is_not_a_note_from_the_runtime() -> None:
    conversation = _conversation()
    conversation.add_user("[runtime] one more nudge")

    assert conversation.last_said() == "third question"
    assert ConversationState().last_said() is None
