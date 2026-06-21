from __future__ import annotations

from typing import Any

from code_ai.core.errors import ToolArgumentError
from code_ai.tools.base import ToolCapability, ToolContext
from code_ai.tools.schema import tool_schema


class RequestExternalGapTool:
    name = "request_external_gap"
    description = "Request web access when local workspace evidence is insufficient."
    capabilities = frozenset({ToolCapability.INTERNAL_TRANSITION})
    input_schema = tool_schema(
        {
            "question": {
                "type": "string",
                "description": "The external question that local workspace evidence cannot answer.",
            },
            "reason": {
                "type": "string",
                "description": "Why local files are insufficient to answer the question.",
            },
        },
        required=("question", "reason"),
    )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        question = str(arguments.get("question") or "").strip()
        reason = str(arguments.get("reason") or "").strip()
        if not question:
            raise ToolArgumentError("question is required.")
        if not reason:
            raise ToolArgumentError("reason is required.")
        return {
            "summary": f"External information requested: {question}",
            "external_knowledge_gaps": [
                {
                    "question": question,
                    "why_local_files_are_insufficient": reason,
                    "decision_depends_on": reason,
                }
            ],
        }
