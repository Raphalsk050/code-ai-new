from __future__ import annotations

from typing import Any

from code_ai.core.errors import ToolExecutionError
from code_ai.tools.base import ToolCapability, ToolContext
from code_ai.tools.schema import tool_schema


class IndexWorkspaceTool:
    name = "index_workspace"
    description = (
        "Build or refresh the code index that search_index reads. Incremental by "
        "default: only files that changed since the last run are re-read, so "
        "calling it again is cheap. Call it once at the start of a task in a "
        "workspace whose index is empty or stale, or after a large batch of "
        "changes; files you read, write or edit through tools are re-indexed "
        "automatically. The index lives outside the workspace and never touches "
        "the project's files."
    )
    capabilities = frozenset({ToolCapability.LOCAL_READ})
    input_schema = tool_schema(
        {
            "path": {
                "type": "string",
                "description": (
                    "Workspace-relative directory to refresh. Defaults to the whole workspace."
                ),
            },
            "full": {
                "type": "boolean",
                "description": (
                    "Rebuild from scratch instead of refreshing incrementally. Only "
                    "needed after changing chunking or embedding settings."
                ),
            },
        },
    )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        index = context.code_index
        if index is None:
            raise ToolExecutionError("The code index is disabled (config: index.enabled).")
        subtree = str(arguments.get("path") or "").strip()
        if subtree and subtree != ".":
            resolved = context.workspace.resolve(subtree, must_exist=True)
            subtree = resolved.relative_to(context.workspace.root).as_posix()
            if subtree == ".":
                subtree = ""
        else:
            subtree = ""
        full = bool(arguments.get("full", False))
        report = await index.refresh(full=full, subtree=subtree, cancel_event=context.cancel_event)
        status = index.status()
        return {
            "path": subtree or ".",
            "summary": report.summary(),
            **report.to_dict(),
            "index": {
                "files": status.files,
                "chunks": status.chunks,
                "embedding_model": status.embedding_model,
                "embedded_chunks": status.embedded_chunks,
            },
        }
