from __future__ import annotations

from typing import Any

from code_ai.core.errors import ToolArgumentError
from code_ai.tools.base import ToolCapability, ToolContext
from code_ai.tools.computer.common import desktop_controller
from code_ai.tools.schema import tool_schema


class OpenApplicationTool:
    name = "open_application"
    description = (
        "Launch a desktop application by name (e.g. 'Safari', 'Notes', 'Calculator') "
        "or by absolute path. On macOS this uses `open`. Set background=true to launch "
        "without bringing it to the front."
    )
    capabilities = frozenset({ToolCapability.COMPUTER_CONTROL})
    input_schema = tool_schema(
        {
            "name": {
                "type": "string",
                "description": "Application name, bundle path, or file/URL to open with it.",
            },
            "background": {
                "type": "boolean",
                "description": "Launch without foregrounding the app. Defaults to false.",
            },
        },
        required=("name",),
    )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        controller = desktop_controller(context)
        name = str(arguments.get("name") or "").strip()
        if not name:
            raise ToolArgumentError("name is required.")
        background = bool(arguments.get("background"))
        result = await controller.run(controller.open_application, name, background=background)
        await context.event_bus.emit(
            "computer.action",
            {"action": "open_application", **result},
            source="tool.open_application",
        )
        return result


class ActivateApplicationTool:
    name = "activate_application"
    description = (
        "Bring an already-running application to the foreground so it receives "
        "keyboard and mouse input. macOS only for true activation; elsewhere it "
        "falls back to launching the app."
    )
    capabilities = frozenset({ToolCapability.COMPUTER_CONTROL})
    input_schema = tool_schema(
        {
            "name": {"type": "string", "description": "Application name to activate."},
        },
        required=("name",),
    )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        controller = desktop_controller(context)
        name = str(arguments.get("name") or "").strip()
        if not name:
            raise ToolArgumentError("name is required.")
        result = await controller.run(controller.activate_application, name)
        await context.event_bus.emit(
            "computer.action",
            {"action": "activate_application", **result},
            source="tool.activate_application",
        )
        return result


class ListApplicationsTool:
    name = "list_applications"
    description = (
        "List the names of applications with visible windows (the ones currently "
        "open on screen). macOS only."
    )
    capabilities = frozenset({ToolCapability.COMPUTER_CONTROL, ToolCapability.LOCAL_READ})
    input_schema = tool_schema({})

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        controller = desktop_controller(context)
        applications = await controller.run(controller.list_applications)
        return {"applications": applications, "count": len(applications)}
