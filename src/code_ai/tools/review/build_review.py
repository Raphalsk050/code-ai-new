from __future__ import annotations

from typing import Any

from code_ai.core.errors import ToolArgumentError
from code_ai.tools.base import ToolCapability, ToolContext
from code_ai.tools.review.prompts import BUILD_REVIEW_PROMPT
from code_ai.tools.schema import tool_schema


class BuildReviewTool:
    name = "build_review"
    description = "Run a one-shot build-output review with tools disabled."
    capabilities = frozenset({ToolCapability.PROCESS, ToolCapability.REVIEW})
    input_schema = tool_schema(
        {
            "content": {
                "type": "string",
                "description": "Build or test output to review.",
            },
        },
        required=("content",),
    )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        if context.review_service is None:
            raise ToolArgumentError("Review service is not configured.")
        result = await context.review_service.review(
            prompt=BUILD_REVIEW_PROMPT,
            content=str(arguments.get("content", "")),
            source=self.name,
        )
        return result.to_dict()
