from __future__ import annotations

from typing import Any

from code_ai.core.errors import ToolArgumentError
from code_ai.tools.base import ToolCapability, ToolContext
from code_ai.tools.schema import tool_schema


class AskUserTool:
    name = "ask_user"
    description = (
        "Ask the user only for a blocking decision that cannot be inferred safely. "
        "Calling this ends the current turn; the user's next message is their answer."
    )
    capabilities = frozenset({ToolCapability.INTERACTION})
    input_schema = tool_schema(
        {
            "question": {
                "type": "string",
                "description": "The single blocking question to put to the user.",
            },
        },
        required=("question",),
    )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        question = str(arguments.get("question") or "").strip()
        why_required = str(arguments.get("why_required") or "").strip()
        if not question:
            raise ToolArgumentError("question is required.")
        if not why_required:
            why_required = "The task is blocked on a user decision."
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
        # The orchestrator ends the turn when it sees this result, so the model
        # never reads this message mid-turn; it reads it on the *next* turn as
        # history, where it must explain that the question already went out.
        return {
            "status": "blocked",
            "question": question,
            "why_required": why_required,
            "choices": choices,
            "message": (
                "Question delivered to the user; this turn ends now. "
                "The user's next message is their reply."
            ),
        }


def _string_list(value: object) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ToolArgumentError("choices must be a list of strings.")
    return [item for item in value if item]
