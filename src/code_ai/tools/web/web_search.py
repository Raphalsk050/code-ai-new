from __future__ import annotations

from typing import Any

from code_ai.core.errors import ToolArgumentError, ToolExecutionError
from code_ai.tools.base import ToolContext
from code_ai.tools.web.backend import DDGSWebSearchBackend, WebSearchBackend


class WebSearchTool:
    name = "web_search"
    description = "Perform a bounded provider-independent web search."
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 10},
            "region": {"type": "string"},
            "time_filter": {"type": "string"},
            "timeout": {"type": "number", "minimum": 1},
        },
        "required": ["query"],
        "additionalProperties": False,
    }

    def __init__(self, backend: WebSearchBackend | None = None) -> None:
        self._backend = backend or DDGSWebSearchBackend()

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        query = str(arguments.get("query", "")).strip()
        if not query:
            raise ToolArgumentError("query is required.")
        max_results = max(1, min(10, int(arguments.get("max_results") or 5)))
        timeout = min(
            float(arguments.get("timeout") or 10), context.config.budgets.default_tool_timeout_s
        )
        try:
            results = await self._backend.search(
                query,
                max_results=max_results,
                region=arguments.get("region"),
                time_filter=arguments.get("time_filter"),
                timeout=timeout,
            )
            if not results:
                raise ToolExecutionError("web_search returned no usable results.")
        except Exception as exc:
            if isinstance(exc, ToolExecutionError):
                raise
            raise ToolExecutionError(f"web_search failed: {exc}") from exc
        return {"query": query, "results": [result.to_dict() for result in results[:max_results]]}
