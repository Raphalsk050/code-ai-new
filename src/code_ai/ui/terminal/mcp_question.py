"""A question an MCP server asks in the middle of a tool call, as a terminal form.

The same request the panel shows as a card: the server's message, one field per
schema property, Send / Decline / Cancel. The tool call waits for the answer, so
the dialog opens the moment the question arrives rather than at the end of the
turn. ``/mcp answer`` stays as the keyboard-only way to answer.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Select, Static

# Settles the question: action and content in, why it was refused out ("" = taken).
Answer = Callable[[str, dict[str, Any] | None], str]


class McpQuestionModal(ModalScreen[None]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, request: dict[str, Any], answer: Answer) -> None:
        super().__init__()
        self._request = request
        self._answer = answer
        self._fields: list[dict[str, Any]] = list(request.get("fields") or [])
        # Answered some other way (/mcp answer, a timeout): any button just closes it.
        self.settled = False

    @property
    def request_id(self) -> str:
        return str(self._request.get("id") or "")

    def compose(self) -> ComposeResult:
        with Vertical(id="mcp-question-dialog"):
            yield Static(f"{self._request.get('server')} asks", id="mcp-question-title")
            message = str(self._request.get("message") or "")
            if message:
                yield Static(message, id="mcp-question-message")
            with VerticalScroll(id="mcp-question-fields"):
                for index, field in enumerate(self._fields):
                    yield from self._field(index, field)
            yield Static("The server's tool call waits for this answer.", id="mcp-question-note")
            yield Static("", id="mcp-question-error")
            with Horizontal(id="mcp-question-actions"):
                yield Button("Send", variant="primary", id="mcp-question-send")
                yield Button("Decline", variant="error", id="mcp-question-decline")
                yield Button("Cancel (Esc)", id="mcp-question-cancel")

    def _field(self, index: int, field: dict[str, Any]) -> ComposeResult:
        # Ids by position: a property name can hold anything a widget id cannot.
        widget_id = f"mcp-q-{index}"
        label = str(field.get("title") or field["name"]) + (" *" if field.get("required") else "")
        default = field.get("default")
        if field.get("type") == "boolean":
            yield Checkbox(label, default is True, id=widget_id, compact=True)
        else:
            yield Static(label, classes="mcp-question-label")
            if field.get("enum"):
                values = [str(value) for value in field["enum"]]
                titles = field.get("enum_names") or values
                yield Select(
                    list(zip(titles, values, strict=False)),
                    id=widget_id,
                    value=str(default) if str(default) in values else Select.NULL,
                    prompt="Choose",
                )
            else:
                numeric = field.get("type") in ("number", "integer")
                # Bordered, not compact: on a blank form a borderless field
                # does not show where to type.
                yield Input(
                    value="" if default is None else str(default),
                    type="number" if numeric else "text",
                    id=widget_id,
                )
        if field.get("description"):
            yield Static(str(field["description"]), classes="mcp-question-hint")

    def values(self) -> dict[str, Any]:
        """What the form holds, leaving out what was left empty."""

        values: dict[str, Any] = {}
        for index, field in enumerate(self._fields):
            widget = self.query_one(f"#mcp-q-{index}")
            if isinstance(widget, Checkbox):
                values[field["name"]] = widget.value
            elif isinstance(widget, Select):
                if isinstance(widget.value, str):
                    values[field["name"]] = widget.value
            elif isinstance(widget, Input) and widget.value.strip():
                values[field["name"]] = widget.value.strip()
        return values

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "mcp-question-send":
            self._send()
        elif event.button.id == "mcp-question-decline":
            self._settle("decline")
        else:
            self.action_cancel()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._send()

    def action_cancel(self) -> None:
        self._settle("cancel")

    def _send(self) -> None:
        if not self.settled:
            problem = self._answer("accept", self.values())
            if problem:
                # Left open: the answer can be fixed instead of typed again.
                self.query_one("#mcp-question-error", Static).update(f"✗ {problem}")
                return
        self._close()

    def _settle(self, action: str) -> None:
        if not self.settled:
            self._answer(action, None)
        self._close()

    def _close(self) -> None:
        # Settled first: the "resolved" event that follows must not dismiss it again.
        self.settled = True
        self.dismiss(None)
