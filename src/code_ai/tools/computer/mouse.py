from __future__ import annotations

from typing import Any

from code_ai.tools.base import ToolCapability, ToolContext
from code_ai.tools.computer.common import desktop_controller, position_payload
from code_ai.tools.schema import tool_schema


class MoveMouseTool:
    name = "move_mouse"
    description = (
        "Move the mouse pointer to absolute screen coordinates (pixels from the "
        "top-left corner). Use screen_info first to learn the screen size."
    )
    capabilities = frozenset({ToolCapability.COMPUTER_CONTROL})
    input_schema = tool_schema(
        {
            "x": {"type": "integer", "description": "Target X coordinate in pixels."},
            "y": {"type": "integer", "description": "Target Y coordinate in pixels."},
            "duration": {
                "type": "number",
                "description": "Seconds to glide the pointer over. Defaults to 0 (instant).",
            },
        },
        required=("x", "y"),
    )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        controller = desktop_controller(context)
        duration = float(arguments.get("duration") or 0.0)
        x, y = await controller.run(
            controller.move_mouse, int(arguments["x"]), int(arguments["y"]), duration
        )
        return await position_payload(context, controller, "move_mouse", {"x": x, "y": y})


class ClickMouseTool:
    name = "click_mouse"
    description = (
        "Click the mouse. Optionally move to absolute coordinates first; omit x/y "
        "to click at the current pointer position. Supports double/triple clicks "
        "and left/middle/right buttons."
    )
    capabilities = frozenset({ToolCapability.COMPUTER_CONTROL})
    input_schema = tool_schema(
        {
            "x": {"type": "integer", "description": "X coordinate to click at (pixels)."},
            "y": {"type": "integer", "description": "Y coordinate to click at (pixels)."},
            "button": {
                "type": "string",
                "description": "Mouse button: 'left', 'middle', or 'right'. Defaults to left.",
            },
            "clicks": {
                "type": "integer",
                "description": "Number of clicks (2 = double, 3 = triple). Defaults to 1.",
            },
            "interval": {
                "type": "number",
                "description": "Seconds between repeated clicks. Defaults to 0.",
            },
        },
    )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        controller = desktop_controller(context)
        x = arguments.get("x")
        y = arguments.get("y")
        cx, cy = await controller.run(
            controller.click_mouse,
            int(x) if x is not None else None,
            int(y) if y is not None else None,
            arguments.get("button") or "left",
            int(arguments.get("clicks") or 1),
            float(arguments.get("interval") or 0.0),
        )
        return await position_payload(context, controller, "click_mouse", {"x": cx, "y": cy})


class DragMouseTool:
    name = "drag_mouse"
    description = (
        "Press and hold the mouse button, drag to a destination, and release. "
        "Omit start_x/start_y to drag from the current pointer position."
    )
    capabilities = frozenset({ToolCapability.COMPUTER_CONTROL})
    input_schema = tool_schema(
        {
            "start_x": {"type": "integer", "description": "Drag origin X (pixels)."},
            "start_y": {"type": "integer", "description": "Drag origin Y (pixels)."},
            "end_x": {"type": "integer", "description": "Drag destination X (pixels)."},
            "end_y": {"type": "integer", "description": "Drag destination Y (pixels)."},
            "button": {
                "type": "string",
                "description": (
                    "Mouse button to hold while dragging: 'left', 'middle', or "
                    "'right'. Defaults to left."
                ),
            },
            "duration": {
                "type": "number",
                "description": "Seconds the drag should take. Defaults to 0.3.",
            },
        },
        required=("end_x", "end_y"),
    )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        controller = desktop_controller(context)
        start_x = arguments.get("start_x")
        start_y = arguments.get("start_y")
        x, y = await controller.run(
            controller.drag_mouse,
            int(start_x) if start_x is not None else None,
            int(start_y) if start_y is not None else None,
            int(arguments["end_x"]),
            int(arguments["end_y"]),
            arguments.get("button") or "left",
            float(arguments.get("duration") or 0.3),
        )
        return await position_payload(context, controller, "drag_mouse", {"x": x, "y": y})


class ScrollMouseTool:
    name = "scroll_mouse"
    description = (
        "Scroll the mouse wheel. Positive amount scrolls up, negative scrolls down. "
        "Optionally move to x/y before scrolling."
    )
    capabilities = frozenset({ToolCapability.COMPUTER_CONTROL})
    input_schema = tool_schema(
        {
            "amount": {
                "type": "integer",
                "description": "Wheel clicks; positive scrolls up, negative down.",
            },
            "x": {"type": "integer", "description": "X to move to before scrolling (pixels)."},
            "y": {"type": "integer", "description": "Y to move to before scrolling (pixels)."},
        },
        required=("amount",),
    )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        controller = desktop_controller(context)
        x = arguments.get("x")
        y = arguments.get("y")
        cx, cy = await controller.run(
            controller.scroll_mouse,
            int(arguments["amount"]),
            int(x) if x is not None else None,
            int(y) if y is not None else None,
        )
        return await position_payload(context, controller, "scroll_mouse", {"x": cx, "y": cy})
