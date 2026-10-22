"""The task board: lanes, tasks, sub-tasks, checklists and acceptance criteria.

One board per workspace, owned by the backend so every front end shows the same
one. Lanes are columns, as on a Jira board. Three of them have a role the run
needs - where work waits, where it happens, where it lands - and those can be
renamed but never removed; any other lane is the user's to add and take away.

Everything here is plain data with a lossless round trip to JSON. What changes
it is :mod:`code_ai.core.board.ops`; what runs it is the app's board runner.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

ROLE_TODO = "todo"
ROLE_DOING = "in_progress"
ROLE_DONE = "done"
ROLES = (ROLE_TODO, ROLE_DOING, ROLE_DONE)

DEFAULT_LANES = (
    (ROLE_TODO, "To Do"),
    (ROLE_DOING, "In Progress"),
    (ROLE_DONE, "Done"),
)

# A task's life. draft: nobody broke it down yet. ready: it has sub-tasks and
# waits for the user's Play. queued/running/verifying: a run holds it, and
# nothing may edit it. failed/stopped: the run let go of it; Play picks it up.
TASK_DRAFT = "draft"
TASK_PLANNING = "planning"
TASK_READY = "ready"
TASK_QUEUED = "queued"
TASK_RUNNING = "running"
TASK_VERIFYING = "verifying"
TASK_DONE = "done"
TASK_FAILED = "failed"
TASK_STOPPED = "stopped"
TASK_STATUSES = (
    TASK_DRAFT,
    TASK_PLANNING,
    TASK_READY,
    TASK_QUEUED,
    TASK_RUNNING,
    TASK_VERIFYING,
    TASK_DONE,
    TASK_FAILED,
    TASK_STOPPED,
)
# A run or a planner holds these; edits wait until it lets go.
BUSY_STATUSES = frozenset({TASK_PLANNING, TASK_QUEUED, TASK_RUNNING, TASK_VERIFYING})
# What Play picks up again without being asked by id.
RESUMABLE_STATUSES = frozenset({TASK_FAILED, TASK_STOPPED})

SUB_PENDING = "pending"
SUB_QUEUED = "queued"
SUB_RUNNING = "running"
SUB_DONE = "done"
SUB_FAILED = "failed"
SUB_STOPPED = "stopped"
SUB_BLOCKED = "blocked"
SUB_STATUSES = (
    SUB_PENDING,
    SUB_QUEUED,
    SUB_RUNNING,
    SUB_DONE,
    SUB_FAILED,
    SUB_STOPPED,
    SUB_BLOCKED,
)

CRITERION_PENDING = "pending"
CRITERION_PASSED = "passed"
CRITERION_FAILED = "failed"
CRITERION_STATUSES = (CRITERION_PENDING, CRITERION_PASSED, CRITERION_FAILED)

SCHEMA_VERSION = 1


@dataclass(slots=True)
class CheckItem:
    """Something that has to be true when the work is done, ticked by hand or by the verifier."""

    id: str
    text: str
    done: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "text": self.text, "done": self.done}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> CheckItem:
        return cls(
            id=str(raw.get("id") or ""), text=str(raw.get("text") or ""), done=bool(raw.get("done"))
        )


@dataclass(slots=True)
class Criterion:
    """A validation criterion: how the verifier decides the work is right."""

    id: str
    text: str
    status: str = CRITERION_PENDING
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "text": self.text, "status": self.status, "note": self.note}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Criterion:
        status = str(raw.get("status") or CRITERION_PENDING)
        return cls(
            id=str(raw.get("id") or ""),
            text=str(raw.get("text") or ""),
            status=status if status in CRITERION_STATUSES else CRITERION_PENDING,
            note=str(raw.get("note") or ""),
        )


@dataclass(slots=True)
class SubTask:
    """One piece of a task, given to one sub-agent."""

    id: str
    title: str
    description: str = ""
    status: str = SUB_PENDING
    # Sibling sub-tasks that must be done first: two pieces that touch the same
    # files must not run side by side.
    depends_on: list[str] = field(default_factory=list)
    checklist: list[CheckItem] = field(default_factory=list)
    criteria: list[Criterion] = field(default_factory=list)
    agent: str = ""
    report: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "status": self.status,
            "depends_on": list(self.depends_on),
            "checklist": [item.to_dict() for item in self.checklist],
            "criteria": [item.to_dict() for item in self.criteria],
            "agent": self.agent,
            "report": self.report,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> SubTask:
        status = str(raw.get("status") or SUB_PENDING)
        return cls(
            id=str(raw.get("id") or ""),
            title=str(raw.get("title") or ""),
            description=str(raw.get("description") or ""),
            status=status if status in SUB_STATUSES else SUB_PENDING,
            depends_on=[str(item) for item in raw.get("depends_on") or []],
            checklist=[CheckItem.from_dict(item) for item in _dicts(raw.get("checklist"))],
            criteria=[Criterion.from_dict(item) for item in _dicts(raw.get("criteria"))],
            agent=str(raw.get("agent") or ""),
            report=str(raw.get("report") or ""),
            error=str(raw.get("error") or ""),
        )


@dataclass(slots=True)
class Task:
    """A card on the board: a feature, broken into sub-tasks."""

    id: str
    title: str
    lane: str
    description: str = ""
    status: str = TASK_DRAFT
    subtasks: list[SubTask] = field(default_factory=list)
    checklist: list[CheckItem] = field(default_factory=list)
    criteria: list[Criterion] = field(default_factory=list)
    # Other tasks that must be done before this one starts.
    depends_on: list[str] = field(default_factory=list)
    report: str = ""
    error: str = ""
    # The last thing one of its agents did, for the card.
    activity: str = ""
    next_sub: int = 1

    def subtask(self, ref: str) -> SubTask | None:
        key = ref.strip().lower()
        for sub in self.subtasks:
            if sub.id.lower() == key:
                return sub
        for sub in self.subtasks:
            if sub.title.strip().lower() == key:
                return sub
        return None

    def new_subtask_id(self) -> str:
        taken = {sub.id for sub in self.subtasks}
        while f"{self.id}.{self.next_sub}" in taken:
            self.next_sub += 1
        sub_id = f"{self.id}.{self.next_sub}"
        self.next_sub += 1
        return sub_id

    @property
    def busy(self) -> bool:
        return self.status in BUSY_STATUSES

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "lane": self.lane,
            "description": self.description,
            "status": self.status,
            "subtasks": [sub.to_dict() for sub in self.subtasks],
            "checklist": [item.to_dict() for item in self.checklist],
            "criteria": [item.to_dict() for item in self.criteria],
            "depends_on": list(self.depends_on),
            "report": self.report,
            "error": self.error,
            "activity": self.activity,
            "next_sub": self.next_sub,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Task:
        status = str(raw.get("status") or TASK_DRAFT)
        return cls(
            id=str(raw.get("id") or ""),
            title=str(raw.get("title") or ""),
            lane=str(raw.get("lane") or ROLE_TODO),
            description=str(raw.get("description") or ""),
            status=status if status in TASK_STATUSES else TASK_DRAFT,
            subtasks=[SubTask.from_dict(item) for item in _dicts(raw.get("subtasks"))],
            checklist=[CheckItem.from_dict(item) for item in _dicts(raw.get("checklist"))],
            criteria=[Criterion.from_dict(item) for item in _dicts(raw.get("criteria"))],
            depends_on=[str(item) for item in raw.get("depends_on") or []],
            report=str(raw.get("report") or ""),
            error=str(raw.get("error") or ""),
            activity=str(raw.get("activity") or ""),
            next_sub=max(1, int(raw.get("next_sub") or 1)),
        )


@dataclass(slots=True)
class Lane:
    id: str
    title: str
    # One of ROLES for the three the run moves cards between, "" for the user's own.
    role: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "title": self.title, "role": self.role}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Lane:
        role = str(raw.get("role") or "")
        return cls(
            id=str(raw.get("id") or ""),
            title=str(raw.get("title") or ""),
            role=role if role in ROLES else "",
        )


@dataclass(slots=True)
class Board:
    lanes: list[Lane] = field(default_factory=list)
    # In display order: a lane shows its tasks in the order they appear here.
    tasks: list[Task] = field(default_factory=list)
    next_task: int = 1
    next_lane: int = 1
    next_item: int = 1

    @classmethod
    def empty(cls) -> Board:
        return cls(lanes=[Lane(id=role, title=title, role=role) for role, title in DEFAULT_LANES])

    # -- lookups ----------------------------------------------------------

    def lane(self, ref: str) -> Lane | None:
        key = ref.strip().lower()
        for lane in self.lanes:
            if lane.id.lower() == key:
                return lane
        for lane in self.lanes:
            if lane.title.strip().lower() == key:
                return lane
        return None

    def lane_for(self, role: str) -> Lane:
        for lane in self.lanes:
            if lane.role == role:
                return lane
        raise LookupError(f"The board has no {role} lane.")

    def task(self, ref: str) -> Task | None:
        key = ref.strip().lower()
        for task in self.tasks:
            if task.id.lower() == key:
                return task
        for task in self.tasks:
            if task.title.strip().lower() == key:
                return task
        return None

    def tasks_in(self, lane_id: str) -> list[Task]:
        return [task for task in self.tasks if task.lane == lane_id]

    def role_of(self, task: Task) -> str:
        lane = self.lane(task.lane)
        return lane.role if lane else ""

    # -- ids --------------------------------------------------------------

    def new_task_id(self) -> str:
        taken = {task.id for task in self.tasks}
        while f"T{self.next_task}" in taken:
            self.next_task += 1
        task_id = f"T{self.next_task}"
        self.next_task += 1
        return task_id

    def new_lane_id(self) -> str:
        taken = {lane.id for lane in self.lanes}
        while f"lane-{self.next_lane}" in taken:
            self.next_lane += 1
        lane_id = f"lane-{self.next_lane}"
        self.next_lane += 1
        return lane_id

    def new_item_id(self, prefix: str) -> str:
        item_id = f"{prefix}{self.next_item}"
        self.next_item += 1
        return item_id

    # -- wire -------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": SCHEMA_VERSION,
            "lanes": [lane.to_dict() for lane in self.lanes],
            "tasks": [task.to_dict() for task in self.tasks],
            "next_task": self.next_task,
            "next_lane": self.next_lane,
            "next_item": self.next_item,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Board:
        board = cls(
            lanes=[Lane.from_dict(item) for item in _dicts(raw.get("lanes"))],
            tasks=[Task.from_dict(item) for item in _dicts(raw.get("tasks"))],
            next_task=max(1, int(raw.get("next_task") or 1)),
            next_lane=max(1, int(raw.get("next_lane") or 1)),
            next_item=max(1, int(raw.get("next_item") or 1)),
        )
        board.repair()
        return board

    def repair(self) -> None:
        """Make a board read from disk whole again.

        A role lane a hand edit lost comes back, a task in a lane that no
        longer exists returns to To Do, and work a run held when the backend
        died is marked stopped: nothing is running it any more.
        """

        for role, title in DEFAULT_LANES:
            if not any(lane.role == role for lane in self.lanes):
                taken = {lane.id for lane in self.lanes}
                lane_id = role if role not in taken else self.new_lane_id()
                position = {ROLE_TODO: 0, ROLE_DOING: 1}.get(role, len(self.lanes))
                self.lanes.insert(min(position, len(self.lanes)), Lane(lane_id, title, role))
        known = {lane.id for lane in self.lanes}
        todo = self.lane_for(ROLE_TODO).id
        for task in self.tasks:
            if task.lane not in known:
                task.lane = todo
            if task.status == TASK_PLANNING:
                task.status = TASK_READY if task.subtasks else TASK_DRAFT
            elif task.status in BUSY_STATUSES:
                task.status = TASK_STOPPED
            for sub in task.subtasks:
                if sub.status in (SUB_QUEUED, SUB_RUNNING):
                    sub.status = SUB_STOPPED


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [item for item in value or [] if isinstance(item, dict)]
