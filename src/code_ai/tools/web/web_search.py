from __future__ import annotations

import asyncio
from typing import Any

from code_ai.core.errors import ToolArgumentError, ToolExecutionError
from code_ai.core.internet_intent import requires_current_web_search
from code_ai.tools.base import ToolCapability, ToolContext
from code_ai.tools.web.backend import DDGSWebSearchBackend, WebSearchBackend, fetch_page_text


class WebSearchTool:
    name = "web_search"
    description = (
        "Search the public web for external current or unknown facts. Use for news, "
        "sports schedules, prices, recent releases, regulations, explicit web "
        "research, or host-approved external gaps."
    )
    capabilities = frozenset({ToolCapability.WEB})
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 10},
            "region": {"type": "string"},
            "time_filter": {"type": "string"},
            "timeout": {"type": "number", "minimum": 1},
            "fetch_top_n": {"type": "integer", "minimum": 0, "maximum": 3},
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
        fetch_top_n = _fetch_top_n(arguments, query)
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

        pages = []
        for result in results[:fetch_top_n]:
            try:
                page = await _fetch_page(result.url, timeout=timeout)
            except Exception as exc:
                pages.append({"url": result.url, "status": "error", "error": str(exc)})
                continue
            pages.append(page)

        return {
            "query": query,
            "results": [result.to_dict() for result in results[:max_results]],
            "pages": pages,
        }


async def _fetch_page(url: str, *, timeout: float) -> dict[str, str]:
    page = await asyncio.to_thread(fetch_page_text, url, timeout=timeout, max_chars=6000)
    data = page.to_dict()
    data["status"] = "ok"
    return data


def _fetch_top_n(arguments: dict[str, Any], query: str) -> int:
    if "fetch_top_n" in arguments:
        return max(0, min(3, int(arguments.get("fetch_top_n") or 0)))
    return 2 if requires_current_web_search(query) else 0
