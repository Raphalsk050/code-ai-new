from __future__ import annotations

from typing import Any

from code_ai.core.errors import ToolArgumentError
from code_ai.tools.base import ToolCapability, ToolContext
from code_ai.tools.review.prompts import CODE_REVIEW_PROMPT
from code_ai.tools.schema import tool_schema


class CodeReviewTool:
    name = "code_review"
    description = "Run a one-shot code review with tools disabled."
    capabilities = frozenset({ToolCapability.REVIEW})
    input_schema = tool_schema(
        {
            "content": {
                "type": "string",
                "description": "Code or diff to review.",
            },
        },
        required=("content",),
    )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        if context.review_service is None:
            raise ToolArgumentError("Review service is not configured.")
        result = await context.review_service.review(
            prompt=CODE_REVIEW_PROMPT,
            content=str(arguments.get("content", "")),
            source=self.name,
            # The caller acts on this review directly, so precision matters more
            # than the extra call: a finding that cannot be defended costs more
            # to chase down than it ever saved.
            refute=True,
        )
        return result.to_dict()
