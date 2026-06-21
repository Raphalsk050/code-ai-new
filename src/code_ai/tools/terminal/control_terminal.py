from __future__ import annotations

from typing import Any

from code_ai.core.errors import ToolArgumentError
from code_ai.tools.base import ToolCapability, ToolContext
from code_ai.tools.schema import tool_schema


class ControlTerminalTool:
    name = "control_terminal"
    description = "Create and control a persistent POSIX terminal session inside the workspace."
    capabilities = frozenset({ToolCapability.INTERACTIVE_TERMINAL})
    input_schema = tool_schema(
        {
            "action": {
                "type": "string",
                "enum": [
                    "create",
                    "send_text",
                    "enter",
                    "control",
                    "resize",
                    "interrupt",
                    "terminate",
                    "status",
                ],
                "description": "Terminal operation to perform.",
            },
            "session_id": {
                "type": "string",
                "description": "Target session id. Required for every action except 'create'.",
            },
            "text": {"type": "string", "description": "Literal text for the 'send_text' action."},
            "key": {"type": "string", "description": "Control key (e.g. 'c') for 'control'."},
            "cwd": {
                "type": "string",
                "description": "Workspace-relative working directory for 'create'.",
            },
            "command": {
                "type": "string",
                "description": "Optional command to launch for the 'create' action.",
            },
            "rows": {"type": "integer", "description": "Rows for 'create'/'resize'. Default 24."},
            "columns": {
                "type": "integer",
                "description": "Columns for 'create'/'resize'. Defaults to 80.",
            },
        },
        required=("action",),
    )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        if context.terminal_manager is None:
            raise ToolArgumentError("Terminal manager is not configured.")
        action = str(arguments.get("action", ""))
        session_id = arguments.get("session_id")
        if action == "create":
            cwd = context.workspace.relative_workdir(arguments.get("cwd"))
            created = context.terminal_manager.create(
                cwd=cwd,
                command=arguments.get("command"),
                rows=int(arguments.get("rows") or 24),
                cols=int(arguments.get("columns") or 80),
            )
            await context.event_bus.emit(
                "terminal.screen.updated",
                context.terminal_manager.read_screen(created),
                source="tool.control_terminal",
            )
            return {"session_id": created, "status": "created"}
        if not isinstance(session_id, str) or not session_id:
            raise ToolArgumentError("session_id is required for this action.")
        if action == "send_text":
            context.terminal_manager.send_text(session_id, str(arguments.get("text", "")))
        elif action == "enter":
            context.terminal_manager.send_enter(session_id)
        elif action == "control":
            context.terminal_manager.send_control(session_id, str(arguments.get("key", "")))
        elif action == "resize":
            context.terminal_manager.resize(
                session_id,
                int(arguments.get("rows") or 24),
                int(arguments.get("columns") or 80),
            )
        elif action == "interrupt":
            context.terminal_manager.interrupt(session_id)
        elif action == "terminate":
            context.terminal_manager.terminate(session_id)
            return {"session_id": session_id, "status": "terminated"}
        elif action != "status":
            raise ToolArgumentError(f"Unsupported terminal action: {action}")

        screen = context.terminal_manager.read_screen(session_id)
        await context.event_bus.emit(
            "terminal.screen.updated", screen, source="tool.control_terminal"
        )
        return screen
