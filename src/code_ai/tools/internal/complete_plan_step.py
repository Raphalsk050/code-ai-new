from __future__ import annotations

from typing import Any

from code_ai.tools.base import ToolCapability, ToolContext
from code_ai.tools.schema import tool_schema


class CompletePlanStepTool:
    name = "complete_plan_step"
    description = (
        "Mark the current step of your submitted checklist as done and move the live "
        "task checklist to the next step. Call this the moment you actually finish a "
        "checklist step - include it in the same tool batch as the step's final "
        "action, so the user sees progress immediately. Never postpone marking or "
        "save several calls for the end of the task, and do not call it to skip "
        "work you have not done."
    )
    capabilities = frozenset({ToolCapability.INTERNAL_TRANSITION})
    input_schema = tool_schema(
        {
            "completed_step": {
                "type": "string",
                "description": (
                    "The exact title of the checklist step you just finished (e.g. "
                    "'Implement LLM adapter'). Used to sync the checklist cursor to "
                    "that step, so always pass it."
                ),
            },
        },
    )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        completed = str(arguments.get("completed_step") or "").strip()
        return {"completed_step": completed}
