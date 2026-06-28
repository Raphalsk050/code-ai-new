from __future__ import annotations

from typing import Any

from code_ai.tools.base import ToolCapability, ToolContext
from code_ai.tools.computer.common import desktop_controller
from code_ai.tools.schema import tool_schema


class ScreenInfoTool:
    name = "screen_info"
    description = (
        "Report the primary screen size in pixels and the current mouse pointer "
        "position. Call this before moving or clicking to size coordinates correctly."
    )
    capabilities = frozenset({ToolCapability.COMPUTER_CONTROL})
    input_schema = tool_schema({})

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        controller = desktop_controller(context)
        width, height = await controller.run(controller.screen_size)
        cx, cy = await controller.run(controller.cursor_position)
        return {
            "screen": {"width": width, "height": height},
            "cursor": {"x": cx, "y": cy},
        }
