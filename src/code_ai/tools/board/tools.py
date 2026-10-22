"""The agent's hands on the task board: read it, change it, plan it, hand it over.

The agent may do everything on the board except start it. ``board_submit`` puts
the planned tasks in front of the user, and only the user's Play implements
them. The tools carry the interaction capability: they act on what the user is
looking at, plan mode allows them, and sub-agents never get them.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from code_ai.core.board import OPERATIONS
from code_ai.core.board.models import Board, Task
from code_ai.core.errors import ToolArgumentError, ToolExecutionError
from code_ai.tools.base import ToolCapability, ToolContext
from code_ai.tools.pdf.common import parse_json_argument
from code_ai.tools.schema import tool_schema

BoardGetter = Callable[[], Any]

EDIT_GUIDE = """\
operations, applied in order, all or nothing. Lanes, tasks, sub-tasks and items
are named by id (T3, T3.2, c7, v4, lane-2) or by their exact title.
  {"op": "add_task", "title", "description", "lane", "subtasks": [{"title",
     "description", "checklist": [text], "criteria": [text], "depends_on": [1]}],
     "checklist": [text], "criteria": [text], "depends_on": [task ids]}
     a sub-task's depends_on names earlier sub-tasks by 1-based place here
  {"op": "update_task", "task", "title", "description", "depends_on", "checklist", "criteria"}
     a list given replaces the one there
  {"op": "remove_task", "task"} | {"op": "move_task", "task", "lane", "index"}
  {"op": "add_subtask", "task", "title", "description", "checklist", "criteria", "depends_on"}
  {"op": "update_subtask", "task", "subtask", "title", "description", "depends_on",
     "checklist", "criteria", "done"} | {"op": "remove_subtask", "task", "subtask"}
  {"op": "add_item", "task", "subtask"?, "list": "checklist"|"criteria", "text"}
  {"op": "update_item", ..., "item", "text", "done"} | {"op": "remove_item", ..., "item"}
  {"op": "add_lane", "title", "after"} | {"op": "rename_lane", "lane", "title"}
  {"op": "remove_lane", "lane"} | {"op": "move_lane", "lane", "index"}
  {"op": "clear_lane", "lane"} - removes every task in it
To Do, In Progress and Done can be renamed but not removed. Sub-tasks run in
parallel, one agent each: make two that touch the same files depend on each other."""

_STATUS_MARK = {
    "pending": " ",
    "queued": "~",
    "running": ">",
    "done": "x",
    "failed": "!",
    "stopped": "-",
    "blocked": "#",
}


def render_board(board: Board) -> str:
    """The whole board as text: every lane, every task with what is inside it."""

    lines: list[str] = []
    for lane in board.lanes:
        tasks = board.tasks_in(lane.id)
        role = f", {lane.role}" if lane.role else ""
        lines.append(f"## {lane.title} [{lane.id}{role}] - {len(tasks)} task(s)")
        for task in tasks:
            lines.extend(_render_task(task))
    return "\n".join(lines)


def _render_task(task: Task) -> list[str]:
    head = f"- {task.id} [{task.status}] {task.title}"
    if task.depends_on:
        head += f" (after {', '.join(task.depends_on)})"
    lines = [head]
    if task.description:
        lines.append(f"    {task.description[:600]}")
    for sub in task.subtasks:
        mark = _STATUS_MARK.get(sub.status, "?")
        after = f" (after {', '.join(sub.depends_on)})" if sub.depends_on else ""
        agent = f" - {sub.agent}" if sub.agent else ""
        lines.append(f"    [{mark}] {sub.id} {sub.title}{after}{agent}")
        if sub.description:
            lines.append(f"        {sub.description[:400]}")
        lines += [
            f"        check {item.id} [{'x' if item.done else ' '}] {item.text}"
            for item in sub.checklist
        ]
        lines += [
            f"        criterion {item.id} ({item.status}) {item.text}" for item in sub.criteria
        ]
        if sub.error:
            lines.append(f"        error: {sub.error[:300]}")
    lines += [
        f"    check {item.id} [{'x' if item.done else ' '}] {item.text}" for item in task.checklist
    ]
    lines += [f"    criterion {item.id} ({item.status}) {item.text}" for item in task.criteria]
    if task.error:
        lines.append(f"    error: {task.error[:400]}")
    return lines


def _service(getter: BoardGetter) -> Any:
    service = getter()
    if service is None:
        raise ToolExecutionError("This session has no task board.")
    return service


def _ids(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


class BoardViewTool:
    name = "board_view"
    description = (
        "Read the Code-AI task board: every lane (To Do, In Progress, Done and the user's "
        "own), each task with its status, sub-tasks, checklist and validation criteria, "
        "and whether a run is going. Read it before changing it."
    )
    capabilities = frozenset({ToolCapability.INTERACTION})
    input_schema = tool_schema({})

    def __init__(self, board: BoardGetter) -> None:
        self._board = board

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        service = _service(self._board)
        board = service.board
        return {
            "board": render_board(board),
            "running": service.running,
            "planning": service.planning,
            "tasks": len(board.tasks),
        }


class BoardEditTool:
    name = "board_edit"
    description = (
        "Change the Code-AI task board: add, edit, move or remove lanes, tasks (features), "
        "sub-tasks, checklist items and validation criteria. Use it to plan: break each "
        "feature into sub-tasks one agent can do each. A task the run is working on cannot "
        "be changed.\n" + EDIT_GUIDE
    )
    capabilities = frozenset({ToolCapability.INTERACTION})
    input_schema = tool_schema(
        {
            "operations": {
                "type": "string",
                "description": "A JSON array of operations: " + ", ".join(OPERATIONS) + ".",
            }
        },
        required=["operations"],
    )

    def __init__(self, board: BoardGetter) -> None:
        self._board = board

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        service = _service(self._board)
        operations = parse_json_argument(arguments.get("operations"), "operations")
        if isinstance(operations, dict):
            operations = [operations]
        if not isinstance(operations, list):
            raise ToolArgumentError("operations is a JSON array of operations.")
        result = await service.apply(operations, source="agent")
        if not result["ok"]:
            raise ToolExecutionError(f"The board was not changed: {result['message']}")
        return {"created": result["created"], "board": render_board(service.board)}


class BoardPlanTool:
    name = "board_plan"
    description = (
        "Have the board's planner agents break tasks into sub-tasks, checklists and "
        "criteria, one agent per task, all in parallel, reading the code as they go. "
        "It returns at once; the planned tasks then wait for the user's confirmation."
    )
    capabilities = frozenset({ToolCapability.INTERACTION})
    input_schema = tool_schema(
        {
            "tasks": {
                "type": "string",
                "description": "Comma-separated task ids. Empty plans every draft in To Do.",
            }
        }
    )

    def __init__(self, board: BoardGetter) -> None:
        self._board = board

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        service = _service(self._board)
        result = await service.plan(_ids(arguments.get("tasks")) or None)
        if not result["ok"]:
            raise ToolExecutionError(result["message"])
        return {
            "planning": result["tasks"],
            "note": "The planners work in the background. When they finish, the board asks "
            "the user to review the tasks and press Play.",
        }


class BoardSubmitTool:
    name = "board_submit"
    description = (
        "Put the planned board in front of the user for confirmation. Call it once the "
        "tasks and sub-tasks are written. You never start the work: the user checks the "
        "tasks, may add or change some, and presses Play."
    )
    capabilities = frozenset({ToolCapability.INTERACTION})
    input_schema = tool_schema(
        {
            "tasks": {
                "type": "string",
                "description": "Comma-separated ids to review. Empty: every ready task in To Do.",
            },
            "message": {
                "type": "string",
                "description": "One or two sentences for the user about what to check.",
            },
        }
    )

    def __init__(self, board: BoardGetter) -> None:
        self._board = board

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        service = _service(self._board)
        result = await service.request_review(
            _ids(arguments.get("tasks")) or None,
            source="agent",
            message=str(arguments.get("message") or "").strip(),
        )
        return {
            "waiting_for_user": result["tasks"],
            "note": "The board is open for the user's review. Tell them to check it and press "
            "Play; the work starts only then.",
        }
