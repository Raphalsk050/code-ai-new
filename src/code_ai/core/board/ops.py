"""Every change to a board, as a list of operations applied all or nothing.

The panel, the terminal and the agent's tool all send the same operations, so
there is one place that decides what an edit may do: a task a run holds is not
edited, a role lane is not removed, a dependency that closes a loop is refused.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from code_ai.core.board.models import (
    BUSY_STATUSES,
    CRITERION_PENDING,
    ROLE_DONE,
    ROLE_TODO,
    SUB_DONE,
    SUB_PENDING,
    TASK_DONE,
    TASK_DRAFT,
    TASK_READY,
    Board,
    CheckItem,
    Criterion,
    Lane,
    SubTask,
    Task,
)

# Kept short and listed for the agent in the tool's description.
OPERATIONS = (
    "add_lane",
    "rename_lane",
    "remove_lane",
    "move_lane",
    "add_task",
    "update_task",
    "remove_task",
    "move_task",
    "clear_lane",
    "add_subtask",
    "update_subtask",
    "remove_subtask",
    "move_subtask",
    "add_item",
    "update_item",
    "remove_item",
    "remove_attachment",
)

_LISTS = {"checklist": "c", "criteria": "v"}

_MAX_TITLE = 200
_MAX_TEXT = 20_000


class BoardError(ValueError):
    """An operation the board refuses; nothing of the batch was applied."""


@dataclass(slots=True)
class Applied:
    """What a batch did: the ids it made, and the cards that changed lane."""

    created: list[str] = field(default_factory=list)
    moved: list[dict[str, str]] = field(default_factory=list)


def apply_operations(board: Board, operations: list[dict[str, Any]]) -> tuple[Board, Applied]:
    """A new board with ``operations`` applied, or :class:`BoardError` and no change."""

    if not isinstance(operations, list) or not operations:
        raise BoardError("No operations were given.")
    work = copy.deepcopy(board)
    before = {task.id: task.lane for task in board.tasks}
    applied = Applied()
    for index, operation in enumerate(operations, start=1):
        if not isinstance(operation, dict):
            raise BoardError(f"Operation {index} is not an object.")
        name = str(operation.get("op") or "")
        handler = _HANDLERS.get(name)
        if handler is None:
            raise BoardError(
                f"Operation {index}: unknown op {name!r}. Known: {', '.join(OPERATIONS)}."
            )
        try:
            created = handler(work, operation)
        except BoardError as exc:
            raise BoardError(f"Operation {index} ({name}): {exc}") from None
        if created:
            applied.created.append(created)
    for task in work.tasks:
        previous = before.get(task.id)
        if previous is not None and previous != task.lane:
            applied.moved.append({"task": task.id, "from": previous, "to": task.lane})
    return work, applied


# -- lanes ------------------------------------------------------------------


def _add_lane(board: Board, op: dict[str, Any]) -> str:
    title = _title(op.get("title"), "lane")
    if board.lane(title) is not None:
        raise BoardError(f"There is already a lane called {title!r}.")
    lane = Lane(id=board.new_lane_id(), title=title)
    after = str(op.get("after") or "").strip()
    if after:
        anchor = _lane(board, after)
        board.lanes.insert(board.lanes.index(anchor) + 1, lane)
    elif op.get("index") is not None:
        board.lanes.insert(_index(op.get("index"), len(board.lanes)), lane)
    else:
        # Before Done: a lane added without a place is work in between.
        done = board.lanes.index(board.lane_for(ROLE_DONE))
        board.lanes.insert(done, lane)
    return lane.id


def _rename_lane(board: Board, op: dict[str, Any]) -> None:
    lane = _lane(board, op.get("lane"))
    title = _title(op.get("title"), "lane")
    other = board.lane(title)
    if other is not None and other is not lane:
        raise BoardError(f"There is already a lane called {title!r}.")
    lane.title = title


def _remove_lane(board: Board, op: dict[str, Any]) -> None:
    lane = _lane(board, op.get("lane"))
    if lane.role:
        raise BoardError(f"{lane.title!r} is one of the board's own lanes; it can be renamed only.")
    cards = board.tasks_in(lane.id)
    if any(task.busy for task in cards):
        raise BoardError(f"A task in {lane.title!r} is being worked on.")
    # Its cards go back to To Do rather than with it.
    todo = board.lane_for(ROLE_TODO).id
    for task in cards:
        task.lane = todo
    board.lanes.remove(lane)


def _move_lane(board: Board, op: dict[str, Any]) -> None:
    lane = _lane(board, op.get("lane"))
    board.lanes.remove(lane)
    board.lanes.insert(_index(op.get("index"), len(board.lanes)), lane)


# -- tasks ------------------------------------------------------------------


def _add_task(board: Board, op: dict[str, Any]) -> str:
    lane = _lane(board, op.get("lane")) if op.get("lane") else board.lane_for(ROLE_TODO)
    task = Task(
        id=board.new_task_id(),
        title=_title(op.get("title"), "task"),
        lane=lane.id,
        description=_text(op.get("description")),
    )
    subtasks = op.get("subtasks") or []
    if not isinstance(subtasks, list):
        raise BoardError("subtasks must be a list.")
    for raw in subtasks:
        if isinstance(raw, str):
            raw = {"title": raw}
        if not isinstance(raw, dict):
            raise BoardError("Each sub-task is an object with a title.")
        task.subtasks.append(_new_subtask(board, task, raw, resolve_later=True))
    # Dependencies inside one add_task may name a sibling by its 1-based place.
    for sub, raw in zip(task.subtasks, subtasks, strict=True):
        refs = raw.get("depends_on") if isinstance(raw, dict) else None
        sub.depends_on = _sub_refs(task, sub, refs)
    task.checklist = _items(board, "checklist", op.get("checklist"))
    task.criteria = _items(board, "criteria", op.get("criteria"))
    board.tasks.insert(_task_slot(board, lane.id, op.get("index")), task)
    task.depends_on = _task_refs(board, task, op.get("depends_on"))
    _settle_status(board, task)
    return task.id


def _update_task(board: Board, op: dict[str, Any]) -> None:
    task = _editable(board, op.get("task"))
    if "title" in op:
        task.title = _title(op.get("title"), "task")
    if "description" in op:
        task.description = _text(op.get("description"))
    if "depends_on" in op:
        task.depends_on = _task_refs(board, task, op.get("depends_on"))
    if "checklist" in op:
        task.checklist = _items(board, "checklist", op.get("checklist"))
    if "criteria" in op:
        task.criteria = _items(board, "criteria", op.get("criteria"))


def _remove_task(board: Board, op: dict[str, Any]) -> None:
    task = _editable(board, op.get("task"))
    board.tasks.remove(task)
    for other in board.tasks:
        other.depends_on = [ref for ref in other.depends_on if ref != task.id]


def _move_task(board: Board, op: dict[str, Any]) -> None:
    task = _editable(board, op.get("task"))
    lane = _lane(board, op.get("lane"))
    was_done = board.role_of(task) == ROLE_DONE
    board.tasks.remove(task)
    task.lane = lane.id
    board.tasks.insert(_task_slot(board, lane.id, op.get("index")), task)
    if lane.role == ROLE_DONE:
        task.status = TASK_DONE
        task.error = ""
    elif was_done:
        # Taken out of Done to be done again: what is already done stays done.
        task.status = TASK_DRAFT
        _settle_status(board, task)


def _clear_lane(board: Board, op: dict[str, Any]) -> None:
    lane = _lane(board, op.get("lane"))
    cards = board.tasks_in(lane.id)
    if any(task.busy for task in cards):
        raise BoardError(f"A task in {lane.title!r} is being worked on.")
    gone = {task.id for task in cards}
    board.tasks = [task for task in board.tasks if task.id not in gone]
    for other in board.tasks:
        other.depends_on = [ref for ref in other.depends_on if ref not in gone]


# -- sub-tasks --------------------------------------------------------------


def _add_subtask(board: Board, op: dict[str, Any]) -> str:
    task = _editable(board, op.get("task"))
    sub = _new_subtask(board, task, op)
    if op.get("index") is not None:
        task.subtasks.insert(_index(op.get("index"), len(task.subtasks)), sub)
    else:
        task.subtasks.append(sub)
    _settle_status(board, task)
    return sub.id


def _update_subtask(board: Board, op: dict[str, Any]) -> None:
    task = _editable(board, op.get("task"))
    sub = _subtask(task, op.get("subtask"))
    if "title" in op:
        sub.title = _title(op.get("title"), "sub-task")
    if "description" in op:
        sub.description = _text(op.get("description"))
    if "depends_on" in op:
        sub.depends_on = _sub_refs(task, sub, op.get("depends_on"))
    if "checklist" in op:
        sub.checklist = _items(board, "checklist", op.get("checklist"))
    if "criteria" in op:
        sub.criteria = _items(board, "criteria", op.get("criteria"))
    if "done" in op:
        # Ticked by hand: the run will not redo it.
        sub.status = SUB_DONE if op.get("done") else SUB_PENDING
        if not op.get("done"):
            sub.error = ""


def _remove_subtask(board: Board, op: dict[str, Any]) -> None:
    task = _editable(board, op.get("task"))
    sub = _subtask(task, op.get("subtask"))
    task.subtasks.remove(sub)
    for other in task.subtasks:
        other.depends_on = [ref for ref in other.depends_on if ref != sub.id]
    _settle_status(board, task)


def _move_subtask(board: Board, op: dict[str, Any]) -> None:
    task = _editable(board, op.get("task"))
    sub = _subtask(task, op.get("subtask"))
    task.subtasks.remove(sub)
    task.subtasks.insert(_index(op.get("index"), len(task.subtasks)), sub)


# -- checklist and criteria ---------------------------------------------------


def _add_item(board: Board, op: dict[str, Any]) -> str:
    owner, name = _owner(board, op)
    text = _title(op.get("text"), "item")
    items = getattr(owner, name)
    if name == "checklist":
        item: CheckItem | Criterion = CheckItem(board.new_item_id("c"), text, bool(op.get("done")))
    else:
        item = Criterion(board.new_item_id("v"), text)
    items.append(item)
    return item.id


def _update_item(board: Board, op: dict[str, Any]) -> None:
    owner, name = _owner(board, op)
    item = _item(getattr(owner, name), op.get("item"))
    if "text" in op:
        item.text = _title(op.get("text"), "item")
        if isinstance(item, Criterion):
            # Reworded, it has not been judged yet.
            item.status = CRITERION_PENDING
            item.note = ""
    if "done" in op and isinstance(item, CheckItem):
        item.done = bool(op.get("done"))


def _remove_item(board: Board, op: dict[str, Any]) -> None:
    owner, name = _owner(board, op)
    items = getattr(owner, name)
    items.remove(_item(items, op.get("item")))


def _remove_attachment(board: Board, op: dict[str, Any]) -> None:
    # The file goes with it: the service sweeps what nothing refers to any more.
    task = _editable(board, op.get("task"))
    task.attachments.remove(_item_named(task.attachments, op.get("attachment")))


def _item_named(items: list[Any], ref: Any) -> Any:
    key = str(ref or "").strip().lower()
    for item in items:
        if item.id.lower() == key or item.name.strip().lower() == key:
            return item
    raise BoardError(f"No attachment {ref!r}.")


_HANDLERS: dict[str, Any] = {
    "add_lane": _add_lane,
    "rename_lane": _rename_lane,
    "remove_lane": _remove_lane,
    "move_lane": _move_lane,
    "add_task": _add_task,
    "update_task": _update_task,
    "remove_task": _remove_task,
    "move_task": _move_task,
    "clear_lane": _clear_lane,
    "add_subtask": _add_subtask,
    "update_subtask": _update_subtask,
    "remove_subtask": _remove_subtask,
    "move_subtask": _move_subtask,
    "add_item": _add_item,
    "update_item": _update_item,
    "remove_item": _remove_item,
    "remove_attachment": _remove_attachment,
}


# -- helpers ------------------------------------------------------------------


def _title(value: Any, what: str) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        raise BoardError(f"A {what} needs a title.")
    return text[:_MAX_TITLE]


def _text(value: Any) -> str:
    return str(value or "").strip()[:_MAX_TEXT]


def _index(value: Any, size: int) -> int:
    try:
        index = int(value)
    except (TypeError, ValueError):
        raise BoardError(f"index must be a number, not {value!r}.") from None
    return max(0, min(index, size))


def _lane(board: Board, ref: Any) -> Lane:
    lane = board.lane(str(ref or ""))
    if lane is None:
        names = ", ".join(f"{item.id} ({item.title})" for item in board.lanes)
        raise BoardError(f"No lane {ref!r}. Lanes: {names}.")
    return lane


def _task(board: Board, ref: Any) -> Task:
    task = board.task(str(ref or ""))
    if task is None:
        raise BoardError(f"No task {ref!r}.")
    return task


def _editable(board: Board, ref: Any) -> Task:
    task = _task(board, ref)
    if task.status in BUSY_STATUSES:
        raise BoardError(f"{task.id} is being worked on; stop the run to change it.")
    return task


def _subtask(task: Task, ref: Any) -> SubTask:
    sub = task.subtask(str(ref or ""))
    if sub is None:
        raise BoardError(f"{task.id} has no sub-task {ref!r}.")
    return sub


def _owner(board: Board, op: dict[str, Any]) -> tuple[Task | SubTask, str]:
    name = str(op.get("list") or "")
    if name not in _LISTS:
        raise BoardError("list is checklist or criteria.")
    task = _editable(board, op.get("task"))
    if op.get("subtask"):
        return _subtask(task, op.get("subtask")), name
    return task, name


def _item(items: list[Any], ref: Any) -> Any:
    key = str(ref or "").strip().lower()
    for item in items:
        if item.id.lower() == key or item.text.strip().lower() == key:
            return item
    raise BoardError(f"No item {ref!r}.")


def _items(board: Board, name: str, value: Any) -> list[Any]:
    """A checklist or criteria list from strings or objects; ids are the board's."""

    if value is None:
        return []
    if not isinstance(value, list):
        raise BoardError(f"{name} must be a list.")
    items: list[Any] = []
    for raw in value:
        text = raw.get("text") if isinstance(raw, dict) else raw
        text = " ".join(str(text or "").split())
        if not text:
            continue
        if name == "checklist":
            done = bool(raw.get("done")) if isinstance(raw, dict) else False
            items.append(CheckItem(board.new_item_id("c"), text[:_MAX_TITLE], done))
        else:
            items.append(Criterion(board.new_item_id("v"), text[:_MAX_TITLE]))
    return items


def _new_subtask(
    board: Board, task: Task, raw: dict[str, Any], *, resolve_later: bool = False
) -> SubTask:
    sub = SubTask(
        id=task.new_subtask_id(),
        title=_title(raw.get("title"), "sub-task"),
        description=_text(raw.get("description")),
        checklist=_items(board, "checklist", raw.get("checklist")),
        criteria=_items(board, "criteria", raw.get("criteria")),
    )
    if not resolve_later:
        sub.depends_on = _sub_refs(task, sub, raw.get("depends_on"))
    return sub


def _sub_refs(task: Task, sub: SubTask, refs: Any) -> list[str]:
    """Sibling ids from ids, titles or 1-based places, refusing a loop."""

    resolved: list[str] = []
    for ref in _ref_list(refs):
        other: SubTask | None = None
        if isinstance(ref, int) or (isinstance(ref, str) and ref.strip().isdigit()):
            place = int(ref)
            if 1 <= place <= len(task.subtasks):
                other = task.subtasks[place - 1]
        else:
            other = task.subtask(str(ref))
        if other is None:
            raise BoardError(f"{sub.id} depends on {ref!r}, which is not a sub-task of {task.id}.")
        if other is sub:
            raise BoardError(f"{sub.id} cannot depend on itself.")
        if other.id not in resolved:
            resolved.append(other.id)
    graph = {item.id: list(item.depends_on) for item in task.subtasks}
    graph[sub.id] = resolved
    if _has_cycle(graph):
        raise BoardError(f"Those dependencies make {sub.id} wait on itself.")
    return resolved


def _task_refs(board: Board, task: Task, refs: Any) -> list[str]:
    resolved: list[str] = []
    for ref in _ref_list(refs):
        other = board.task(str(ref))
        if other is None:
            raise BoardError(f"{task.id} depends on {ref!r}, which is not a task.")
        if other is task:
            raise BoardError(f"{task.id} cannot depend on itself.")
        if other.id not in resolved:
            resolved.append(other.id)
    graph = {item.id: list(item.depends_on) for item in board.tasks}
    graph[task.id] = resolved
    if _has_cycle(graph):
        raise BoardError(f"Those dependencies make {task.id} wait on itself.")
    return resolved


def _ref_list(refs: Any) -> list[Any]:
    if refs is None or refs == "":
        return []
    if isinstance(refs, (str, int)):
        return [refs]
    if not isinstance(refs, list):
        raise BoardError("depends_on must be a list.")
    return refs


def _has_cycle(graph: dict[str, list[str]]) -> bool:
    state: dict[str, int] = {}

    def visit(node: str) -> bool:
        mark = state.get(node, 0)
        if mark == 1:
            return True
        if mark == 2:
            return False
        state[node] = 1
        if any(visit(nxt) for nxt in graph.get(node, ())):
            return True
        state[node] = 2
        return False

    return any(visit(node) for node in graph)


def _task_slot(board: Board, lane_id: str, index: Any) -> int:
    """Where in ``board.tasks`` a card lands to be ``index``-th in its lane (last without one)."""

    positions = [i for i, task in enumerate(board.tasks) if task.lane == lane_id]
    if index is None:
        return positions[-1] + 1 if positions else len(board.tasks)
    place = _index(index, len(positions))
    if place < len(positions):
        return positions[place]
    return positions[-1] + 1 if positions else len(board.tasks)


def _settle_status(board: Board, task: Task) -> None:
    """Draft until it has sub-tasks, ready once it does; nothing else is touched."""

    if task.status in (TASK_DRAFT, TASK_READY):
        task.status = TASK_READY if task.subtasks else TASK_DRAFT
    if board.role_of(task) == ROLE_DONE:
        task.status = TASK_DONE


def reset_for_rerun(task: Task) -> None:
    """Back to the start: every sub-task to do again and every criterion unjudged."""

    for sub in task.subtasks:
        sub.status = SUB_PENDING
        sub.error = ""
        sub.report = ""
        sub.agent = ""
    for item in task.criteria:
        item.status = CRITERION_PENDING
        item.note = ""
    task.error = ""
    task.report = ""
