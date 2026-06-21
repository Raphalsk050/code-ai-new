from __future__ import annotations

from typing import Any

from code_ai.core.errors import ToolArgumentError
from code_ai.tools.base import ToolCapability, ToolContext
from code_ai.tools.schema import tool_schema


class FinishDiscoveryTool:
    name = "finish_discovery"
    description = "Request transition out of local discovery with a bounded evidence summary."
    capabilities = frozenset({ToolCapability.INTERNAL_TRANSITION})
    input_schema = tool_schema(
        {
            "summary": {
                "type": "string",
                "description": "Summary of what local discovery established before moving on.",
            },
        },
        required=("summary",),
    )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        summary = str(arguments.get("summary") or "").strip()
        if not summary:
            raise ToolArgumentError("summary is required.")
        relevant_paths = _string_list(arguments.get("relevant_paths"))
        for path in relevant_paths:
            context.workspace.resolve(path, must_exist=True)
        gaps = arguments.get("external_knowledge_gaps") or []
        if not isinstance(gaps, list):
            gaps = []
        return {
            "summary": summary,
            "relevant_paths": relevant_paths,
            "observed_patterns": _string_list(arguments.get("observed_patterns")),
            "project_commands": _string_list(arguments.get("project_commands")),
            "unresolved_questions": _string_list(arguments.get("unresolved_questions")),
            "external_knowledge_gaps": gaps,
        }


def _string_list(value: object) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ToolArgumentError("Expected a list of strings.")
    return [item for item in value if item]
