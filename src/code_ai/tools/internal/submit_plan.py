from __future__ import annotations

from typing import Any

from code_ai.core.errors import ToolArgumentError
from code_ai.tools.base import ToolCapability, ToolContext
from code_ai.tools.schema import tool_schema


class SubmitPlanTool:
    name = "submit_plan"
    description = (
        "Declare the concrete ordered steps you will follow for this task. Call this "
        "once you know the real plan, before acting, and call it again only to revise "
        "the plan. The steps you submit are shown to the user as the task checklist."
    )
    capabilities = frozenset({ToolCapability.INTERNAL_TRANSITION})
    input_schema = tool_schema(
        {
            "steps": {
                "type": "array",
                "description": (
                    "Ordered, concrete steps you will actually take, each a short "
                    "imperative phrase (e.g. 'Read ROADMAP.md', 'Add the missing "
                    "section to data.py')."
                ),
                "items": {"type": "string"},
            },
        },
        required=("steps",),
    )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        steps = _step_titles(arguments.get("steps"))
        if not steps:
            raise ToolArgumentError("steps must be a non-empty list of step descriptions.")
        return {"steps": steps}


def _step_titles(value: object) -> list[str]:
    if not isinstance(value, list):
        raise ToolArgumentError("steps must be a list.")
    titles: list[str] = []
    for item in value:
        if isinstance(item, str):
            title = item.strip()
        elif isinstance(item, dict):
            raw = item.get("title") or item.get("step") or item.get("description")
            title = str(raw).strip() if raw is not None else ""
        else:
            raise ToolArgumentError("each step must be a string or an object with a title.")
        if title:
            titles.append(title)
    return titles
