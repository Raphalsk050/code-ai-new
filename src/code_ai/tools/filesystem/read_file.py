from __future__ import annotations

from typing import Any

from code_ai.core.errors import ToolArgumentError
from code_ai.tools.base import ToolContext
from code_ai.tools.filesystem.common import read_text_file
from code_ai.tools.output import bound_text


class ReadFileTool:
    name = "read_file"
    description = "Read a UTF-8 text file inside the workspace, optionally bounded to a line range."
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "start_line": {"type": "integer", "minimum": 1},
            "end_line": {"type": "integer", "minimum": 1},
            "max_chars": {"type": "integer", "minimum": 1},
        },
        "required": ["path"],
        "additionalProperties": False,
    }

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        path_value = str(arguments.get("path", ""))
        if not path_value:
            raise ToolArgumentError("path is required.")
        path = context.workspace.resolve(path_value, must_exist=True)
        text, digest = read_text_file(path)
        lines = text.splitlines(keepends=True)
        start = int(arguments.get("start_line") or 1)
        end = int(arguments.get("end_line") or len(lines))
        if start < 1 or end < start:
            raise ToolArgumentError("Invalid line range.")
        selected = "".join(lines[start - 1 : end])
        max_chars = int(arguments.get("max_chars") or context.config.budgets.max_tool_output_chars)
        return {
            "path": str(path.relative_to(context.workspace.root)),
            "sha256": digest,
            "start_line": start,
            "end_line": min(end, len(lines)),
            "content": bound_text(selected, max_chars),
            "truncated": len(selected) > max_chars,
        }
