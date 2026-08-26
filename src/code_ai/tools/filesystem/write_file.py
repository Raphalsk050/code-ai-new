from __future__ import annotations

import asyncio
from typing import Any

from code_ai.core.errors import ToolArgumentError, ToolExecutionError
from code_ai.tools.base import ToolCapability, ToolContext
from code_ai.tools.filesystem.common import sha256_bytes, sha256_file
from code_ai.tools.locations import LOCATION_SCHEMA, for_context
from code_ai.tools.schema import tool_schema
from code_ai.util.fileio import RetryPolicy, atomic_write_bytes


class WriteFileTool:
    name = "write_file"
    description = (
        "Atomically write a UTF-8 text file with optional hash guards. Writes into the "
        "workspace by default; pass location 'sandbox' for anything the project should "
        "not keep - generated scripts, throwaway experiments, scratch data."
    )
    capabilities = frozenset({ToolCapability.LOCAL_WRITE})
    input_schema = tool_schema(
        {
            "path": {
                "type": "string",
                "description": "Path of the file to write, relative to the chosen location.",
            },
            "location": LOCATION_SCHEMA,
            # Declared before the contents on purpose: arguments stream in the
            # order they are declared, so putting the justification first means
            # the user reads why the file is being written while it is still
            # being written, instead of after the fact.
            "reason": {
                "type": "string",
                "description": (
                    "One or two plain-language sentences explaining why this file is being "
                    "created/overwritten and what it accomplishes. Shown to the user while "
                    "the file streams in, and in the approval prompt before they decide "
                    "whether to allow it."
                ),
            },
            "content": {
                "type": "string",
                "description": "Full UTF-8 contents to write to the file.",
            },
        },
        required=("path", "content"),
    )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        path_value = str(arguments.get("path", ""))
        if not path_value:
            raise ToolArgumentError("path is required.")
        content = str(arguments.get("content", ""))
        location = for_context(context, arguments.get("location"))
        path = location.resolve(path_value, must_exist=False)
        expected_new = bool(arguments.get("expected_new_file", False))
        expected_hash = arguments.get("expected_sha256")
        create_dirs = bool(arguments.get("create_dirs", True))

        policy = RetryPolicy.from_config(context.config.file_io)

        old_hash: str | None = None
        if path.exists():
            if expected_new:
                raise ToolExecutionError(f"File already exists: {path_value}")
            old_hash = sha256_file(path, policy=policy)
            if expected_hash and old_hash != expected_hash:
                raise ToolExecutionError("expected_sha256 does not match existing file.")
        elif expected_hash:
            raise ToolExecutionError("expected_sha256 was provided but file does not exist.")

        data = content.encode("utf-8")
        # Off the event loop: the write retries a locked file for up to a
        # second or two on Windows, and a slow network share can take longer
        # still. Neither should freeze the UI.
        outcome = await asyncio.to_thread(
            atomic_write_bytes,
            path,
            data,
            policy=policy,
            allow_non_atomic_fallback=context.config.file_io.allow_non_atomic_fallback,
            create_parents=create_dirs,
        )

        return {
            "path": location.relative(path),
            "location": location.location.value,
            "old_sha256": old_hash,
            "new_sha256": sha256_bytes(data),
            "bytes_written": len(data),
            **outcome.to_dict(),
        }
