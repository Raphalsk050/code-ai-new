from __future__ import annotations

from typing import Any

from code_ai.core.errors import ToolArgumentError
from code_ai.tools.base import ToolCapability, ToolContext
from code_ai.tools.review.prompts import TEST_REVIEW_PROMPT
from code_ai.tools.schema import tool_schema


class TestReviewTool:
    name = "test_review"
    description = (
        "Review test cases for whether they are well constructed: single intent, no "
        "unnecessary steps, deterministic and isolated, meaningful assertions, and good "
        "coverage of edge cases and failures. Pays special attention to device/hardware "
        "tests (states, permissions, connectivity, configurations, cleanup). "
        "Language-agnostic."
    )
    capabilities = frozenset({ToolCapability.REVIEW})
    input_schema = tool_schema(
        {
            "content": {
                "type": "string",
                "description": "Test code (and any relevant code under test) to review.",
            },
        },
        required=("content",),
    )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        if context.review_service is None:
            raise ToolArgumentError("Review service is not configured.")
        result = await context.review_service.review(
            prompt=TEST_REVIEW_PROMPT,
            content=str(arguments.get("content", "")),
            source=self.name,
        )
        return result.to_dict()
