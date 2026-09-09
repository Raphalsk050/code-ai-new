from __future__ import annotations

import json
from typing import Any

from code_ai.core.errors import ToolArgumentError, ToolExecutionError
from code_ai.tools.base import ToolCapability, ToolContext
from code_ai.tools.schema import tool_schema

EMPTY_INDEX_HINT = (
    "The code index is empty. Call index_workspace once to build it (the user can "
    "also run /index), or fall back to search_code for an exact text match."
)


# How much of a turn's tool-output budget a single search may spend, and the
# per-hit bounds inside it. A snippet under _MIN_SNIPPET is too small to judge
# a hit from, so a very tight budget returns fewer hits rather than unreadable
# ones.
_BUDGET_SHARE = 0.75
_MIN_BUDGET = 2000
_MIN_SNIPPET = 400
_MAX_SNIPPET = 2000


def _pack(hits: list[Any], budget: int) -> tuple[list[dict[str, Any]], int]:
    """Serialise hits in rank order until the budget is spent.

    Each hit is charged for its whole serialised size, not just its snippet:
    twenty hits' worth of path, line range, symbol and score is itself
    kilobytes, and it is the total the model pays for in context.
    """

    packed: list[dict[str, Any]] = []
    spent = 0
    for position, hit in enumerate(hits):
        item = hit.to_dict()
        cost = len(json.dumps(item))
        # The best hit is always returned, even if it alone exceeds the budget:
        # an empty answer would just send the model back to reading files.
        if packed and spent + cost > budget:
            return packed, len(hits) - position
        packed.append(item)
        spent += cost
    return packed, 0


class SearchIndexTool:
    name = "search_index"
    description = (
        "Fast ranked retrieval over the indexed workspace: finds the functions, "
        "classes and blocks most relevant to a natural-language question or a set "
        "of identifiers ('where are tool calls approved', 'retry backoff subagent'). "
        "Answers in milliseconds from a local index instead of scanning the tree, "
        "and understands split identifiers (ToolCall matches 'tool call'). Prefer "
        "it to locate code by meaning or by symbol name; use search_code when you "
        "need every exact occurrence of a literal string or a regex. Each hit "
        "carries the chunk text with its path and line range, so read_file is only "
        "needed for surrounding context."
    )
    capabilities = frozenset({ToolCapability.LOCAL_READ})
    input_schema = tool_schema(
        {
            "query": {
                "type": "string",
                "description": (
                    "What to look for: a question, a description of behaviour, or "
                    "identifiers separated by spaces."
                ),
            },
            "path": {
                "type": "string",
                "description": (
                    "Workspace-relative directory to restrict results to. Defaults to "
                    "the whole workspace."
                ),
            },
            "max_results": {
                "type": "integer",
                "description": "How many chunks to return (1-25). Defaults to 8.",
            },
        },
        required=("query",),
    )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        query = str(arguments.get("query") or "").strip()
        if not query:
            raise ToolArgumentError("query is required.")
        index = context.code_index
        if index is None:
            raise ToolExecutionError(
                "The code index is disabled (config: index.enabled). Use search_code instead."
            )
        prefix = str(arguments.get("path") or "").strip()
        if prefix and prefix != ".":
            # Resolving validates the path stays inside the workspace.
            resolved = context.workspace.resolve(prefix, must_exist=True)
            prefix = resolved.relative_to(context.workspace.root).as_posix()
            if prefix == ".":
                prefix = ""
        else:
            prefix = ""
        limit = max(1, min(25, int(arguments.get("max_results") or 8)))
        # One search must not be able to spend the whole turn's context. The
        # answer is budgeted as a whole rather than per hit: asking for 25
        # results at a fixed snippet size would return three times the tool
        # output budget, so instead a fixed allowance is shared out in rank
        # order and the tail is dropped once it runs out.
        budget = max(_MIN_BUDGET, int(context.config.budgets.max_tool_output_chars * _BUDGET_SHARE))
        snippet_chars = max(_MIN_SNIPPET, min(_MAX_SNIPPET, budget // limit))

        status = index.status()
        hits = await index.search(
            query, limit=limit, path_prefix=prefix, snippet_chars=snippet_chars
        )
        payload, dropped = _pack(hits, budget)
        result: dict[str, Any] = {
            "query": query,
            "path": prefix or ".",
            "hits": payload,
            "index": {
                "files": status.files,
                "chunks": status.chunks,
                "semantic": status.semantic_ready,
                "last_refresh": status.last_refresh,
            },
        }
        if dropped:
            result["truncated"] = (
                f"{dropped} lower-ranked hit(s) omitted to stay inside the output "
                "budget; narrow the query or set path to see more."
            )
        if status.empty:
            result["hint"] = EMPTY_INDEX_HINT
        elif status.last_error:
            result["warning"] = status.last_error
        return result
