"""Letting the agent look at the desktop the user is sitting in front of."""

from __future__ import annotations

import asyncio
from typing import Any

from code_ai.tools.base import TOOL_IMAGES_KEY, ToolCapability, ToolContext
from code_ai.tools.computer.capture import capture_screen_base64
from code_ai.tools.schema import tool_schema


class ReadDesktopScreenTool:
    """Screenshot the desktop and attach it to the tool result.

    Named apart from the terminal's ``read_screen``, which reads an emulated
    PTY: one answers "what did that command print", the other "what is on the
    user's monitor". Sharing a name would leave the model picking between them
    by luck.
    """

    name = "capture_screen"
    description = (
        "Take a screenshot of the desktop and look at it. Use it to see what is "
        "actually on the user's monitor: which window has focus, what a GUI is "
        "showing, whether a dialog is waiting, what an application rendered "
        "after a click. The image comes back attached, so it is seen rather "
        "than described. For the output of a command in a terminal session use "
        "read_screen instead; this is the whole screen, not a shell."
    )
    capabilities = frozenset({ToolCapability.COMPUTER_CONTROL})
    input_schema = tool_schema(
        {
            "reason": {
                "type": "string",
                "description": (
                    "What you are looking for. Shown to the user, who is "
                    "watching their own screen be captured."
                ),
            },
        },
    )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        reason = str(arguments.get("reason") or "").strip()
        # Capture is a blocking subprocess against the display server; off the
        # loop so a compositor taking its time cannot freeze the UI.
        encoded, raw_bytes = await asyncio.to_thread(capture_screen_base64)
        await context.event_bus.emit(
            "computer.action",
            {"action": "capture_screen", "reason": reason, "bytes": raw_bytes},
            source="tool.capture_screen",
        )
        return {
            "captured": True,
            "reason": reason,
            "bytes": raw_bytes,
            TOOL_IMAGES_KEY: [{"data": encoded, "media_type": "image/png"}],
        }
