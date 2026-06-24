from __future__ import annotations

import asyncio
import json

from rich.syntax import Syntax
from textual.app import ComposeResult
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from code_ai.core.approval import ApprovalDecision, ApprovalRequest

# A dark Pygments theme that blends with the dialog background. The colours are
# language-agnostic: Pygments ships lexers for every supported language and we
# let it pick the right one from the file name (or the content itself).
_SYNTAX_THEME = "monokai"
_DIALOG_BG = "#111820"
_MAX_PREVIEW_CHARS = 40000


def _format_command(arguments: dict[str, object]) -> str:
    command = arguments.get("command")
    if isinstance(command, str) and command.strip():
        return command.strip()
    argv = arguments.get("argv")
    if isinstance(argv, list):
        return " ".join(str(item) for item in argv)
    return ""


def _guess_lexer(path: str, code: str) -> str:
    """Best-effort lexer name, agnostic of the language.

    When a path is available we match on its extension; otherwise we let
    Pygments analyse the content. Falls back to plain text on any failure so an
    unknown language never breaks the dialog.
    """

    try:
        if path:
            return Syntax.guess_lexer(path, code)
        from pygments.lexers import guess_lexer

        return guess_lexer(code).aliases[0]
    except Exception:
        return "text"


def _syntax(code: str, *, path: str = "", lexer: str | None = None) -> Syntax:
    if len(code) > _MAX_PREVIEW_CHARS:
        code = code[:_MAX_PREVIEW_CHARS] + "\n… (truncated)"
    return Syntax(
        code or "",
        lexer or _guess_lexer(path, code),
        theme=_SYNTAX_THEME,
        line_numbers=True,
        indent_guides=True,
        word_wrap=False,
        background_color=_DIALOG_BG,
        padding=0,
    )


def _render_title(request: ApprovalRequest) -> str:
    if request.policy_denied:
        return f"⚠  Permission required — the policy blocked '{request.tool_name}'"
    return f"Permission required — run '{request.tool_name}'?"


def _render_preview(request: ApprovalRequest) -> tuple[str, Syntax]:
    """Return a one-line summary and a syntax-highlighted body for the call."""

    args = request.arguments
    tool = request.tool_name

    if tool == "write_file":
        path = str(args.get("path", "") or "")
        content = str(args.get("content", "") or "")
        meta = f"Create / overwrite:  {path}" if path else "Create file"
        return meta, _syntax(content, path=path)

    if tool == "edit_code":
        path = str(args.get("path", "") or "")
        new_text = str(args.get("new_text", "") or "")
        meta = f"Edit:  {path}" if path else "Edit file"
        return meta, _syntax(new_text, path=path)

    if tool == "execute_command":
        command = _format_command(args)
        return "Command", _syntax(command, lexer="console")

    rendered = json.dumps(args, indent=2, default=str, ensure_ascii=False)
    return "Arguments", _syntax(rendered, lexer="json")


def _render_info(request: ApprovalRequest) -> str:
    lines: list[str] = []
    if request.capabilities:
        lines.append(f"Capabilities: {', '.join(request.capabilities)}")
    if request.reason:
        lines.append(f"Reason: {request.reason}")
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
        meta, body = _render_preview(self._request)
        info = _render_info(self._request)
        with Vertical(id="approval-dialog"):
            yield Static(_render_title(self._request), id="approval-title")
            yield Static(meta, id="approval-meta")
            with ScrollableContainer(id="approval-body"):
                yield Static(body, id="approval-code")
            if info:
                yield Static(info, id="approval-info")
            yield Static(
                "[1] Deny   ·   [2] Allow once   ·   [3] Always allow (this session)",
                id="approval-keys",
            )
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
