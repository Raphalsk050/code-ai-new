from __future__ import annotations

from typing import Any

from code_ai.core.errors import ToolArgumentError
from code_ai.tools.base import ToolContext
from code_ai.tools.review.prompts import BUILD_REVIEW_PROMPT


class BuildReviewTool:
    name = "build_review"
    description = "Run a one-shot build-output review with tools disabled."
    input_schema = {
        "type": "object",
        "properties": {"content": {"type": "string"}},
        "required": ["content"],
        "additionalProperties": False,
    }

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        if context.review_service is None:
            raise ToolArgumentError("Review service is not configured.")
        result = await context.review_service.review(
            prompt=BUILD_REVIEW_PROMPT,
            content=str(arguments.get("content", "")),
            source=self.name,
        )
        return result.to_dict()
