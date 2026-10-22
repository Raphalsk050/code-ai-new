"""The task board in the terminal: the panel's board, driven from the keyboard.

Lanes side by side, a card per task; Enter opens a task to edit its sub-tasks,
checklist and validation criteria. Every change goes through the same
SessionCommands the bridge uses, and the screen redraws from the board the
backend announces, so it never holds a copy of its own. A card that changes
lane flies there over the lanes, as it does in the panel.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.geometry import Region
from textual.screen import ModalScreen, Screen
from textual.timer import Timer
from textual.widgets import Button, Checkbox, Footer, Input, RichLog, Static, TextArea

from code_ai.app.commands import SessionCommands

_STATUS = {
    "draft": "draft",
    "planning": "planning…",
    "ready": "ready",
    "queued": "queued",
    "running": "running",
    "verifying": "checking",
    "done": "done",
    "failed": "failed",
    "stopped": "stopped",
}
_BUSY = frozenset({"planning", "queued", "running", "verifying"})
_SUB_MARK = {
    "pending": "○",
    "queued": "◌",
    "running": "◐",
    "done": "●",
    "failed": "✗",
    "stopped": "‖",
    "blocked": "⊘",
}
_CRITERION_MARK = {"pending": "○", "passed": "✓", "failed": "✗"}

# The flight from one lane to another: frames and their spacing.
_FLIGHT_FRAMES = 14
_FLIGHT_STEP_S = 0.035
_FLIGHT_STAGGER_S = 0.35
_FEED_LINES = 300
_LOG_ROWS = 10
_MIN_LANE = 20


def _size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def _lane_of(board: dict[str, Any], role: str) -> dict[str, Any] | None:
    return next((lane for lane in board.get("lanes", []) if lane.get("role") == role), None)


def _card_text(task: dict[str, Any], activity: str = "") -> Text:
    text = Text()
    text.append(task["id"], style="dim")
    status = task.get("status", "draft")
    if status != "done":
        text.append(f"  {_STATUS.get(status, status)}", style=f"bold {_status_style(status)}")
    else:
        text.append("  ✓", style="bold green")
    text.append("\n")
    text.append(task.get("title", ""), style="bold")
    subs = task.get("subtasks", [])
    checks = [
        *task.get("checklist", []),
        *(item for sub in subs for item in sub.get("checklist", [])),
    ]
    criteria = [
        *task.get("criteria", []),
        *(item for sub in subs for item in sub.get("criteria", [])),
    ]
    parts: list[str] = []
    if subs:
        done = sum(1 for sub in subs if sub.get("status") == "done")
        parts.append(f"▤ {done}/{len(subs)}")
    if checks:
        parts.append(f"☑ {sum(1 for item in checks if item.get('done'))}/{len(checks)}")
    if criteria:
        parts.append(
            f"◎ {sum(1 for item in criteria if item.get('status') == 'passed')}/{len(criteria)}"
        )
    if task.get("attachments"):
        parts.append(f"📎 {len(task['attachments'])}")
    if parts:
        text.append("\n" + "  ".join(parts), style="dim")
    if task.get("depends_on") and status != "done":
        text.append(f"\nafter {', '.join(task['depends_on'])}", style="dim")
    line = activity or task.get("activity", "")
    if line and status in _BUSY:
        text.append(f"\n{line}", style="italic dim")
    return text


_VERBS = {
    "read_file": "reading",
    "edit_code": "editing",
    "write_file": "writing",
    "execute_command": "running",
    "list_files": "listing",
    "web_search": "searching the web",
}


def _verb(tool: str) -> str:
    """What a tool reads as on a card: "editing src/api.py"."""

    if tool in _VERBS:
        return _VERBS[tool]
    if tool.startswith("search_") or tool == "index_workspace":
        return "searching"
    if tool.startswith("browser_"):
        return "browsing"
    if tool.endswith("_review"):
        return "reviewing"
    return f"using {tool}" if tool else "working"


def _clock(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def _step_text(live: dict[str, Any] | None, fallback: str = "") -> str:
    if live and live.get("phase"):
        return str(live["phase"])
    if live and live.get("tool"):
        return f"{_verb(live['tool'])} {live.get('target', '')}".strip()
    # What the board saved of the step: "<tool> <target>", read the same way.
    tool, _, target = fallback.partition(" ")
    return f"{_verb(tool)} {target}".strip() if tool else ""


def _sub_text(sub: dict[str, Any], live: dict[str, Any] | None = None) -> Text:
    """One sub-task: its mark, id and title, and while it runs, its agent and step."""

    status = sub.get("status", "pending")
    text = Text()
    text.append(f"{_SUB_MARK.get(status, '○')} ", style=_sub_style(status))
    text.append(f"{sub['id']} ", style="dim")
    text.append(sub.get("title", ""))
    if status == "running":
        started = float(sub.get("started_at") or 0)
        clock = f"  {_clock(time.time() - started)}" if started else ""
        steps = f" · {live['steps']} step(s)" if live and live.get("steps") else ""
        text.append(f"\n{sub.get('agent', '')}", style="bold cyan")
        text.append(f"{clock}{steps}", style="dim")
        step = _step_text(live, sub.get("activity", ""))
        if step:
            text.append(f"\n{step}", style="italic")
        if live and live.get("said"):
            text.append(f"\n“{live['said'][-90:]}”", style="dim italic")
    elif status == "done" and sub.get("agent"):
        text.append(f"\n{sub['agent']}", style="dim")
    elif status in ("failed", "blocked") and sub.get("error"):
        text.append(f"\n{sub['error'][:120]}", style="red")
    elif status == "pending" and sub.get("depends_on"):
        text.append(f"\nafter {', '.join(sub['depends_on'])}", style="dim")
    return text


def _sub_style(status: str) -> str:
    if status == "done":
        return "green"
    if status == "running":
        return "cyan"
    if status in ("failed", "blocked"):
        return "red"
    return "grey50"


def _sub_lane(task: dict[str, Any], sub: dict[str, Any]) -> str:
    return str(sub.get("lane") or task["lane"])


def _lane_groups(
    board: dict[str, Any], lane_id: str
) -> list[tuple[dict[str, Any], bool, list[dict[str, Any]]]]:
    """Each task this lane shows: the card itself or a stub, and the sub-cards the lane holds."""

    groups = []
    for task in board.get("tasks", []):
        subs = [sub for sub in task.get("subtasks", []) if _sub_lane(task, sub) == lane_id]
        full = task["lane"] == lane_id
        if full or subs:
            groups.append((task, full, subs))
    return groups


def _flights_between(before: dict[str, Any], after: dict[str, Any]) -> list[tuple[str, str]]:
    """(id, new lane) of every card and sub-card that moved: tasks first, as the run pulls them."""

    was: dict[str, str] = {}
    for task in before.get("tasks", []):
        was[task["id"]] = task["lane"]
        for sub in task.get("subtasks", []):
            was[sub["id"]] = _sub_lane(task, sub)
    tasks: list[tuple[str, str]] = []
    subs: list[tuple[str, str]] = []
    for task in after.get("tasks", []):
        if was.get(task["id"], task["lane"]) != task["lane"]:
            tasks.append((task["id"], task["lane"]))
        for sub in task.get("subtasks", []):
            lane = _sub_lane(task, sub)
            if sub["id"] in was and was[sub["id"]] != lane:
                subs.append((sub["id"], lane))
    return tasks + subs


def _widget_id(card_id: str) -> str:
    return "board-card-" + card_id.replace(".", "_")


def _status_style(status: str) -> str:
    if status in ("running", "queued", "verifying", "planning"):
        return "cyan"
    if status == "ready":
        return "yellow"
    if status == "failed":
        return "red"
    return "grey50"


class BoardCard(Static, can_focus=True):
    """One task on its lane."""

    # On the card itself: the lane's scroll would take the arrows first.
    BINDINGS = [
        Binding("up", "screen.focus_card(-1)", show=False),
        Binding("down", "screen.focus_card(1)", show=False),
        Binding("left", "screen.focus_lane(-1)", show=False),
        Binding("right", "screen.focus_lane(1)", show=False),
        Binding("shift+up", "screen.reorder(-1)", show=False),
        Binding("shift+down", "screen.reorder(1)", show=False),
    ]

    def __init__(self, task: dict[str, Any], activity: str = "") -> None:
        super().__init__(
            _card_text(task, activity), id=_widget_id(task["id"]), classes="board-card"
        )
        self.task_id = task["id"]
        self.lane_id = task["lane"]
        self.set_class(True, f"s-{task.get('status', 'draft')}")

    def show(self, task: dict[str, Any], activity: str = "") -> None:
        self.update(_card_text(task, activity))


class BoardSubCard(Static, can_focus=True):
    """A sub-task as a card of its own, under its task: it moves across lanes as it is worked."""

    BINDINGS = [
        Binding("up", "screen.focus_card(-1)", show=False),
        Binding("down", "screen.focus_card(1)", show=False),
        Binding("left", "screen.focus_lane(-1)", show=False),
        Binding("right", "screen.focus_lane(1)", show=False),
    ]

    def __init__(
        self,
        task: dict[str, Any],
        sub: dict[str, Any],
        live: dict[str, Any] | None,
        lane_id: str,
    ) -> None:
        super().__init__(_sub_text(sub, live), id=_widget_id(sub["id"]), classes="board-subcard")
        self.task_id = task["id"]
        self.sub_id = sub["id"]
        self.lane_id = lane_id
        self.set_class(True, f"st-{sub.get('status', 'pending')}")

    def on_click(self) -> None:
        self.open()

    def open(self) -> None:
        """The sub-task alone, not the task it is part of."""

        screen = self.screen
        if isinstance(screen, BoardScreen):
            self.app.push_screen(SubTaskEditor(screen, self.task_id, self.sub_id))


class BoardStub(Static):
    """A task named in a lane it is not in, over the sub-cards of it the lane holds."""

    def __init__(self, task: dict[str, Any]) -> None:
        super().__init__(
            Text.assemble(("↳ ", "dim"), (task["id"] + " ", "dim"), (task.get("title", ""), "dim")),
            classes="board-stub",
        )
        self.task_id = task["id"]

    def on_click(self) -> None:
        screen = self.screen
        if isinstance(screen, BoardScreen):
            self.app.push_screen(TaskEditor(screen, self.task_id))


class BoardLaneView(Vertical):
    def __init__(
        self, lane: dict[str, Any], board: dict[str, Any], live: dict[str, dict[str, Any]]
    ) -> None:
        super().__init__(id=f"board-lane-{lane['id']}", classes="board-lane")
        self.lane = lane
        self._board = board
        self._live = live

    def compose(self) -> ComposeResult:
        groups = _lane_groups(self._board, self.lane["id"])
        cards = sum(1 for _, full, _ in groups if full)
        pulled = sum(len(subs) for _, full, subs in groups if not full)
        count = f"  {cards}" + (f"  ▤ {pulled}" if pulled else "")
        yield Static(
            Text.assemble((self.lane["title"].upper(), "bold"), (count, "dim")),
            classes="board-lane-title",
        )
        with VerticalScroll(classes="board-lane-cards"):
            for task, full, subs in groups:
                if full:
                    yield BoardCard(task, self._live.get(task["id"], {}).get("line", ""))
                else:
                    yield BoardStub(task)
                with Vertical(classes="board-subcards"):
                    for sub in subs:
                        yield BoardSubCard(task, sub, self._live.get(sub["id"]), self.lane["id"])
            if not groups:
                yield Static("—", classes="board-lane-empty")


def _ghost_text(board: dict[str, Any], card_id: str) -> Text:
    """What a card looked like before it moved: the copy that flies."""

    for task in board.get("tasks", []):
        if task["id"] == card_id:
            return _card_text(task)
        for sub in task.get("subtasks", []):
            if sub["id"] == card_id:
                return _sub_text(sub)
    return Text(card_id)


class TextPrompt(ModalScreen[str | None]):
    """One line asked for: a task title, a lane name."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, title: str, value: str = "", placeholder: str = "") -> None:
        super().__init__()
        self._title = title
        self._value = value
        self._placeholder = placeholder

    def compose(self) -> ComposeResult:
        with Vertical(classes="board-dialog board-prompt"):
            yield Static(self._title, classes="board-dialog-title")
            yield Input(value=self._value, placeholder=self._placeholder, id="board-prompt-input")
            yield Static("Enter to confirm, Esc to cancel.", classes="board-hint")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class _ItemsMixin:
    """The checklist and criteria lists both editors show."""

    def _items(
        self, kind: str, items: list[dict[str, Any]], locked: bool, prefix: str
    ) -> ComposeResult:
        if kind == "checklist":
            for item in items:
                with Horizontal(classes="board-row"):
                    yield Checkbox(
                        item["text"],
                        item.get("done", False),
                        id=f"{prefix}-check-{item['id']}",
                        disabled=locked,
                        compact=True,
                    )
                    yield Button(
                        "✕",
                        id=f"{prefix}-remove-checklist-{item['id']}",
                        classes="board-x",
                        compact=True,
                        disabled=locked,
                    )
            yield Input(
                placeholder="Add a checklist item (Enter)",
                id=f"{prefix}-add-checklist",
                disabled=locked,
            )
        else:
            for item in items:
                with Horizontal(classes="board-row"):
                    mark = _CRITERION_MARK.get(item.get("status", "pending"), "○")
                    note = f"  ({item['note']})" if item.get("note") else ""
                    yield Static(
                        f"{mark} {item['text']}{note}",
                        classes=f"board-criterion c-{item.get('status')}",
                    )
                    yield Button(
                        "✕",
                        id=f"{prefix}-remove-criteria-{item['id']}",
                        classes="board-x",
                        compact=True,
                        disabled=locked,
                    )
            yield Input(
                placeholder="Add a validation criterion (Enter)",
                id=f"{prefix}-add-criteria",
                disabled=locked,
            )


class _EditorBase(_ItemsMixin, ModalScreen[None]):
    """What the task and sub-task editors share: the board they read, the edits they send."""

    BINDINGS = [("escape", "close", "Close")]

    def __init__(self, screen: BoardScreen, task_id: str) -> None:
        super().__init__()
        self._board_screen = screen
        self.task_id = task_id
        self._confirm = ""
        self._drawn = self._drawn_state()

    def task(self) -> dict[str, Any] | None:
        return self._board_screen.task(self.task_id)

    def edit(self, *operations: dict[str, Any]) -> None:
        self._board_screen.edit(list(operations))

    def _drawn_state(self) -> str:
        return json.dumps(self.task(), sort_keys=True, default=str)

    async def redraw(self, *, force: bool = False) -> None:
        # A run changes the board many times a second; an editor whose task
        # did not change keeps what is being typed into it.
        state = self._drawn_state()
        if not force and state == self._drawn:
            return
        self._drawn = state
        focused = self.focused.id if self.focused is not None else None
        await self.recompose()
        if focused:
            for widget in self.query(f"#{focused}"):
                if widget.focusable:
                    widget.focus()
                break

    def action_close(self) -> None:
        self.dismiss(None)

    def _where(self) -> dict[str, Any]:
        return {"task": self.task_id}

    def _item_event(self, widget_id: str) -> bool:
        """Checklist and criteria buttons; True when it was one of them."""

        for name in ("checklist", "criteria"):
            marker = f"-remove-{name}-"
            if marker in widget_id:
                item = widget_id.split(marker, 1)[1]
                self.edit({"op": "remove_item", **self._where(), "list": name, "item": item})
                return True
        return False


class SubTaskEditor(_EditorBase):
    def __init__(self, screen: BoardScreen, task_id: str, sub_id: str) -> None:
        super().__init__(screen, task_id)
        self.sub_id = sub_id

    def _sub(self) -> dict[str, Any] | None:
        task = self.task()
        if task is None:
            return None
        return next((sub for sub in task["subtasks"] if sub["id"] == self.sub_id), None)

    def _where(self) -> dict[str, Any]:
        return {"task": self.task_id, "subtask": self.sub_id}

    def compose(self) -> ComposeResult:
        task = self.task()
        sub = self._sub()
        with VerticalScroll(classes="board-dialog board-editor"):
            if task is None or sub is None:
                yield Static("This sub-task is gone.", classes="board-dialog-title")
                yield Button("Close", id="sub-close")
                return
            locked = task["status"] in _BUSY
            mark = _SUB_MARK.get(sub["status"], "○")
            agent = f" · {sub['agent']}" if sub.get("agent") else ""
            yield Static(
                f"{mark} {sub['id']} · {sub['status']}{agent}", classes="board-dialog-title"
            )
            yield Input(sub["title"], id="sub-title", disabled=locked)
            yield TextArea(sub.get("description", ""), id="sub-description", disabled=locked)
            yield Static("Waits for (sub-task ids, comma separated)", classes="board-label")
            yield Input(", ".join(sub.get("depends_on", [])), id="sub-depends", disabled=locked)
            yield Static("Checklist", classes="board-label")
            yield from self._items("checklist", sub.get("checklist", []), locked, "sub")
            yield Static("Validation criteria", classes="board-label")
            yield from self._items("criteria", sub.get("criteria", []), locked, "sub")
            if sub.get("error"):
                yield Static(sub["error"], classes="board-error")
            if sub.get("report"):
                yield Static(sub["report"], classes="board-report")
            with Horizontal(classes="board-actions"):
                yield Button("Save", id="sub-save", variant="primary", disabled=locked)
                yield Button(
                    "Press again to remove" if self._confirm == "remove" else "Remove",
                    id="sub-remove",
                    variant="error",
                    disabled=locked,
                )
                yield Button("Close (Esc)", id="sub-close")

    def _save(self) -> None:
        sub = self._sub()
        if sub is None:
            return
        title = self.query_one("#sub-title", Input).value.strip()
        description = self.query_one("#sub-description", TextArea).text
        depends = [
            item.strip()
            for item in self.query_one("#sub-depends", Input).value.split(",")
            if item.strip()
        ]
        op: dict[str, Any] = {"op": "update_subtask", **self._where()}
        if title and title != sub["title"]:
            op["title"] = title
        if description != sub.get("description", ""):
            op["description"] = description
        if depends != sub.get("depends_on", []):
            op["depends_on"] = depends
        if len(op) > 3:
            self.edit(op)

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        widget_id = event.button.id or ""
        if widget_id == "sub-close":
            self.dismiss(None)
        elif widget_id == "sub-save":
            self._save()
        elif widget_id == "sub-remove":
            if self._confirm != "remove":
                self._confirm = "remove"
                await self.redraw(force=True)
                return
            self.edit({"op": "remove_subtask", **self._where()})
            self.dismiss(None)
        else:
            self._item_event(widget_id)

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        widget_id = event.checkbox.id or ""
        if widget_id.startswith("sub-check-"):
            item = widget_id[len("sub-check-") :]
            self.edit(
                {
                    "op": "update_item",
                    **self._where(),
                    "list": "checklist",
                    "item": item,
                    "done": event.value,
                }
            )

    def on_input_submitted(self, event: Input.Submitted) -> None:
        widget_id = event.input.id or ""
        if widget_id in ("sub-add-checklist", "sub-add-criteria") and event.value.strip():
            name = widget_id.rsplit("-", 1)[1]
            self.edit(
                {"op": "add_item", **self._where(), "list": name, "text": event.value.strip()}
            )
            event.input.value = ""
        elif widget_id in ("sub-title", "sub-depends"):
            self._save()


class TaskEditor(_EditorBase):
    """A task with everything inside it, editable until a run holds it."""

    def compose(self) -> ComposeResult:
        task = self.task()
        with VerticalScroll(classes="board-dialog board-editor"):
            if task is None:
                yield Static("This task is gone.", classes="board-dialog-title")
                yield Button("Close", id="task-close")
                return
            locked = task["status"] in _BUSY
            lane = next(
                (
                    item["title"]
                    for item in self._board_screen.board["lanes"]
                    if item["id"] == task["lane"]
                ),
                "",
            )
            yield Static(
                f"{task['id']} · {_STATUS.get(task['status'], task['status'])} · {lane}",
                classes="board-dialog-title",
            )
            if locked:
                yield Static(
                    "Agents are working on this task. Stop the board (s) to change it.",
                    classes="board-locked",
                )
            yield Input(task["title"], id="task-title", disabled=locked)
            yield TextArea(task.get("description", ""), id="task-description", disabled=locked)
            if task.get("error"):
                yield Static(task["error"], classes="board-error")
            subs = task.get("subtasks", [])
            done = sum(1 for sub in subs if sub.get("status") == "done")
            yield Static(f"Sub-tasks  {done}/{len(subs)}", classes="board-label")
            for sub in subs:
                with Horizontal(classes="board-row"):
                    yield Checkbox(
                        f"{_SUB_MARK.get(sub['status'], '○')} {sub['id']} {sub['title']}",
                        sub.get("status") == "done",
                        id=f"task-subdone-{sub['id'].replace('.', '_')}",
                        disabled=locked,
                        compact=True,
                    )
                    yield Button(
                        "Edit",
                        id=f"task-subedit-{sub['id'].replace('.', '_')}",
                        classes="board-x",
                        compact=True,
                    )
            yield Input(placeholder="Add a sub-task (Enter)", id="task-add-sub", disabled=locked)
            yield Static("Checklist", classes="board-label")
            yield from self._items("checklist", task.get("checklist", []), locked, "task")
            yield Static("Validation criteria", classes="board-label")
            yield from self._items("criteria", task.get("criteria", []), locked, "task")
            attachments = task.get("attachments", [])
            yield Static(f"Attachments  {len(attachments)}", classes="board-label")
            for item in attachments:
                with Horizontal(classes="board-row"):
                    yield Static(
                        f"📎 {item['name']}  ({_size(item.get('size', 0))})  {item['path']}",
                        classes="board-attachment",
                    )
                    yield Button(
                        "✕",
                        id=f"task-remove-attachment-{item['id']}",
                        classes="board-x",
                        compact=True,
                        disabled=locked,
                    )
            yield Input(
                placeholder="Attach a file: its path (Enter)",
                id="task-attach",
                disabled=locked,
            )
            yield Static("Starts after (task ids, comma separated)", classes="board-label")
            yield Input(", ".join(task.get("depends_on", [])), id="task-depends", disabled=locked)
            if task.get("report"):
                yield Static("Report", classes="board-label")
                yield Static(task["report"], classes="board-report")
            with Horizontal(classes="board-actions"):
                yield Button("Save", id="task-save", variant="primary", disabled=locked)
                yield Button("Plan", id="task-plan", disabled=locked)
                yield Button(
                    "Run",
                    id="task-run",
                    variant="success",
                    disabled=locked or task["status"] == "done",
                )
                yield Button(
                    "Press again to delete" if self._confirm == "delete" else "Delete",
                    id="task-delete",
                    variant="error",
                    disabled=locked,
                )
                yield Button("Close (Esc)", id="task-close")

    def _save(self) -> None:
        task = self.task()
        if task is None:
            return
        title = self.query_one("#task-title", Input).value.strip()
        description = self.query_one("#task-description", TextArea).text
        depends = [
            item.strip()
            for item in self.query_one("#task-depends", Input).value.split(",")
            if item.strip()
        ]
        op: dict[str, Any] = {"op": "update_task", "task": self.task_id}
        if title and title != task["title"]:
            op["title"] = title
        if description != task.get("description", ""):
            op["description"] = description
        if depends != task.get("depends_on", []):
            op["depends_on"] = depends
        if len(op) > 2:
            self.edit(op)

    @staticmethod
    def _sub_id(widget_id: str, prefix: str) -> str:
        return widget_id[len(prefix) :].replace("_", ".")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        widget_id = event.button.id or ""
        if widget_id == "task-close":
            self.dismiss(None)
        elif widget_id == "task-save":
            self._save()
        elif widget_id == "task-plan":
            self._board_screen.plan([self.task_id])
        elif widget_id == "task-run":
            self._save()
            self._board_screen.play([self.task_id])
            self.dismiss(None)
        elif widget_id == "task-delete":
            if self._confirm != "delete":
                self._confirm = "delete"
                await self.redraw(force=True)
                return
            self.edit({"op": "remove_task", "task": self.task_id})
            self.dismiss(None)
        elif widget_id.startswith("task-remove-attachment-"):
            attachment = widget_id[len("task-remove-attachment-") :]
            self.edit({"op": "remove_attachment", "task": self.task_id, "attachment": attachment})
        elif widget_id.startswith("task-subedit-"):
            self.app.push_screen(
                SubTaskEditor(
                    self._board_screen, self.task_id, self._sub_id(widget_id, "task-subedit-")
                )
            )
        else:
            self._item_event(widget_id)

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        widget_id = event.checkbox.id or ""
        if widget_id.startswith("task-subdone-"):
            sub = self._sub_id(widget_id, "task-subdone-")
            self.edit(
                {"op": "update_subtask", "task": self.task_id, "subtask": sub, "done": event.value}
            )
        elif widget_id.startswith("task-check-"):
            item = widget_id[len("task-check-") :]
            self.edit(
                {
                    "op": "update_item",
                    **self._where(),
                    "list": "checklist",
                    "item": item,
                    "done": event.value,
                }
            )

    def on_input_submitted(self, event: Input.Submitted) -> None:
        widget_id = event.input.id or ""
        value = event.value.strip()
        if widget_id == "task-add-sub" and value:
            self.edit({"op": "add_subtask", "task": self.task_id, "title": value})
            event.input.value = ""
        elif widget_id in ("task-add-checklist", "task-add-criteria") and value:
            name = widget_id.rsplit("-", 1)[1]
            self.edit({"op": "add_item", **self._where(), "list": name, "text": value})
            event.input.value = ""
        elif widget_id == "task-attach" and value:
            self._board_screen.attach(self.task_id, value.strip('"'))
            event.input.value = ""
        elif widget_id in ("task-title", "task-depends"):
            self._save()


class BoardScreen(Screen[None]):
    """The board, full screen; Esc goes back to the chat."""

    BINDINGS = [
        Binding("escape", "close", "Chat"),
        Binding("n", "new_task", "New task"),
        Binding("enter", "open", "Open"),
        Binding("shift+right", "move(1)", "Move →"),
        Binding("shift+left", "move(-1)", "Move ←", show=False),
        Binding("p", "plan", "Plan"),
        Binding("g", "play", "Play"),
        Binding("r", "run", "Run card"),
        Binding("s", "stop", "Stop"),
        Binding("a", "add_lane", "Lane"),
        Binding("l", "toggle_activity", "Activity"),
        Binding("ctrl+up", "resize_log(2)", "", show=False),
        Binding("ctrl+down", "resize_log(-2)", "", show=False),
        Binding("ctrl+right", "resize_lanes(4)", "", show=False),
        Binding("ctrl+left", "resize_lanes(-4)", "", show=False),
        Binding("e", "rename_lane", "Rename lane", show=False),
        Binding("x", "remove_lane", "Remove lane", show=False),
        Binding("delete", "delete", "Delete", show=False),
    ]

    def __init__(self, application: Any, *, on_close: Callable[[], None] | None = None) -> None:
        super().__init__(id="board-screen")
        self._application = application
        self._commands = SessionCommands(application)
        self._on_close = on_close
        snapshot = self._commands.board_snapshot()
        self.board: dict[str, Any] = snapshot.data.get("board") or {"lanes": [], "tasks": []}
        # What each agent is doing, by sub-task id (or task id when worked whole).
        self._live: dict[str, dict[str, Any]] = {}
        self._review = ""
        self._subscription: Any = None
        self._confirm = ""
        self._focus_id = ""
        self._flights: list[Timer] = []
        # Cards pulled together leave one after another (monotonic time of the next start).
        self._next_flight = 0.0
        self._activity_shown = False
        # Cells per lane once the user widened or narrowed them; 0 shares the width.
        self._lane_width = 0

    # -- reading ----------------------------------------------------------

    def task(self, task_id: str) -> dict[str, Any] | None:
        return next((task for task in self.board.get("tasks", []) if task["id"] == task_id), None)

    def _tasks_in(self, lane_id: str) -> list[dict[str, Any]]:
        return [task for task in self.board.get("tasks", []) if task["lane"] == lane_id]

    def _focused_card(self) -> BoardCard | None:
        return self.focused if isinstance(self.focused, BoardCard) else None

    def _focused_item(self) -> BoardCard | BoardSubCard | None:
        return self.focused if isinstance(self.focused, (BoardCard, BoardSubCard)) else None

    def _items_in(self, lane_id: str) -> list[BoardCard | BoardSubCard]:
        """The lane's cards and sub-cards, top to bottom: what the arrows walk."""

        lane = self.query(f"#board-lane-{lane_id}")
        if not lane:
            return []
        return [
            widget
            for widget in lane.first().query(".board-card, .board-subcard")
            if isinstance(widget, (BoardCard, BoardSubCard))
        ]

    def _focused_lane(self) -> dict[str, Any] | None:
        item = self._focused_item()
        lane_id = item.lane_id if item else self._focus_lane_id
        return next((lane for lane in self.board.get("lanes", []) if lane["id"] == lane_id), None)

    _focus_lane_id = "todo"

    # -- drawing ----------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Static(self._header(), id="board-header")
        yield Static(self._banner(), id="board-review")
        yield Horizontal(id="board-lanes")
        log = RichLog(id="board-activity", max_lines=_FEED_LINES, wrap=False, markup=False)
        log.border_title = "Activity · l hides it · ctrl+↑↓ its height · ctrl+←→ lane width"
        log.display = False
        yield log
        yield Footer()

    async def on_mount(self) -> None:
        self._subscription = self._application.subscribe(self._on_event)
        await self._draw_lanes()
        self._refocus()

    def on_unmount(self) -> None:
        unsubscribe = getattr(self._application, "unsubscribe", None)
        if unsubscribe is not None and self._subscription is not None:
            unsubscribe(self._subscription)
        for timer in self._flights:
            timer.stop()

    def _header(self) -> Text:
        text = Text.assemble(
            ("● ", "cyan" if self.board.get("running") else "grey50"), ("Task board", "bold")
        )
        running = sum(
            1
            for task in self.board.get("tasks", [])
            for sub in task.get("subtasks", [])
            if sub.get("status") == "running"
        )
        if self.board.get("running"):
            text.append(f"   running · {running} agent(s)", style="cyan")
        elif self.board.get("planning"):
            text.append("   planning", style="cyan")
        return text

    def _waiting(self) -> list[dict[str, Any]]:
        todo = _lane_of(self.board, "todo")
        if todo is None:
            return []
        return [
            task
            for task in self.board.get("tasks", [])
            if task["lane"] == todo["id"] and task["status"] == "ready"
        ]

    def _banner(self) -> Text:
        waiting = self._waiting()
        if self.board.get("running") or not waiting:
            return Text("")
        text = Text.assemble(
            (f"{len(waiting)} task(s) waiting for your confirmation. ", "bold"),
            "Check the tasks and sub-tasks (Enter), change or add what you need, "
            "then press g to Play.",
        )
        if self._review:
            text.append(f"\n{self._review}", style="dim")
        return text

    async def _draw_lanes(self) -> None:
        container = self.query_one("#board-lanes", Horizontal)
        await container.remove_children()
        lanes = [
            BoardLaneView(lane, self.board, self._live) for lane in self.board.get("lanes", [])
        ]
        await container.mount_all(lanes)
        self._apply_lane_width()
        self.query_one("#board-header", Static).update(self._header())
        banner = self.query_one("#board-review", Static)
        banner.update(self._banner())
        banner.display = bool(self._waiting()) and not self.board.get("running")

    def _refocus(self) -> None:
        items = [widget for widget in self.query(".board-card, .board-subcard") if widget.focusable]
        target = next((item for item in items if item.id == _widget_id(self._focus_id)), None)
        if target is None and items:
            target = next(
                (item for item in items if getattr(item, "lane_id", "") == self._focus_lane_id),
                items[0],
            )
        if target is not None:
            target.focus()

    def on_descendant_focus(self, event: Any) -> None:
        widget = event.widget if hasattr(event, "widget") else None
        if isinstance(widget, BoardCard):
            self._focus_id = widget.task_id
            self._focus_lane_id = widget.lane_id
        elif isinstance(widget, BoardSubCard):
            self._focus_id = widget.sub_id
            self._focus_lane_id = widget.lane_id

    # -- backend ----------------------------------------------------------

    async def _on_event(self, envelope: Any) -> None:
        payload = envelope.payload or {}
        if envelope.event_type == "board.changed":
            await self.apply_snapshot(payload.get("board") or {}, payload.get("moved") or [])
        elif envelope.event_type == "board.activity":
            self._on_step(payload)
        elif envelope.event_type == "board.review.requested":
            self._review = str(payload.get("message") or "")
        elif envelope.event_type == "board.run.completed":
            self._live.clear()
            failed = payload.get("failed") or []
            if failed:
                self.notify(
                    f"Not done: {', '.join(failed)}. Open the card to see why.", severity="warning"
                )

    def _on_step(self, step: dict[str, Any]) -> None:
        """One step of one agent: its card says what it does now, the log keeps it."""

        key = str(step.get("subtask") or step.get("task") or "")
        agent = str(step.get("agent") or "")
        live = self._live.get(key)
        if live is None or live.get("agent") != agent:
            live = {"agent": agent, "steps": 0}
            self._live[key] = live
        kind = step.get("kind")
        text = str(step.get("text") or "").split(": ", 1)[-1]
        if kind == "tool":
            if step.get("state") == "started":
                live.update(
                    tool=step.get("tool", ""), target=step.get("target", ""), phase="", said=""
                )
                live["steps"] += 1
                self._log(step, f"{_verb(str(step.get('tool') or ''))} {step.get('target', '')}")
            elif step.get("state") == "failed":
                self._log(step, f"{step.get('tool')} failed", style="red")
        elif kind == "say":
            live["said"] = text
        else:
            live.update(phase=text, tool="", target="")
            self._log(step, text, style="cyan")
        live["line"] = f"{agent}: {_step_text(live)}"
        task = self.task(str(step.get("task") or ""))
        if task is None:
            return
        sub = next((item for item in task.get("subtasks", []) if item["id"] == key), None)
        for widget in self.query(f"#{_widget_id(key)}"):
            if isinstance(widget, BoardSubCard) and sub is not None:
                widget.update(_sub_text(sub, live))
            elif isinstance(widget, BoardCard):
                widget.show(task, live["line"])

    def _log(self, step: dict[str, Any], what: str, *, style: str = "") -> None:
        log = self.query_one("#board-activity", RichLog)
        # The first step shows the log, unless the user already chose: a board
        # opened mid-run never sees the run start.
        if not self._activity_shown:
            self._activity_shown = True
            log.display = True
        when = time.strftime("%H:%M:%S", time.localtime(float(step.get("at") or time.time())))
        where = str(step.get("subtask") or step.get("task") or "")
        log.write(
            Text.assemble(
                (f"{when} ", "dim"),
                (f"{step.get('agent', '')} ", "bold cyan"),
                (f"{where} ", "dim"),
                (what.strip(), style),
            )
        )

    async def apply_snapshot(self, board: dict[str, Any], moved: list[dict[str, str]]) -> None:
        if not board:
            return
        previous = self.board
        before = {
            widget.id or "": widget.region for widget in self.query(".board-card, .board-subcard")
        }
        self.board = board
        if not self.is_mounted:
            return
        # A run that starts shows its log, once.
        if board.get("running") and not previous.get("running") and not self._activity_shown:
            self._activity_shown = True
            self.query_one("#board-activity", RichLog).display = True
        await self._draw_lanes()
        self._refocus()
        done = _lane_of(board, "done")
        for card_id, lane in _flights_between(previous, board):
            origin = before.get(_widget_id(card_id))
            if origin is not None:
                # After the next refresh: until then the new cards have no place.
                self.call_after_refresh(
                    self._schedule_flight,
                    card_id,
                    origin,
                    _ghost_text(previous, card_id),
                    bool(done and lane == done["id"]),
                )
        for screen in self.app.screen_stack:
            if isinstance(screen, _EditorBase):
                await screen.redraw()

    def _schedule_flight(self, card_id: str, origin: Region, text: Text, landing: bool) -> None:
        """Fly a card in its turn: a task first, then its sub-tasks as they are pulled."""

        if origin.width == 0:
            return
        ghost = Static(text, classes="board-ghost")
        ghost.styles.width = origin.width
        ghost.styles.height = origin.height
        ghost.styles.offset = (origin.x, origin.y)
        self.mount(ghost)
        for widget in self.query(f"#{_widget_id(card_id)}"):
            widget.styles.opacity = 0.0
        now = time.monotonic()
        start = max(now, self._next_flight)
        self._next_flight = start + _FLIGHT_STAGGER_S

        def fly() -> None:
            self._fly(card_id, origin, ghost, landing)

        # A timer of no delay is one Textual cannot run.
        if start - now < 0.02:
            self.call_later(fly)
        else:
            self.set_timer(start - now, fly)

    def _fly(self, card_id: str, origin: Region, ghost: Static, landing: bool) -> None:
        """Send the copy from where the card was to where it is now, over the lanes."""

        found = list(self.query(f"#{_widget_id(card_id)}"))
        if not found or found[0].region.width == 0:
            ghost.remove()
            for widget in found:
                widget.styles.opacity = 1.0
            return
        card = found[0]
        card.styles.opacity = 0.0
        target = card.region
        frame = [0]

        def step() -> None:
            frame[0] += 1
            share = frame[0] / _FLIGHT_FRAMES
            eased = 1 - (1 - share) ** 3
            # Lifted off its lane and set down on the other: an arc, not a slide.
            lift = round(2 * (1 - (2 * share - 1) ** 2))
            x = round(origin.x + (target.x - origin.x) * eased)
            y = round(origin.y + (target.y - origin.y) * eased) - lift
            ghost.styles.offset = (x, max(0, y))
            if frame[0] >= _FLIGHT_FRAMES:
                timer.stop()
                ghost.remove()
                # Looked up again: the lanes may have been drawn anew in flight.
                for landed in self.query(f"#{_widget_id(card_id)}"):
                    landed.styles.opacity = 1.0
                    if landing:
                        landed.add_class("landed")
                        self.set_timer(0.9, lambda widget=landed: widget.remove_class("landed"))

        timer = self.set_interval(_FLIGHT_STEP_S, step)
        self._flights.append(timer)

    def edit(self, operations: list[dict[str, Any]]) -> None:
        async def send() -> None:
            result = await self._commands.board_edit(operations)
            if not result.ok:
                self.notify(result.message, severity="warning")

        self.run_worker(send(), exclusive=False)

    def attach(self, task_id: str, path: str) -> None:
        self.run_worker(
            self._call(self._commands.board_attach(task_id, path=path)), exclusive=False
        )

    def plan(self, task_ids: list[str] | None = None) -> None:
        self.run_worker(self._call(self._commands.board_plan(task_ids)), exclusive=False)

    def play(self, task_ids: list[str] | None = None) -> None:
        self.run_worker(self._call(self._commands.board_play(task_ids)), exclusive=False)

    async def _call(self, call: Any) -> None:
        result = await call
        if result.message:
            self.notify(result.message, severity="information" if result.ok else "warning")

    # -- keys -------------------------------------------------------------

    def action_close(self) -> None:
        self.app.pop_screen()
        if self._on_close is not None:
            self._on_close()

    def action_open(self) -> None:
        item = self._focused_item()
        if isinstance(item, BoardSubCard):
            item.open()
        elif item is not None:
            self.app.push_screen(TaskEditor(self, item.task_id))

    def action_new_task(self) -> None:
        lane = self._focused_lane()
        if lane is None or lane.get("role") in ("in_progress", "done"):
            lane = _lane_of(self.board, "todo")
        lane_id = lane["id"] if lane else "todo"

        def added(title: str | None) -> None:
            if title:
                self.edit([{"op": "add_task", "title": title, "lane": lane_id}])

        self.app.push_screen(TextPrompt("New task", placeholder="What needs to be done?"), added)

    def action_focus_lane(self, step: int) -> None:
        lanes = self.board.get("lanes", [])
        current = self._focused_lane()
        if not lanes or current is None:
            return
        index = next((i for i, lane in enumerate(lanes) if lane["id"] == current["id"]), 0)
        for offset in range(1, len(lanes)):
            lane = lanes[(index + step * offset) % len(lanes)]
            items = self._items_in(lane["id"])
            self._focus_lane_id = lane["id"]
            if items:
                items[0].focus()
                return

    def action_focus_card(self, step: int) -> None:
        item = self._focused_item()
        if item is None:
            self._refocus()
            return
        items = self._items_in(item.lane_id)
        index = items.index(item) + step
        if 0 <= index < len(items):
            items[index].focus()

    def action_resize_log(self, rows: int) -> None:
        log = self.query_one("#board-activity", RichLog)
        height = log.styles.height.value if log.styles.height else _LOG_ROWS
        log.styles.height = int(max(4, min(40, height + rows)))
        log.display = True
        self._activity_shown = True

    def action_resize_lanes(self, cells: int) -> None:
        """Every lane wider or narrower together, starting from the width they share now."""

        if not self._lane_width:
            lanes = max(1, len(self.board.get("lanes", [])))
            self._lane_width = self.query_one("#board-lanes").size.width // lanes
        self._lane_width = max(_MIN_LANE, self._lane_width + cells)
        self._apply_lane_width()

    def _apply_lane_width(self) -> None:
        for lane in self.query(BoardLaneView):
            lane.styles.width = self._lane_width if self._lane_width else "1fr"

    def action_move(self, step: int) -> None:
        card = self._focused_card()
        if card is None:
            return
        lanes = [lane["id"] for lane in self.board.get("lanes", [])]
        index = lanes.index(card.lane_id) + step
        if 0 <= index < len(lanes):
            self._focus_id = card.task_id
            self.edit([{"op": "move_task", "task": card.task_id, "lane": lanes[index]}])

    def action_reorder(self, step: int) -> None:
        card = self._focused_card()
        if card is None:
            return
        cards = [task["id"] for task in self._tasks_in(card.lane_id)]
        index = cards.index(card.task_id) + step
        if 0 <= index < len(cards):
            self.edit(
                [{"op": "move_task", "task": card.task_id, "lane": card.lane_id, "index": index}]
            )

    def action_plan(self) -> None:
        card = self._focused_card()
        task = self.task(card.task_id) if card else None
        self.plan([task["id"]] if task and task["status"] in ("draft", "ready") else None)

    def action_play(self) -> None:
        self.play(None)

    def action_run(self) -> None:
        card = self._focused_card()
        if card is not None:
            self.play([card.task_id])

    def action_stop(self) -> None:
        self.run_worker(self._call(self._commands.board_stop()), exclusive=False)

    def action_toggle_activity(self) -> None:
        log = self.query_one("#board-activity", RichLog)
        log.display = not log.display
        self._activity_shown = True

    def action_add_lane(self) -> None:
        def added(title: str | None) -> None:
            if title:
                self.edit([{"op": "add_lane", "title": title}])

        self.app.push_screen(TextPrompt("New lane", placeholder="e.g. Review"), added)

    def action_rename_lane(self) -> None:
        lane = self._focused_lane()
        if lane is None:
            return

        def renamed(title: str | None) -> None:
            if title and title != lane["title"]:
                self.edit([{"op": "rename_lane", "lane": lane["id"], "title": title}])

        self.app.push_screen(TextPrompt("Rename lane", value=lane["title"]), renamed)

    def action_remove_lane(self) -> None:
        lane = self._focused_lane()
        if lane is None:
            return
        if lane.get("role"):
            self.notify(f"{lane['title']} is one of the board's own lanes; it can be renamed only.")
            return
        if self._confirm != f"lane:{lane['id']}":
            self._confirm = f"lane:{lane['id']}"
            self.notify(f"Press x again to remove {lane['title']}; its tasks go back to To Do.")
            return
        self._confirm = ""
        self.edit([{"op": "remove_lane", "lane": lane["id"]}])

    def action_delete(self) -> None:
        card = self._focused_card()
        if card is None:
            return
        if self._confirm != f"task:{card.task_id}":
            self._confirm = f"task:{card.task_id}"
            self.notify(f"Press Delete again to delete {card.task_id}.")
            return
        self._confirm = ""
        self.edit([{"op": "remove_task", "task": card.task_id}])
