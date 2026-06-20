from __future__ import annotations

from typing import Any

from code_ai.core.errors import ToolArgumentError
from code_ai.tools.base import ToolCapability, ToolContext


class FinishDiscoveryTool:
    name = "finish_discovery"
    description = "Request transition out of local discovery with a bounded evidence summary."
    capabilities = frozenset({ToolCapability.INTERNAL_TRANSITION})
    input_schema = {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "relevant_paths": {"type": "array", "items": {"type": "string"}},
            "observed_patterns": {"type": "array", "items": {"type": "string"}},
            "project_commands": {"type": "array", "items": {"type": "string"}},
            "unresolved_questions": {"type": "array", "items": {"type": "string"}},
            "external_knowledge_gaps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string"},
                        "why_local_files_are_insufficient": {"type": "string"},
                        "decision_depends_on": {"type": "string"},
                    },
                    "required": [
                        "question",
                        "why_local_files_are_insufficient",
                        "decision_depends_on",
                    ],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["summary"],
        "additionalProperties": False,
    }

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        summary = str(arguments.get("summary") or "").strip()
        if not summary:
            raise ToolArgumentError("summary is required.")
        relevant_paths = _string_list(arguments.get("relevant_paths"))
        for path in relevant_paths:
            context.workspace.resolve(path, must_exist=True)
        gaps = arguments.get("external_knowledge_gaps") or []
        if not isinstance(gaps, list):
            raise ToolArgumentError("external_knowledge_gaps must be a list.")
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
