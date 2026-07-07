from __future__ import annotations

from pathlib import Path
from typing import Any

from code_ai.core.errors import ToolArgumentError, WorkspaceBoundaryError
from code_ai.tools.base import ToolCapability, ToolContext
from code_ai.tools.schema import tool_schema


class CompleteTaskTool:
    name = "complete_task"
    description = (
        "Request task completion after the runtime has recorded the required evidence."
    )
    capabilities = frozenset({ToolCapability.INTERNAL_COMPLETION})
    input_schema = tool_schema(
        {
            "summary": {
                "type": "string",
                "description": "Concise summary of what was accomplished this turn.",
            },
            "outcome": {
                "type": "string",
                "description": "One of 'success', 'blocked', or 'failed'. Defaults to 'success'.",
            },
            "remaining_issues": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "When the outcome is 'blocked' or 'failed': the concrete obstacles "
                    "that stopped completion (e.g. a command needs sudo, a path is "
                    "outside the workspace)."
                ),
            },
            "limitations": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "When the outcome is 'blocked' or 'failed': caveats or parts of the "
                    "task left undone that the user should know about."
                ),
            },
        },
        required=("summary",),
    )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        outcome = str(arguments.get("outcome") or "success").strip().lower()
        if outcome not in {"success", "blocked", "failed"}:
            raise ToolArgumentError("outcome must be success, blocked, or failed.")
        summary = str(arguments.get("summary") or "").strip()
        if not summary:
            raise ToolArgumentError("summary is required.")
        changed_paths = _string_list(arguments.get("changed_paths"))
        for path in changed_paths:
            try:
                context.workspace.resolve(path, must_exist=True)
            except WorkspaceBoundaryError:
                # A task may legitimately change files *outside* the workspace
                # (applied via commands, since file tools are workspace-bound).
                # Claiming such a path must not blow up the completion claim -
                # the planner's evidence gate arbitrates it - but the file must
                # really exist so the claim stays honest.
                candidate = Path(path).expanduser()
                if not (candidate.is_absolute() and candidate.exists()):
                    raise
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
