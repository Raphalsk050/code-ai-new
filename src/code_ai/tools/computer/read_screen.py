"""Letting the agent look at the desktop the user is sitting in front of."""

from __future__ import annotations

import asyncio
from typing import Any

from code_ai.tools.base import TOOL_IMAGES_KEY, ToolCapability, ToolContext
from code_ai.tools.computer.capture import capture_screen
from code_ai.tools.computer.common import desktop_controller
from code_ai.tools.schema import tool_schema


class ReadDesktopScreenTool:
    """Screenshot the desktop, and say where its pixels actually are.

    Named apart from the terminal's ``read_screen``, which reads an emulated
    PTY: one answers "what did that command print", the other "what is on the
    user's monitor". Sharing a name would leave the model picking between them
    by luck.

    The geometry travels with the image because the image alone is not enough
    to click from. It has been shrunk to fit in a request, and the desktop it
    pictures may start at a negative x when a second monitor sits to the left
    of the primary one, so a coordinate read off it is wrong twice over. The
    numbers needed to correct that are reported, and the pointer tools will
    apply them on request, which is the form least likely to be got wrong.
    """

    name = "capture_screen"
    description = (
        "Take a screenshot of the desktop and look at it. Use it to see what is "
        "actually on the user's monitor: which window has focus, what a GUI is "
        "showing, whether a dialog is waiting, what an application rendered "
        "after a click. The image comes back attached, so it is seen rather "
        "than described, together with the screen's real size, the size the "
        "image was scaled to, and the pointer position. To click something you "
        "can see in the image, pass the coordinates you read off it to "
        "click_mouse with coordinate_space='image' - the scaling and the "
        "monitor offset are then applied for you, and you must not convert "
        "them yourself. For the output of a command in a terminal session use "
        "read_screen instead; this is the whole desktop, not a shell."
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
        controller = desktop_controller(context)
        # Capture is a blocking subprocess against the display server; off the
        # loop so a compositor taking its time cannot freeze the UI.
        encoded, raw_bytes, geometry = await asyncio.to_thread(capture_screen)
        # Remembered so a later click can be given in the image's own
        # coordinates: the model looks in one turn and clicks in the next.
        controller.last_capture = geometry

        payload: dict[str, Any] = {
            "captured": True,
            "reason": reason,
            "bytes": raw_bytes,
            **geometry.to_dict(),
            "clicking": (
                "Coordinates read off this image are image coordinates: pass "
                "them to click_mouse/move_mouse with coordinate_space='image' "
                "rather than converting them."
            ),
        }
        cursor = await self._cursor(controller)
        if cursor is not None:
            payload["cursor"] = cursor
        payload[TOOL_IMAGES_KEY] = [{"data": encoded, "media_type": "image/png"}]

        await context.event_bus.emit(
            "computer.action",
            {"action": "capture_screen", "reason": reason, "bytes": raw_bytes},
            source="tool.capture_screen",
        )
        return payload

    @staticmethod
    async def _cursor(controller: Any) -> dict[str, int] | None:
        """Where the pointer is, when there is a backend that can say.

        Absent rather than fatal: seeing the screen is useful on a machine with
        no pointer backend installed, and refusing the screenshot because the
        cursor cannot be located would trade the whole capability for a detail.
        """

        try:
            x, y = await controller.run(controller.cursor_position)
        except Exception:  # noqa: BLE001 - no backend, or no pointer
            return None
        return {"x": int(x), "y": int(y)}
