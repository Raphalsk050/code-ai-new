from __future__ import annotations

from typing import Any

from code_ai.core.errors import ToolArgumentError
from code_ai.tools.base import ToolCapability, ToolContext


class CompleteTaskTool:
    name = "complete_task"
    description = (
        "Request task completion with acceptance criteria mapped to recorded evidence. "
        "The runtime validates all claims before accepting completion."
    )
    capabilities = frozenset({ToolCapability.INTERNAL_COMPLETION})
    input_schema = {
        "type": "object",
        "properties": {
            "outcome": {"type": "string", "enum": ["success", "blocked", "failed"]},
            "summary": {"type": "string"},
            "acceptance_evidence": {
                "type": "object",
                "additionalProperties": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "verification_summary": {"type": "string"},
            "changed_paths": {"type": "array", "items": {"type": "string"}},
            "remaining_issues": {"type": "array", "items": {"type": "string"}},
            "limitations": {"type": "array", "items": {"type": "string"}},
            "double_check_acknowledged": {"type": "boolean"},
        },
        "required": ["outcome", "summary"],
        "additionalProperties": False,
    }

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        outcome = str(arguments.get("outcome") or "").strip().lower()
        if outcome not in {"success", "blocked", "failed"}:
            raise ToolArgumentError("outcome must be success, blocked, or failed.")
        summary = str(arguments.get("summary") or "").strip()
        if not summary:
            raise ToolArgumentError("summary is required.")
        changed_paths = _string_list(arguments.get("changed_paths"))
        for path in changed_paths:
            context.workspace.resolve(path, must_exist=True)
        return {
            "outcome": outcome,
            "summary": summary,
            "acceptance_evidence": arguments.get("acceptance_evidence") or {},
            "verification_summary": str(arguments.get("verification_summary") or ""),
            "changed_paths": changed_paths,
            "remaining_issues": _string_list(arguments.get("remaining_issues")),
            "limitations": _string_list(arguments.get("limitations")),
            "double_check_acknowledged": bool(
                arguments.get("double_check_acknowledged", False)
            ),
        }


def _string_list(value: object) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ToolArgumentError("Expected a list of strings.")
    return [item for item in value if item]
