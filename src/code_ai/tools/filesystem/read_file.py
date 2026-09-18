from __future__ import annotations

import asyncio
from typing import Any

from code_ai.core.errors import ToolArgumentError
from code_ai.tools.base import ToolCapability, ToolContext
from code_ai.tools.filesystem.common import read_text_file
from code_ai.tools.locations import LOCATION_SCHEMA, for_context
from code_ai.tools.output import bound_text
from code_ai.tools.schema import tool_schema
from code_ai.util.fileio import RetryPolicy


class ReadFileTool:
    name = "read_file"
    description = (
        "Read a UTF-8 text file, optionally bounded to a line range. Reads the "
        "workspace by default; pass location 'sandbox' to read something this "
        "session produced in its scratch area. Use it when you already know "
        "which file you need. To find out *which* file holds something, use "
        "search_index instead and read only what it points at - reading files "
        "to discover what is in them costs a whole file per guess. Pass "
        "start_line and end_line to read only the part you need."
    )
    capabilities = frozenset({ToolCapability.LOCAL_READ})
    input_schema = tool_schema(
        {
            "path": {
                "type": "string",
                "description": "Path of the text file to read, relative to the chosen location.",
            },
            "location": LOCATION_SCHEMA,
            "start_line": {
                "type": "integer",
                "description": "First line to return, 1-based. Defaults to the first line.",
            },
            "end_line": {
                "type": "integer",
                "description": "Last line to return, inclusive. Defaults to the last line.",
            },
        },
        required=("path",),
    )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        path_value = str(arguments.get("path", ""))
        if not path_value:
            raise ToolArgumentError("path is required.")
        location = for_context(context, arguments.get("location"))
        path = location.resolve(path_value, must_exist=True)
        # Reading is blocking, and the orchestrator shares its event loop with
        # the UI: done here it would freeze the screen, the cancel key and the
        # tool's own wall-clock backstop for as long as the read takes. On a
        # cloud-synced workspace a placeholder file downloads on open, so that
        # is unbounded.
        text, digest = await asyncio.to_thread(
            read_text_file, path, policy=RetryPolicy.from_config(context.config.file_io)
        )
        lines = text.splitlines(keepends=True)
        start = int(arguments.get("start_line") or 1)
        end = int(arguments.get("end_line") or len(lines))
        if start < 1 or end < start:
            raise ToolArgumentError("Invalid line range.")
        selected = "".join(lines[start - 1 : end])
        max_chars = context.config.budgets.max_tool_output_chars
        return {
            "path": location.relative(path),
            "location": location.location.value,
            "sha256": digest,
            "start_line": start,
            "end_line": min(end, len(lines)),
            "content": bound_text(selected, max_chars),
            "truncated": len(selected) > max_chars,
        }
