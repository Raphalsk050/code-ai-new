from __future__ import annotations

import asyncio
import bisect
import itertools
from typing import Any

from code_ai.core.errors import ToolArgumentError
from code_ai.tools.base import ToolCapability, ToolContext
from code_ai.tools.filesystem.common import read_text_file
from code_ai.tools.filesystem.markdown_images import (
    MAX_IMAGES_PER_READ,
    is_markdown,
    markdown_image_fields,
)
from code_ai.tools.locations import LOCATION_SCHEMA, for_context
from code_ai.tools.output import bound_text, bound_text_kept
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
        "start_line and end_line to read only the part you need. Reading a "
        "Markdown file (.md, .markdown, .mdx) also attaches the local images "
        "the returned lines reference, so you can look at them (at most "
        f"{MAX_IMAGES_PER_READ}, in order); the result's 'images' field names "
        "each one and lists what was not attached and why."
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
        policy = RetryPolicy.from_config(context.config.file_io)
        # Reading is blocking, and the orchestrator shares its event loop with
        # the UI: done here it would freeze the screen, the cancel key and the
        # tool's own wall-clock backstop for as long as the read takes. On a
        # cloud-synced workspace a placeholder file downloads on open, so that
        # is unbounded.
        text, digest = await asyncio.to_thread(read_text_file, path, policy=policy)
        lines = text.splitlines(keepends=True)
        start = int(arguments.get("start_line") or 1)
        end = int(arguments.get("end_line") or len(lines))
        if start < 1 or end < start:
            raise ToolArgumentError("Invalid line range.")
        selected = "".join(lines[start - 1 : end])
        max_chars = context.config.budgets.max_tool_output_chars
        payload: dict[str, Any] = {
            "path": location.relative(path),
            "location": location.location.value,
            "sha256": digest,
            "start_line": start,
            "end_line": min(end, len(lines)),
            "content": bound_text(selected, max_chars),
            "truncated": len(selected) > max_chars,
        }
        if is_markdown(path):
            # Off the loop for the same reason as the text: the images are
            # files too, and decoding one is not free either.
            payload.update(
                await asyncio.to_thread(
                    markdown_image_fields,
                    lines,
                    _returned_lines(lines, start, end, max_chars),
                    document=path,
                    location=location,
                    policy=policy,
                )
            )
        return payload


def _returned_lines(
    lines: list[str], start: int, end: int, max_chars: int
) -> list[tuple[int, int]]:
    """The 1-based line ranges whose text the result actually carries.

    The requested range, less whatever bound_text cut out of its middle: an
    image whose reference the model was never shown would arrive with nothing
    to say what it is.
    """

    last = min(end, len(lines))
    if start > last:
        return []
    # offsets[i] is where line start + i begins within the returned text.
    offsets = list(itertools.accumulate((len(line) for line in lines[start - 1 : last]), initial=0))
    ranges: list[tuple[int, int]] = []
    for low, high in bound_text_kept(offsets[-1], max_chars):
        first = bisect.bisect_right(offsets, low) - 1
        final = min(bisect.bisect_left(offsets, high) - 1, last - start)
        if low < high and first <= final:
            ranges.append((start + first, start + final))
    return ranges
