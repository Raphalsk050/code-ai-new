"""What reaches the board of each step a board agent takes."""

from __future__ import annotations

from code_ai.app.board_runner import BoardRunner
from code_ai.core.board import apply_operations
from code_ai.core.board.models import Board
from code_ai.events.models import EventEnvelope


def envelope(event_type: str, payload: dict) -> EventEnvelope:
    return EventEnvelope.create(
        event_type=event_type, session_id="s", sequence=1, payload=payload, source="test"
    )


async def test_tool_steps_and_words_reach_the_card_and_the_feed() -> None:
    board, _ = apply_operations(
        Board.empty(), [{"op": "add_task", "title": "API", "subtasks": ["Endpoint"]}]
    )
    emitted: list[tuple[str, dict]] = []

    async def mutate(change, persist=True, quiet=False):
        change(board)

    async def emit(event_type, payload):
        emitted.append((event_type, payload))

    runner = BoardRunner(
        runtime=None, mutate=mutate, read=lambda: board, emit=emit, max_parallel=2, run_id="r"
    )
    handler = runner._forwarder("T1", "T1.1", "Lovelace")
    await handler(envelope("model.stream.delta", {"text": "Reading the routes first."}))
    await handler(
        envelope(
            "tool.call.started",
            {"name": "read_file", "target": "src/routes.py", "tool_call_id": "c1"},
        )
    )
    sub = board.task("T1").subtask("T1.1")
    assert sub.activity == "read_file src/routes.py"
    assert board.task("T1").activity == "Lovelace: read_file src/routes.py"
    await handler(envelope("tool.call.completed", {"name": "read_file", "tool_call_id": "c1"}))
    await handler(envelope("model.response.completed", {}))

    feed = [payload for name, payload in emitted if name == "board.activity"]
    assert [(item["kind"], item["state"]) for item in feed] == [
        ("say", ""),
        ("tool", "started"),
        ("tool", "done"),
    ]
    assert feed[0]["text"].endswith("Reading the routes first.")
    assert feed[1]["tool"] == "read_file" and feed[1]["target"] == "src/routes.py"
    assert feed[1]["call"] == "c1" and feed[1]["subtask"] == "T1.1" and feed[1]["at"] > 0
