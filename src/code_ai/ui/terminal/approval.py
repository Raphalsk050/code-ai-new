from __future__ import annotations

import asyncio
import json

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from code_ai.core.approval import ApprovalDecision, ApprovalRequest


def _format_command(arguments: dict[str, object]) -> str:
    command = arguments.get("command")
    if isinstance(command, str) and command.strip():
        return command.strip()
    argv = arguments.get("argv")
    if isinstance(argv, list):
        return " ".join(str(item) for item in argv)
    return ""


def _render_title(request: ApprovalRequest) -> str:
    if request.policy_denied:
        return f"⚠  Permission required — the policy blocked '{request.tool_name}'"
    return f"Permission required — run '{request.tool_name}'?"


def _render_body(request: ApprovalRequest) -> str:
    lines: list[str] = []
    command = _format_command(request.arguments) if request.tool_name == "execute_command" else ""
    if command:
        lines.append(f"Command:\n  {command}")
    else:
        rendered = json.dumps(request.arguments, indent=2, default=str, ensure_ascii=False)
        lines.append("Arguments:")
        lines.append(rendered[:1500])
    if request.capabilities:
        lines.append(f"Capabilities: {', '.join(request.capabilities)}")
    if request.reason:
        lines.append(f"Reason: {request.reason}")
    lines.append("")
    lines.append("[1] Deny   ·   [2] Allow once   ·   [3] Always allow (this session)")
    return "\n".join(lines)


class ApprovalModal(ModalScreen[ApprovalDecision]):
    """Blocking approve/deny dialog for a single gated tool call."""

    BINDINGS = [
        ("escape", "deny", "Deny"),
        ("1", "deny", "Deny"),
        ("2", "allow_once", "Allow once"),
        ("3", "allow_session", "Always allow"),
    ]

    def __init__(self, request: ApprovalRequest) -> None:
        super().__init__()
        self._request = request

    def compose(self) -> ComposeResult:
        with Vertical(id="approval-dialog"):
            yield Static(_render_title(self._request), id="approval-title")
            yield Static(_render_body(self._request), id="approval-body")
            with Horizontal(id="approval-actions"):
                yield Button("Deny (Esc)", variant="error", id="approval-deny")
                yield Button("Allow once (2)", variant="primary", id="approval-once")
                yield Button("Always allow (3)", variant="success", id="approval-session")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "approval-once":
            self.action_allow_once()
        elif event.button.id == "approval-session":
            self.action_allow_session()
        else:
            self.action_deny()

    def action_deny(self) -> None:
        self.dismiss(ApprovalDecision.deny("Denied by user."))

    def action_allow_once(self) -> None:
        self.dismiss(ApprovalDecision.allow_once())

    def action_allow_session(self) -> None:
        self.dismiss(ApprovalDecision.allow_session())


class TerminalApprovalGateway:
    """Approval gateway backed by a Textual modal screen.

    The orchestrator runs as an asyncio task on the same loop as the Textual
    app, so we push the modal with a callback and await a future the callback
    resolves. A dismissal without a value is treated as a denial.
    """

    def __init__(self, app) -> None:
        self._app = app

    async def request_approval(self, request: ApprovalRequest) -> ApprovalDecision:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[ApprovalDecision] = loop.create_future()

        def _resolve(decision: ApprovalDecision | None) -> None:
            if future.done():
                return
            future.set_result(
                decision
                if isinstance(decision, ApprovalDecision)
                else ApprovalDecision.deny("Dismissed without a choice.")
            )

        self._app.push_screen(ApprovalModal(request), _resolve)
        return await future
