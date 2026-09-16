from __future__ import annotations

from typing import Any

from code_ai.core.errors import ToolExecutionError
from code_ai.tools.base import ToolCapability, ToolContext
from code_ai.tools.output import bound_text
from code_ai.tools.schema import tool_schema

_MAX_ERRORS = 5
_MAX_ERROR_CHARS = 300


def _bound_errors(errors: list[str]) -> list[str]:
    shown = [bound_text(error, _MAX_ERROR_CHARS) for error in errors[:_MAX_ERRORS]]
    if len(errors) > len(shown):
        shown.append(f"... and {len(errors) - len(shown)} more error(s)")
    return shown


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
        payload = report.to_dict()
        # A refresh of a large tree can fail on thousands of files (a mounted
        # drive, a permission boundary), and a list that long would be the
        # context blow-up the index exists to prevent. The model needs the
        # shape of the failure, not the roll call.
        payload["errors"] = _bound_errors(payload["errors"])
        return {
            "path": subtree or ".",
            "summary": report.summary(),
            **payload,
            "index": {
                "files": status.files,
                "chunks": status.chunks,
                "embedding_model": status.embedding_model,
                "embedded_chunks": status.embedded_chunks,
            },
        }
