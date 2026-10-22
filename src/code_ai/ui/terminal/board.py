"""The task board in the terminal: the panel's board, driven from the keyboard.

Lanes side by side, a card per task; Enter opens a task to edit its sub-tasks,
checklist and validation criteria. Every change goes through the same
SessionCommands the bridge uses, and the screen redraws from the board the
backend announces, so it never holds a copy of its own. A card that changes
lane flies there over the lanes, as it does in the panel.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.geometry import Region
from textual.screen import ModalScreen, Screen
from textual.timer import Timer
from textual.widgets import Button, Checkbox, Footer, Input, Static, TextArea

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
    if parts:
        text.append("\n" + "  ".join(parts), style="dim")
    if task.get("depends_on") and status != "done":
        text.append(f"\nafter {', '.join(task['depends_on'])}", style="dim")
    line = activity or task.get("activity", "")
    if line and status in _BUSY:
        text.append(f"\n{line}", style="italic dim")
    return text


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
            _card_text(task, activity), id=f"board-card-{task['id']}", classes="board-card"
        )
        self.task_id = task["id"]
        self.lane_id = task["lane"]
        self.set_class(True, f"s-{task.get('status', 'draft')}")

    def show(self, task: dict[str, Any], activity: str = "") -> None:
        self.update(_card_text(task, activity))


class BoardLaneView(Vertical):
    def __init__(
        self, lane: dict[str, Any], tasks: list[dict[str, Any]], activity: dict[str, str]
    ) -> None:
        super().__init__(id=f"board-lane-{lane['id']}", classes="board-lane")
        self.lane = lane
        self._tasks = tasks
        self._activity = activity

    def compose(self) -> ComposeResult:
        yield Static(
            Text.assemble((self.lane["title"].upper(), "bold"), (f"  {len(self._tasks)}", "dim")),
            classes="board-lane-title",
        )
        with VerticalScroll(classes="board-lane-cards"):
            for task in self._tasks:
                yield BoardCard(task, self._activity.get(task["id"], ""))
            if not self._tasks:
                yield Static("—", classes="board-lane-empty")


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
        self._activity: dict[str, str] = {}
        self._review = ""
        self._subscription: Any = None
        self._confirm = ""
        self._focus_id = ""
        self._flights: list[Timer] = []

    # -- reading ----------------------------------------------------------

    def task(self, task_id: str) -> dict[str, Any] | None:
        return next((task for task in self.board.get("tasks", []) if task["id"] == task_id), None)

    def _tasks_in(self, lane_id: str) -> list[dict[str, Any]]:
        return [task for task in self.board.get("tasks", []) if task["lane"] == lane_id]

    def _focused_card(self) -> BoardCard | None:
        return self.focused if isinstance(self.focused, BoardCard) else None

    def _focused_lane(self) -> dict[str, Any] | None:
        card = self._focused_card()
        lane_id = card.lane_id if card else self._focus_lane_id
        return next((lane for lane in self.board.get("lanes", []) if lane["id"] == lane_id), None)

    _focus_lane_id = "todo"

    # -- drawing ----------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Static(self._header(), id="board-header")
        yield Static(self._banner(), id="board-review")
        yield Horizontal(id="board-lanes")
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
            BoardLaneView(lane, self._tasks_in(lane["id"]), self._activity)
            for lane in self.board.get("lanes", [])
        ]
        await container.mount_all(lanes)
        self.query_one("#board-header", Static).update(self._header())
        banner = self.query_one("#board-review", Static)
        banner.update(self._banner())
        banner.display = bool(self._waiting()) and not self.board.get("running")

    def _refocus(self) -> None:
        cards = list(self.query(BoardCard))
        target = next((card for card in cards if card.task_id == self._focus_id), None)
        if target is None and cards:
            target = next((card for card in cards if card.lane_id == self._focus_lane_id), cards[0])
        if target is not None:
            target.focus()

    def on_descendant_focus(self, event: Any) -> None:
        widget = event.widget if hasattr(event, "widget") else None
        if isinstance(widget, BoardCard):
            self._focus_id = widget.task_id
            self._focus_lane_id = widget.lane_id

    # -- backend ----------------------------------------------------------

    async def _on_event(self, envelope: Any) -> None:
        payload = envelope.payload or {}
        if envelope.event_type == "board.changed":
            await self.apply_snapshot(payload.get("board") or {}, payload.get("moved") or [])
        elif envelope.event_type == "board.activity":
            task_id = str(payload.get("task") or "")
            self._activity[task_id] = str(payload.get("text") or "")
            task = self.task(task_id)
            for card in self.query(BoardCard):
                if card.task_id == task_id and task is not None:
                    card.show(task, self._activity[task_id])
        elif envelope.event_type == "board.review.requested":
            self._review = str(payload.get("message") or "")
        elif envelope.event_type == "board.run.completed":
            self._activity.clear()
            failed = payload.get("failed") or []
            if failed:
                self.notify(
                    f"Not done: {', '.join(failed)}. Open the card to see why.", severity="warning"
                )

    async def apply_snapshot(self, board: dict[str, Any], moved: list[dict[str, str]]) -> None:
        if not board:
            return
        before = {card.task_id: card.region for card in self.query(BoardCard)}
        self.board = board
        if not self.is_mounted:
            return
        await self._draw_lanes()
        self._refocus()
        done = _lane_of(board, "done")
        for move in moved:
            origin = before.get(move["task"])
            if origin is not None:
                # After the next refresh: until then the new cards have no place.
                self.call_after_refresh(
                    self._fly, move["task"], origin, landing=bool(done and move["to"] == done["id"])
                )
        for screen in self.app.screen_stack:
            if isinstance(screen, _EditorBase):
                await screen.redraw()

    def _fly(self, task_id: str, origin: Region, *, landing: bool) -> None:
        """Send a copy of the card from where it was to where it is now, over the lanes."""

        cards = [card for card in self.query(BoardCard) if card.task_id == task_id]
        task = self.task(task_id)
        if not cards or task is None or origin.width == 0:
            return
        card = cards[0]
        target = card.region
        if target.width == 0:
            if landing:
                card.add_class("landed")
                self.set_timer(0.9, lambda: card.remove_class("landed"))
            return
        ghost = Static(_card_text(task), classes="board-card board-ghost")
        ghost.styles.width = origin.width
        ghost.styles.height = origin.height
        ghost.styles.offset = (origin.x, origin.y)
        self.mount(ghost)
        card.styles.opacity = 0.0
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
                card.styles.opacity = 1.0
                if landing:
                    card.add_class("landed")
                    self.set_timer(0.9, lambda: card.remove_class("landed"))

        timer = self.set_interval(_FLIGHT_STEP_S, step)
        self._flights.append(timer)

    def edit(self, operations: list[dict[str, Any]]) -> None:
        async def send() -> None:
            result = await self._commands.board_edit(operations)
            if not result.ok:
                self.notify(result.message, severity="warning")

        self.run_worker(send(), exclusive=False)

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
        card = self._focused_card()
        if card is not None:
            self.app.push_screen(TaskEditor(self, card.task_id))

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
            cards = [card for card in self.query(BoardCard) if card.lane_id == lane["id"]]
            self._focus_lane_id = lane["id"]
            if cards:
                cards[0].focus()
                return

    def action_focus_card(self, step: int) -> None:
        card = self._focused_card()
        if card is None:
            self._refocus()
            return
        cards = [item for item in self.query(BoardCard) if item.lane_id == card.lane_id]
        index = cards.index(card) + step
        if 0 <= index < len(cards):
            cards[index].focus()

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
