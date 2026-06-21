from __future__ import annotations

from typing import Any

from code_ai.core.errors import ToolArgumentError
from code_ai.tools.base import ToolCapability, ToolContext
from code_ai.tools.schema import tool_schema


class StartTerminalTool:
    name = "start_terminal"
    description = "Start one persistent terminal session inside the workspace."
    capabilities = frozenset({ToolCapability.INTERACTIVE_TERMINAL})
    input_schema = tool_schema(
        {
            "cwd": {
                "type": "string",
                "description": "Workspace-relative directory to start in. Defaults to the root.",
            },
            "command": {
                "type": "string",
                "description": "Optional command to launch instead of a plain shell.",
            },
        },
    )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        manager = _terminal_manager(context)
        cwd = context.workspace.relative_workdir(arguments.get("cwd"))
        command = arguments.get("command")
        created = manager.create(cwd=cwd, command=command if isinstance(command, str) else None)
        screen = manager.read_screen(created)
        await context.event_bus.emit(
            "terminal.screen.updated", screen, source="tool.start_terminal"
        )
        return {**screen, "status": "created"}


class SendTerminalTextTool:
    name = "send_terminal_text"
    description = "Send literal text to a persistent terminal session."
    capabilities = frozenset({ToolCapability.INTERACTIVE_TERMINAL})
    input_schema = tool_schema(
        {
            "session_id": {
                "type": "string",
                "description": "Identifier of the target terminal session.",
            },
            "text": {
                "type": "string",
                "description": "Literal text to send to the session (no implicit newline).",
            },
        },
        required=("session_id", "text"),
    )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        manager = _terminal_manager(context)
        session_id = _session_id(arguments)
        manager.send_text(session_id, str(arguments.get("text", "")))
        screen = manager.read_screen(session_id)
        await context.event_bus.emit(
            "terminal.screen.updated", screen, source="tool.send_terminal_text"
        )
        return screen


class TerminalEnterTool:
    name = "terminal_enter"
    description = "Press Enter in a persistent terminal session."
    capabilities = frozenset({ToolCapability.INTERACTIVE_TERMINAL})
    input_schema = tool_schema(
        {
            "session_id": {
                "type": "string",
                "description": "Identifier of the target terminal session.",
            },
        },
        required=("session_id",),
    )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        manager = _terminal_manager(context)
        session_id = _session_id(arguments)
        manager.send_enter(session_id)
        screen = manager.read_screen(session_id)
        await context.event_bus.emit(
            "terminal.screen.updated", screen, source="tool.terminal_enter"
        )
        return screen


class InterruptTerminalTool:
    name = "interrupt_terminal"
    description = "Send Ctrl-C to a persistent terminal session."
    capabilities = frozenset({ToolCapability.INTERACTIVE_TERMINAL})
    input_schema = tool_schema(
        {
            "session_id": {
                "type": "string",
                "description": "Identifier of the target terminal session.",
            },
        },
        required=("session_id",),
    )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        manager = _terminal_manager(context)
        session_id = _session_id(arguments)
        manager.interrupt(session_id)
        screen = manager.read_screen(session_id)
        await context.event_bus.emit(
            "terminal.screen.updated", screen, source="tool.interrupt_terminal"
        )
        return screen


class TerminateTerminalTool:
    name = "terminate_terminal"
    description = "Terminate one persistent terminal session."
    capabilities = frozenset({ToolCapability.INTERACTIVE_TERMINAL})
    input_schema = tool_schema(
        {
            "session_id": {
                "type": "string",
                "description": "Identifier of the target terminal session.",
            },
        },
        required=("session_id",),
    )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        manager = _terminal_manager(context)
        session_id = _session_id(arguments)
        manager.terminate(session_id)
        return {"session_id": session_id, "status": "terminated"}


def _terminal_manager(context: ToolContext) -> Any:
    if context.terminal_manager is None:
        raise ToolArgumentError("Terminal manager is not configured.")
    return context.terminal_manager


def _session_id(arguments: dict[str, Any]) -> str:
    session_id = str(arguments.get("session_id") or "")
    if not session_id:
        raise ToolArgumentError("session_id is required.")
    return session_id
