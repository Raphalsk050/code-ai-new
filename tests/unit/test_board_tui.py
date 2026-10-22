"""The task board in the terminal, driven through the real Textual screens: the
same lanes, cards, editors and flight the panel has, from the keyboard.
"""

from __future__ import annotations

import asyncio
import time

from textual.widgets import Input, TextArea

from code_ai.app.board import BoardService
from code_ai.ui.terminal.app import create_terminal_app
from code_ai.ui.terminal.board import (
    BoardCard,
    BoardLaneView,
    BoardScreen,
    BoardSubCard,
    SubTaskEditor,
    TaskEditor,
    TextPrompt,
)
from tests.unit.test_terminal_ui import FakeTerminalApplication


class _App(FakeTerminalApplication):
    """The terminal fake with a real board on its event bus."""

    def __init__(self, tmp_path) -> None:
        super().__init__(tmp_path)
        self.board = BoardService(
            path=tmp_path / "board.json",
            event_bus=self.event_bus,
            runtime=lambda: None,
            max_parallel=lambda: 4,
        )

    def unsubscribe(self, subscriber) -> None:
        if subscriber in self.subscribers:
            self.subscribers.remove(subscriber)


async def _until(pilot, check, timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not check():
        assert asyncio.get_running_loop().time() < deadline, "timed out"
        await pilot.pause(0.05)


async def _open_board(terminal_app, pilot) -> BoardScreen:
    terminal_app.query_one("#input", TextArea).value = "/board"
    await pilot.press("enter")
    await _until(pilot, lambda: isinstance(terminal_app.screen, BoardScreen))
    return terminal_app.screen


async def test_the_board_screen_adds_edits_and_moves_tasks(tmp_path) -> None:
    fake = _App(tmp_path)
    terminal_app = create_terminal_app(fake)
    async with terminal_app.run_test(size=(150, 50)) as pilot:
        board = await _open_board(terminal_app, pilot)
        lanes = [lane.lane["title"] for lane in board.query(".board-lane").results()]
        assert lanes == ["To Do", "In Progress", "Done"]

        # n asks for a title and adds the task to To Do.
        await pilot.press("n")
        await _until(pilot, lambda: isinstance(terminal_app.screen, TextPrompt))
        terminal_app.screen.query_one(Input).value = "Login page"
        await pilot.press("enter")
        await _until(pilot, lambda: bool(board.query(BoardCard)))
        assert fake.board.board.task("T1").lane == "todo"

        # Enter opens it: a sub-task, a checklist item and a criterion.
        board.query_one(BoardCard).focus()
        await pilot.press("enter")
        await _until(pilot, lambda: isinstance(terminal_app.screen, TaskEditor))
        editor = terminal_app.screen
        for field_id, text in (
            ("#task-add-sub", "Form"),
            ("#task-add-checklist", "labels on every field"),
            ("#task-add-criteria", "a user can sign in"),
        ):
            field = editor.query_one(field_id, Input)
            field.focus()
            field.value = text
            await pilot.press("enter")
            await pilot.pause(0.1)
        task = fake.board.board.task("T1")
        assert [sub.title for sub in task.subtasks] == ["Form"]
        assert task.status == "ready"
        assert task.checklist[0].text == "labels on every field"
        assert task.criteria[0].text == "a user can sign in"

        # The sub-task has its own editor.
        await _until(pilot, lambda: bool(editor.query("#task-subedit-T1_1")))
        editor.query_one("#task-subedit-T1_1").press()
        await _until(pilot, lambda: isinstance(terminal_app.screen, SubTaskEditor))
        sub_editor = terminal_app.screen
        field = sub_editor.query_one("#sub-add-criteria", Input)
        field.focus()
        field.value = "renders without errors"
        await pilot.press("enter")
        await pilot.pause(0.1)
        assert fake.board.board.task("T1").subtasks[0].criteria[0].text == "renders without errors"
        await pilot.press("escape")
        await pilot.press("escape")
        await _until(pilot, lambda: terminal_app.screen is board)

        # a adds a lane, before Done.
        await pilot.press("a")
        await _until(pilot, lambda: isinstance(terminal_app.screen, TextPrompt))
        terminal_app.screen.query_one(Input).value = "Review"
        await pilot.press("enter")
        await _until(pilot, lambda: len(fake.board.board.lanes) == 4)
        assert [lane.title for lane in fake.board.board.lanes][2] == "Review"

        # Shift+Right carries the card to the next lane, flying over the lanes.
        board.query_one(BoardCard).focus()
        await pilot.press("shift+right")
        await _until(pilot, lambda: fake.board.board.task("T1").lane == "in_progress")
        await _until(pilot, lambda: bool(board.query(".board-ghost")), timeout=2)
        await _until(pilot, lambda: not board.query(".board-ghost"), timeout=3)
        card = board.query_one(BoardCard)
        assert card.lane_id == "in_progress"
        assert card.styles.opacity == 1.0

        # Esc goes back to the chat.
        await pilot.press("escape")
        await _until(pilot, lambda: not isinstance(terminal_app.screen, BoardScreen))


async def test_a_card_landing_in_done_flashes(tmp_path) -> None:
    fake = _App(tmp_path)
    await fake.board.apply([{"op": "add_task", "title": "Export", "subtasks": ["Writer"]}])
    terminal_app = create_terminal_app(fake)
    async with terminal_app.run_test(size=(150, 40)) as pilot:
        board = await _open_board(terminal_app, pilot)
        await fake.board.apply([{"op": "move_task", "task": "T1", "lane": "done"}])
        await _until(pilot, lambda: bool(board.query(".board-card.landed")), timeout=3)
        assert fake.board.board.task("T1").status == "done"


async def test_planned_tasks_wait_for_confirmation_on_the_board(tmp_path) -> None:
    fake = _App(tmp_path)
    await fake.board.apply([{"op": "add_task", "title": "Search", "subtasks": ["Index"]}])
    terminal_app = create_terminal_app(fake)
    async with terminal_app.run_test(size=(150, 40)) as pilot:
        await pilot.pause(0.2)
        await fake.board.request_review(["T1"], message="Check the split")
        await pilot.pause(0.2)
        board = await _open_board(terminal_app, pilot)
        banner = str(board.query_one("#board-review").render())
        assert "1 task(s) waiting for your confirmation" in banner


async def test_board_subcommands_from_the_prompt(tmp_path) -> None:
    fake = _App(tmp_path)
    terminal_app = create_terminal_app(fake)
    async with terminal_app.run_test(size=(150, 40)) as pilot:
        terminal_app.query_one("#input", TextArea).value = "/board add Dark mode"
        await pilot.press("enter")
        await _until(pilot, lambda: fake.board.board.task("T1") is not None)
        assert fake.board.board.task("T1").title == "Dark mode"
        terminal_app.query_one("#input", TextArea).value = "/board play"
        await pilot.press("enter")
        await pilot.pause(0.3)
        # No sub-agents in the fake: Play says so instead of running anything.
        assert fake.board.running is False


async def test_sub_cards_nest_under_their_task_and_follow_their_agent(tmp_path) -> None:
    fake = _App(tmp_path)
    await fake.board.apply(
        [{"op": "add_task", "title": "API", "subtasks": ["Schema", "Endpoint", "Tests"]}]
    )
    terminal_app = create_terminal_app(fake)
    async with terminal_app.run_test(size=(160, 50)) as pilot:
        board = await _open_board(terminal_app, pilot)
        lane = board.query_one("#board-lane-todo")
        assert [sub.sub_id for sub in lane.query(BoardSubCard)] == ["T1.1", "T1.2", "T1.3"]

        # The run pulls the task and two of its pieces; the third waits in To Do under a stub.
        def pull(state) -> None:
            task = state.task("T1")
            task.status = "running"
            task.lane = "in_progress"
            for sub, status in zip(task.subtasks, ("done", "running", "pending"), strict=True):
                sub.status = status
            task.subtasks[1].agent = "Lovelace"
            task.subtasks[1].started_at = time.time() - 65

        await fake.board.mutate(pull)
        await _until(pilot, lambda: bool(board.query("#board-lane-done BoardSubCard")))
        place = {
            sub.sub_id: next(item for item in sub.ancestors if isinstance(item, BoardLaneView)).id
            for sub in board.query(BoardSubCard)
        }
        assert place == {
            "T1.1": "board-lane-done",
            "T1.2": "board-lane-in_progress",
            "T1.3": "board-lane-todo",
        }
        assert [stub.task_id for stub in board.query("#board-lane-todo BoardStub")] == ["T1"]
        assert [stub.task_id for stub in board.query("#board-lane-done BoardStub")] == ["T1"]

        # The agent's step shows on its sub-card and lands in the log.
        await fake.emit(
            "board.activity",
            {
                "task": "T1",
                "subtask": "T1.2",
                "agent": "Lovelace",
                "kind": "tool",
                "state": "started",
                "tool": "edit_code",
                "target": "src/api.py",
                "call": "c1",
                "text": "",
                "at": time.time(),
            },
        )
        await pilot.pause(0.1)
        card = str(board.query_one("#board-card-T1_2").render())
        assert "Lovelace" in card and "editing src/api.py" in card and "1:0" in card
        # The first step showed the log, and it is in it.
        log = board.query_one("#board-activity")
        assert log.display
        assert any("editing src/api.py" in line.text for line in log.lines)
        await pilot.press("l")
        assert not board.query_one("#board-activity").display


async def test_files_are_attached_from_the_task_editor(tmp_path) -> None:
    fake = _App(tmp_path)
    await fake.board.apply([{"op": "add_task", "title": "Login"}])
    spec = tmp_path / "spec.md"
    spec.write_text("# Login", encoding="utf-8")
    terminal_app = create_terminal_app(fake)
    async with terminal_app.run_test(size=(150, 50)) as pilot:
        board = await _open_board(terminal_app, pilot)
        board.query_one(BoardCard).focus()
        await pilot.press("enter")
        await _until(pilot, lambda: isinstance(terminal_app.screen, TaskEditor))
        editor = terminal_app.screen
        field = editor.query_one("#task-attach", Input)
        field.focus()
        field.value = str(spec)
        await pilot.press("enter")
        await _until(pilot, lambda: bool(fake.board.board.task("T1").attachments))
        attachment = fake.board.board.task("T1").attachments[0]
        assert attachment.name == "spec.md"
        await _until(pilot, lambda: bool(editor.query(f"#task-remove-attachment-{attachment.id}")))
        editor.query_one(f"#task-remove-attachment-{attachment.id}").press()
        await _until(pilot, lambda: not fake.board.board.task("T1").attachments)
