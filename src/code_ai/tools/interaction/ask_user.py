from __future__ import annotations

from typing import Any

from code_ai.core.errors import ToolArgumentError
from code_ai.tools.base import ToolCapability, ToolContext


class AskUserTool:
    name = "ask_user"
    description = "Ask the user only for a blocking decision that cannot be inferred safely."
    capabilities = frozenset({ToolCapability.INTERACTION})
    input_schema = {
        "type": "object",
        "properties": {
            "question": {"type": "string"},
            "why_required": {"type": "string"},
            "choices": {"type": "array", "items": {"type": "string"}},
            "allow_free_form": {"type": "boolean"},
        },
        "required": ["question", "why_required"],
        "additionalProperties": False,
    }

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        question = str(arguments.get("question") or "").strip()
        why_required = str(arguments.get("why_required") or "").strip()
        if not question:
            raise ToolArgumentError("question is required.")
        if not why_required:
            raise ToolArgumentError("why_required is required.")
        choices = _string_list(arguments.get("choices"))
        await context.event_bus.emit(
            "interaction.question.requested",
            {
                "question": question,
                "why_required": why_required,
                "choices": choices,
                "allow_free_form": bool(arguments.get("allow_free_form", True)),
            },
            source="tool.ask_user",
        )
        return {
            "status": "blocked",
            "question": question,
            "why_required": why_required,
            "choices": choices,
            "message": "Interactive answer handling is pending; the turn is blocked.",
        }


def _string_list(value: object) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ToolArgumentError("choices must be a list of strings.")
    return [item for item in value if item]
