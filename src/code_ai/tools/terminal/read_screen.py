from __future__ import annotations

from typing import Any

from code_ai.core.errors import ToolArgumentError
from code_ai.tools.base import ToolCapability, ToolContext


class ReadScreenTool:
    name = "read_screen"
    description = "Read the current emulated screen of a persistent terminal session."
    capabilities = frozenset(
        {ToolCapability.INTERACTIVE_TERMINAL, ToolCapability.LOCAL_READ}
    )
    input_schema = {
        "type": "object",
        "properties": {
            "session_id": {"type": "string"},
            "include_cursor": {"type": "boolean"},
        },
        "required": ["session_id"],
        "additionalProperties": False,
    }

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        if context.terminal_manager is None:
            raise ToolArgumentError("Terminal manager is not configured.")
        session_id = str(arguments.get("session_id") or "")
        if not session_id:
            raise ToolArgumentError("session_id is required.")
        return context.terminal_manager.read_screen(
            session_id,
            include_cursor=bool(arguments.get("include_cursor", True)),
        )
