from __future__ import annotations

import asyncio
import difflib
import json
import re

from rich.console import RenderableType
from rich.style import Style
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static

from code_ai.core.approval import ApprovalDecision, ApprovalRequest
from code_ai.ui.terminal.code_view import syntax_block

# Unified-diff line prefixes mapped to a Claude-Code-style background tint.
# Context lines (a leading space) and hunk headers fall through to a dim default.
_DIFF_LINE_STYLES = {
    "+": Style(color="#a6e3a1", bgcolor="#1d3322"),
    "-": Style(color="#f38ba8", bgcolor="#3a1620"),
    "@": Style(color="#7aa2f7", bold=True),
}
_DIFF_CONTEXT_LINES = 3
_HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")
_GUTTER_STYLE = Style(color="#5c6773")


def _format_command(arguments: dict[str, object]) -> str:
    command = arguments.get("command")
    if isinstance(command, str) and command.strip():
        return command.strip()
    argv = arguments.get("argv")
    if isinstance(argv, list):
        return " ".join(str(item) for item in argv)
    return ""


def _render_command(command: str) -> Text:
    """Render a shell command as a terminal prompt, not as source code.

    A command is a thing you *run*, not a file you write, so it gets a ``$``
    prompt and a single solid color with no line numbers — visually distinct
    from the line-numbered, multi-color syntax used for code previews.
    """
    text = Text(no_wrap=False)
    for index, line in enumerate(command.split("\n")):
        if index:
            text.append("\n")
        text.append("$ " if index == 0 else "  ", style=Style(color="#ff9f1c", bold=True))
        text.append(line, style=Style(color="#7ee787"))
    return text


def _render_diff(old_text: str, new_text: str) -> Text:
    """Unified diff with Claude-Code-style red/green line backgrounds.

    ``old_text``/``new_text`` come straight from the edit_code call, so the
    diff is available before the tool ever touches the filesystem. Each line
    is prefixed with an old/new line-number gutter so a reviewer can match
    the diff back to the file without opening it separately.
    """

    diff_lines = list(
        difflib.unified_diff(
            old_text.splitlines(),
            new_text.splitlines(),
            n=_DIFF_CONTEXT_LINES,
            lineterm="",
        )
    )
    # Drop the "--- "/"+++ " file headers; the dialog already shows the path.
    diff_lines = [line for line in diff_lines if not line.startswith(("--- ", "+++ "))]

    width = max(len(str(len(old_text.splitlines()))), len(str(len(new_text.splitlines()))), 1)
    blank_gutter = " " * (2 * width + 1)
    old_line = new_line = 0

    text = Text(no_wrap=True)
    for index, line in enumerate(diff_lines):
        if index:
            text.append("\n")
        marker = line[:1]
        if marker == "@":
            match = _HUNK_HEADER_RE.match(line)
            if match:
                old_line, new_line = int(match.group(1)), int(match.group(2))
            text.append(blank_gutter + " ", style=_GUTTER_STYLE)
            text.append(line, style=_DIFF_LINE_STYLES["@"])
            continue
        if marker == "-":
            gutter = f"{old_line:>{width}} {'':>{width}}"
            old_line += 1
        elif marker == "+":
            gutter = f"{'':>{width}} {new_line:>{width}}"
            new_line += 1
        else:
            gutter = f"{old_line:>{width}} {new_line:>{width}}"
            old_line += 1
            new_line += 1
        text.append(gutter + " ", style=_GUTTER_STYLE)
        text.append(line, style=_DIFF_LINE_STYLES.get(marker, "#9fb3c8"))
    return text


def _render_title(request: ApprovalRequest) -> str:
    if request.policy_denied:
        return f"⚠  Permission required — the policy blocked '{request.tool_name}'"
    return f"Permission required — run '{request.tool_name}'?"


def _render_preview(request: ApprovalRequest) -> tuple[str, RenderableType]:
    """Return a one-line summary and a syntax-highlighted body for the call."""

    args = request.arguments
    tool = request.tool_name

    if tool == "write_file":
        path = str(args.get("path", "") or "")
        content = str(args.get("content", "") or "")
        meta = f"Create / overwrite:  {path}" if path else "Create file"
        return meta, syntax_block(content, path=path)

    if tool == "edit_code":
        path = str(args.get("path", "") or "")
        new_text = str(args.get("new_text", "") or "")
        old_text = args.get("old_text")
        meta = f"Edit:  {path}" if path else "Edit file"
        if isinstance(old_text, str) and old_text != new_text:
            return meta, _render_diff(old_text, new_text)
        return meta, syntax_block(new_text, path=path)

    if tool == "execute_command":
        command = _format_command(args)
        return "Command", _render_command(command)

    rendered = json.dumps(args, indent=2, default=str, ensure_ascii=False)
    return "Arguments", syntax_block(rendered, lexer="json")


def _render_info(request: ApprovalRequest) -> str:
    lines: list[str] = []
    if request.capabilities:
        lines.append(f"Capabilities: {', '.join(request.capabilities)}")
    if request.reason:
        lines.append(f"Reason: {request.reason}")
    return "\n".join(lines)


def _render_justification(request: ApprovalRequest) -> str:
    """The model's own explanation of why this call is needed, if it gave one.

    Comes from the optional ``reason`` argument on write_file/edit_code/
    execute_command — purely informational, never used by the tools
    themselves. Gated behind /config learn so users who don't want the extra
    text can turn it off.
    """

    reason = request.arguments.get("reason")
    return reason.strip() if isinstance(reason, str) else ""


class ApprovalModal(ModalScreen[ApprovalDecision]):
    """Blocking approve/deny dialog for a single gated tool call."""

    BINDINGS = [
        ("escape", "deny", "Deny"),
        ("1", "deny", "Deny"),
        ("2", "allow_once", "Allow once"),
        ("3", "allow_session", "Always allow"),
    ]

    def __init__(self, request: ApprovalRequest, *, learn_enabled: bool = True) -> None:
        super().__init__()
        self._request = request
        self._learn_enabled = learn_enabled

    def compose(self) -> ComposeResult:
        meta, body = _render_preview(self._request)
        info = _render_info(self._request)
        justification = _render_justification(self._request) if self._learn_enabled else ""
        with Vertical(id="approval-dialog"):
            yield Static(_render_title(self._request), id="approval-title")
            yield Static(meta, id="approval-meta")
            if justification:
                yield Static(f"Why: {justification}", id="approval-justification")
            with ScrollableContainer(id="approval-body"):
                yield Static(body, id="approval-code")
            if info:
                yield Static(info, id="approval-info")
            yield Static(
                "[1] Deny   ·   [2] Allow once   ·   [3] Always allow (this session)",
                id="approval-keys",
            )
            yield Input(
                placeholder="On deny: tell the agent why, or what to do instead (optional)…",
                id="approval-feedback",
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

    def on_input_submitted(self, event: Input.Submitted) -> None:
        # Enter in the feedback field denies, carrying the typed text to the model.
        if event.input.id == "approval-feedback":
            self.action_deny()

    def action_deny(self) -> None:
        self.dismiss(ApprovalDecision.deny(self._deny_reason()))

    def _deny_reason(self) -> str:
        try:
            feedback = self.query_one("#approval-feedback", Input).value.strip()
        except Exception:
            feedback = ""
        return feedback or "Denied by user."

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

    def __init__(self, app, config) -> None:
        self._app = app
        self._config = config

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

        # Read learn live so toggling /config learn applies to the very next prompt.
        modal = ApprovalModal(request, learn_enabled=self._config.learn)
        self._app.push_screen(modal, _resolve)
        return await future
