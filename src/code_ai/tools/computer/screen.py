from __future__ import annotations

import time
from typing import Any

from code_ai.core.errors import ToolArgumentError
from code_ai.tools.base import ToolCapability, ToolContext
from code_ai.tools.computer.common import desktop_controller
from code_ai.tools.schema import tool_schema

_SCREENSHOT_DIR = ".code-ai/screenshots"


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


class ScreenshotTool:
    name = "take_screenshot"
    description = (
        "Capture the screen to a PNG file inside the workspace and return its path "
        "and dimensions. Optionally capture only a rectangular region. Use this to "
        "see the current state of the desktop before acting."
    )
    capabilities = frozenset({ToolCapability.COMPUTER_CONTROL, ToolCapability.LOCAL_WRITE})
    input_schema = tool_schema(
        {
            "path": {
                "type": "string",
                "description": (
                    "Workspace-relative PNG path to save to. Defaults to a timestamped "
                    "file under .code-ai/screenshots/."
                ),
            },
            "region": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "Optional [left, top, width, height] region in pixels.",
            },
        },
    )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        controller = desktop_controller(context)
        requested = arguments.get("path")
        if requested:
            target = context.workspace.resolve(str(requested))
        else:
            name = f"shot-{time.strftime('%Y%m%d-%H%M%S')}.png"
            target = context.workspace.resolve(f"{_SCREENSHOT_DIR}/{name}")
        target.parent.mkdir(parents=True, exist_ok=True)

        region = self._region(arguments.get("region"))
        width, height = await controller.run(controller.screenshot, target, region)

        try:
            relative = str(target.relative_to(context.workspace.root))
        except ValueError:
            relative = str(target)
        payload = {
            "path": relative,
            "width": width,
            "height": height,
            "region": list(region) if region else None,
        }
        await context.event_bus.emit(
            "computer.action",
            {"action": "take_screenshot", "path": relative},
            source="tool.take_screenshot",
        )
        return payload

    @staticmethod
    def _region(raw: Any) -> tuple[int, int, int, int] | None:
        if raw is None:
            return None
        if not isinstance(raw, list) or len(raw) != 4:
            raise ToolArgumentError("region must be [left, top, width, height].")
        try:
            left, top, width, height = (int(value) for value in raw)
        except (TypeError, ValueError):
            raise ToolArgumentError("region values must be integers.") from None
        if width <= 0 or height <= 0:
            raise ToolArgumentError("region width and height must be positive.")
        return (left, top, width, height)
