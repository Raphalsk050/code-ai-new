from __future__ import annotations

from typing import Any

from code_ai.core.errors import ToolArgumentError
from code_ai.tools.base import ToolCapability, ToolContext
from code_ai.tools.computer.common import desktop_controller
from code_ai.tools.schema import tool_schema


class TypeTextTool:
    name = "type_text"
    description = (
        "Type literal text into whatever application currently has keyboard focus, "
        "as if entered on the keyboard. Click the target field first to focus it."
    )
    capabilities = frozenset({ToolCapability.COMPUTER_CONTROL})
    input_schema = tool_schema(
        {
            "text": {"type": "string", "description": "The literal text to type."},
            "interval": {
                "type": "number",
                "description": "Seconds between keystrokes. Defaults to 0.",
            },
        },
        required=("text",),
    )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        controller = desktop_controller(context)
        text = str(arguments.get("text", ""))
        interval = float(arguments.get("interval") or 0.0)
        await controller.run(controller.type_text, text, interval)
        await context.event_bus.emit(
            "computer.action",
            {"action": "type_text", "length": len(text)},
            source="tool.type_text",
        )
        return {"action": "type_text", "typed_characters": len(text)}


class PressKeysTool:
    name = "press_keys"
    description = (
        "Press keyboard keys by name. With chord=true the keys are held together "
        "as a shortcut (e.g. ['command','c'] to copy, ['ctrl','alt','delete']). "
        "With chord=false each key is pressed in sequence (e.g. ['enter']). Use "
        "names like enter, tab, esc, space, up, down, left, right, command, ctrl, "
        "alt/option, shift, f1-f12, backspace, delete."
    )
    capabilities = frozenset({ToolCapability.COMPUTER_CONTROL})
    input_schema = tool_schema(
        {
            "keys": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Key names to press.",
            },
            "chord": {
                "type": "boolean",
                "description": "Hold keys simultaneously as a shortcut. Defaults to true.",
            },
        },
        required=("keys",),
    )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        controller = desktop_controller(context)
        keys = arguments.get("keys")
        if not isinstance(keys, list) or not keys or not all(isinstance(k, str) for k in keys):
            raise ToolArgumentError("keys must be a non-empty list of strings.")
        chord = arguments.get("chord")
        hold = True if chord is None else bool(chord)
        await controller.run(controller.press_keys, list(keys), hold)
        await context.event_bus.emit(
            "computer.action",
            {"action": "press_keys", "keys": list(keys), "chord": hold},
            source="tool.press_keys",
        )
        return {"action": "press_keys", "keys": list(keys), "chord": hold}
