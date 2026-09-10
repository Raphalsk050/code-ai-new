from __future__ import annotations

from typing import Any

from code_ai.core.errors import ToolArgumentError
from code_ai.tools.base import ToolCapability, ToolContext
from code_ai.tools.schema import tool_schema


class SubmitPlanTool:
    name = "submit_plan"
    description = (
        "Declare the concrete ordered steps you will follow for this task, and "
        "whether carrying them out changes the workspace. Call this once you know "
        "the real plan, before acting, and call it again only to revise the plan. "
        "The steps you submit are shown to the user as the task checklist. You "
        "decide what kind of task this is: set changes_workspace to true when the "
        "plan creates or edits files (or runs commands that do), and to false when "
        "the deliverable is an answer in the chat."
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
            "changes_workspace": {
                "type": "boolean",
                "description": (
                    "Your decision on the nature of this task: true if the plan "
                    "creates, edits or deletes files in the workspace (directly or "
                    "through commands), false if the user wants information and "
                    "your answer in the chat is the deliverable. The runtime "
                    "follows this declaration instead of guessing from the "
                    "request text."
                ),
            },
        },
        required=("steps",),
    )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        steps = _step_titles(arguments.get("steps"))
        if not steps:
            raise ToolArgumentError("steps must be a non-empty list of step descriptions.")
        result: dict[str, Any] = {"steps": steps}
        changes_workspace = _optional_bool(arguments.get("changes_workspace"))
        if changes_workspace is not None:
            result["changes_workspace"] = changes_workspace
        return result


def _optional_bool(value: object) -> bool | None:
    """Coerce the declaration, tolerating the string forms weak models emit."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "1"}:
            return True
        if lowered in {"false", "no", "0"}:
            return False
        raise ToolArgumentError("changes_workspace must be a boolean.")
    raise ToolArgumentError("changes_workspace must be a boolean.")


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
