from __future__ import annotations

from typing import Any

from code_ai.core.errors import ToolArgumentError
from code_ai.tools.base import ToolCapability, ToolContext
from code_ai.tools.review.prompts import DOCUMENTATION_PROMPT
from code_ai.tools.schema import tool_schema


class GenerateDocumentationTool:
    name = "generate_documentation"
    description = (
        "Generate clear, accurate, structured documentation (Markdown) for the supplied "
        "code or design context. Leads with purpose, documents only behavior that is "
        "actually present, and is ready to be saved to a file with write_file. "
        "Language-agnostic."
    )
    capabilities = frozenset({ToolCapability.REVIEW})
    input_schema = tool_schema(
        {
            "content": {
                "type": "string",
                "description": "Code, API surface, or design context to document.",
            },
            "audience": {
                "type": "string",
                "description": (
                    "Who the documentation is for (e.g. 'end users', 'maintainers', "
                    "'API consumers'). Defaults to a general technical reader."
                ),
            },
        },
        required=("content",),
    )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        if context.review_service is None:
            raise ToolArgumentError("Review service is not configured.")
        content = str(arguments.get("content", "")).strip()
        if not content:
            raise ToolArgumentError("content is required.")
        audience = arguments.get("audience")
        prompt = DOCUMENTATION_PROMPT
        if isinstance(audience, str) and audience.strip():
            prompt = f"{DOCUMENTATION_PROMPT}\nIntended audience: {audience.strip()}."
        result = await context.review_service.generate(
            prompt=prompt,
            content=content,
            source=self.name,
        )
        return {"documentation": result.text, "usage": result.to_dict()["usage"]}
