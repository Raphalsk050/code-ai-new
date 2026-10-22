"""The task board's data and the one set of operations every front end sends."""

from __future__ import annotations

import json

import pytest

from code_ai.core.board import BoardError, apply_operations, load_board, save_board
from code_ai.core.board.models import (
    ROLE_DONE,
    ROLE_TODO,
    SUB_DONE,
    TASK_DONE,
    TASK_DRAFT,
    TASK_READY,
    TASK_RUNNING,
    TASK_STOPPED,
    Board,
)


def apply(board: Board, *ops: dict) -> Board:
    updated, _ = apply_operations(board, list(ops))
    return updated


def feature_board() -> Board:
    return apply(
        Board.empty(),
        {
            "op": "add_task",
            "title": "Login page",
            "description": "Email and password",
            "subtasks": [
                {"title": "Form", "checklist": ["has labels"], "criteria": ["submits"]},
                {"title": "API call", "depends_on": [1]},
            ],
            "criteria": ["a user can sign in"],
        },
    )


def test_a_new_board_has_the_three_default_lanes() -> None:
    board = Board.empty()
    assert [(lane.id, lane.title, lane.role) for lane in board.lanes] == [
        ("todo", "To Do", "todo"),
        ("in_progress", "In Progress", "in_progress"),
        ("done", "Done", "done"),
    ]


def test_add_task_with_subtasks_numbers_them_and_resolves_places() -> None:
    board = feature_board()
    task = board.task("T1")
    assert task is not None
    assert task.lane == "todo"
    assert task.status == TASK_READY
    assert [sub.id for sub in task.subtasks] == ["T1.1", "T1.2"]
    assert task.subtasks[1].depends_on == ["T1.1"]
    assert [item.text for item in task.subtasks[0].checklist] == ["has labels"]
    assert task.criteria[0].text == "a user can sign in"
    assert len({item.id for item in [*task.criteria, *task.subtasks[0].checklist]}) == 2


def test_a_task_without_subtasks_is_a_draft_until_it_gets_one() -> None:
    board = apply(Board.empty(), {"op": "add_task", "title": "Dark mode"})
    assert board.task("T1").status == TASK_DRAFT
    board = apply(board, {"op": "add_subtask", "task": "T1", "title": "Theme tokens"})
    assert board.task("T1").status == TASK_READY
    board = apply(board, {"op": "remove_subtask", "task": "T1", "subtask": "T1.1"})
    assert board.task("T1").status == TASK_DRAFT


def test_lanes_can_be_added_renamed_and_removed_but_not_the_defaults() -> None:
    board = apply(Board.empty(), {"op": "add_lane", "title": "Review"})
    assert [lane.title for lane in board.lanes] == ["To Do", "In Progress", "Review", "Done"]
    board = apply(board, {"op": "add_task", "title": "x", "lane": "Review"})
    board = apply(board, {"op": "rename_lane", "lane": "Review", "title": "QA"})
    board = apply(board, {"op": "remove_lane", "lane": "QA"})
    assert [lane.title for lane in board.lanes] == ["To Do", "In Progress", "Done"]
    # Its card went back to To Do instead of disappearing with it.
    assert board.task("T1").lane == "todo"
    board = apply(board, {"op": "rename_lane", "lane": "done", "title": "Feito"})
    assert board.lane_for(ROLE_DONE).title == "Feito"
    with pytest.raises(BoardError, match="renamed only"):
        apply(board, {"op": "remove_lane", "lane": "Feito"})


def test_a_refused_operation_leaves_the_whole_batch_unapplied() -> None:
    board = Board.empty()
    with pytest.raises(BoardError, match="Operation 2"):
        apply_operations(
            board, [{"op": "add_task", "title": "a"}, {"op": "remove_task", "task": "T9"}]
        )
    assert board.tasks == []


def test_dependency_loops_are_refused() -> None:
    board = feature_board()
    with pytest.raises(BoardError, match="wait on itself"):
        apply(
            board, {"op": "update_subtask", "task": "T1", "subtask": "T1.1", "depends_on": ["T1.2"]}
        )
    board = apply(board, {"op": "add_task", "title": "Logout", "depends_on": ["T1"]})
    with pytest.raises(BoardError, match="wait on itself"):
        apply(board, {"op": "update_task", "task": "T1", "depends_on": ["T2"]})


def test_a_task_a_run_holds_cannot_be_edited() -> None:
    board = feature_board()
    board.task("T1").status = TASK_RUNNING
    with pytest.raises(BoardError, match="being worked on"):
        apply(board, {"op": "update_task", "task": "T1", "title": "Other"})
    with pytest.raises(BoardError, match="being worked on"):
        apply(board, {"op": "add_item", "task": "T1", "list": "checklist", "text": "x"})


def test_moving_a_card_into_done_and_back_out() -> None:
    board = feature_board()
    board, applied = apply_operations(board, [{"op": "move_task", "task": "T1", "lane": "Done"}])
    assert board.task("T1").status == TASK_DONE
    assert applied.moved == [{"task": "T1", "from": "todo", "to": "done"}]
    board = apply(board, {"op": "move_task", "task": "T1", "lane": "todo"})
    assert board.task("T1").status == TASK_READY


def test_checklist_and_criteria_items_on_tasks_and_subtasks() -> None:
    board = feature_board()
    board, applied = apply_operations(
        board,
        [
            {"op": "add_item", "task": "T1", "list": "checklist", "text": "README updated"},
            {
                "op": "add_item",
                "task": "T1",
                "subtask": "T1.2",
                "list": "criteria",
                "text": "401 on bad password",
            },
        ],
    )
    check_id, criterion_id = applied.created
    board = apply(
        board,
        {"op": "update_item", "task": "T1", "list": "checklist", "item": check_id, "done": True},
    )
    task = board.task("T1")
    assert task.checklist[0].done is True
    assert task.subtasks[1].criteria[0].id == criterion_id
    board = apply(
        board,
        {
            "op": "remove_item",
            "task": "T1",
            "subtask": "T1.2",
            "list": "criteria",
            "item": criterion_id,
        },
    )
    assert board.task("T1").subtasks[1].criteria == []


def test_ticking_a_subtask_by_hand_marks_it_done() -> None:
    board = apply(
        feature_board(), {"op": "update_subtask", "task": "T1", "subtask": "Form", "done": True}
    )
    assert board.task("T1").subtasks[0].status == SUB_DONE


def test_cards_are_placed_and_ordered_within_their_lane() -> None:
    board = Board.empty()
    for title in ("a", "b", "c"):
        board = apply(board, {"op": "add_task", "title": title})
    board = apply(board, {"op": "move_task", "task": "c", "lane": "todo", "index": 0})
    assert [task.title for task in board.tasks_in("todo")] == ["c", "a", "b"]
    board = apply(board, {"op": "clear_lane", "lane": "To Do"})
    assert board.tasks == []


def test_the_board_round_trips_and_a_dead_run_is_marked_stopped(tmp_path) -> None:
    board = feature_board()
    board.task("T1").status = TASK_RUNNING
    board.task("T1").subtasks[0].status = "running"
    path = tmp_path / "board.json"
    save_board(path, board)

    loaded = load_board(path)
    assert loaded.task("T1").status == TASK_STOPPED
    assert loaded.task("T1").subtasks[0].status == "stopped"
    assert loaded.task("T1").title == "Login page"
    # Ids keep counting from where they were.
    loaded = apply(loaded, {"op": "add_task", "title": "Next"})
    assert loaded.task("Next").id == "T2"


def test_an_unreadable_board_is_set_aside_not_overwritten(tmp_path) -> None:
    path = tmp_path / "board.json"
    path.write_text("{not json", encoding="utf-8")
    board = load_board(path)
    assert board.lane_for(ROLE_TODO).title == "To Do"
    assert (tmp_path / "board.json.unreadable").read_text(encoding="utf-8") == "{not json"


def test_a_hand_edited_board_gets_its_role_lanes_back(tmp_path) -> None:
    path = tmp_path / "board.json"
    path.write_text(
        json.dumps(
            {
                "lanes": [{"id": "x", "title": "Mine"}],
                "tasks": [{"id": "T1", "title": "t", "lane": "gone"}],
            }
        ),
        encoding="utf-8",
    )
    board = load_board(path)
    assert [lane.role for lane in board.lanes].count(ROLE_TODO) == 1
    assert board.lane_for(ROLE_DONE)
    assert board.task("T1").lane == board.lane_for(ROLE_TODO).id
