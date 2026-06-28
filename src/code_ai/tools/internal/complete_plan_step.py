from __future__ import annotations

from typing import Any

from code_ai.tools.base import ToolCapability, ToolContext
from code_ai.tools.schema import tool_schema


class CompletePlanStepTool:
    name = "complete_plan_step"
    description = (
        "Mark the current step of your submitted checklist as done and move the live "
        "task checklist to the next step. Call this each time you actually finish one "
        "checklist step, so the sidebar and runtime track your real progress instead "
        "of guessing. Do not call it to skip work you have not done."
    )
    capabilities = frozenset({ToolCapability.INTERNAL_TRANSITION})
    input_schema = tool_schema(
        {
            "completed_step": {
                "type": "string",
                "description": (
                    "The checklist step you just finished, for the record (e.g. "
                    "'Implement LLM adapter'). Optional but helps keep the log clear."
                ),
            },
        },
    )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        completed = str(arguments.get("completed_step") or "").strip()
        return {"completed_step": completed}
