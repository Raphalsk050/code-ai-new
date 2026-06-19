from __future__ import annotations

import os
import tempfile
from typing import Any

from code_ai.core.errors import ToolArgumentError, ToolExecutionError
from code_ai.tools.base import ToolContext
from code_ai.tools.filesystem.common import sha256_bytes, sha256_file


class WriteFileTool:
    name = "write_file"
    description = (
        "Atomically write a UTF-8 text file inside the workspace with optional hash guards."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
            "expected_sha256": {"type": "string"},
            "expected_new_file": {"type": "boolean"},
            "create_dirs": {"type": "boolean"},
        },
        "required": ["path", "content"],
        "additionalProperties": False,
    }

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        path_value = str(arguments.get("path", ""))
        if not path_value:
            raise ToolArgumentError("path is required.")
        content = str(arguments.get("content", ""))
        path = context.workspace.resolve(path_value, must_exist=False)
        expected_new = bool(arguments.get("expected_new_file", False))
        expected_hash = arguments.get("expected_sha256")
        create_dirs = bool(arguments.get("create_dirs", True))

        old_hash: str | None = None
        if path.exists():
            if expected_new:
                raise ToolExecutionError(f"File already exists: {path_value}")
            old_hash = sha256_file(path)
            if expected_hash and old_hash != expected_hash:
                raise ToolExecutionError("expected_sha256 does not match existing file.")
        elif expected_hash:
            raise ToolExecutionError("expected_sha256 was provided but file does not exist.")

        if create_dirs:
            path.parent.mkdir(parents=True, exist_ok=True)
        data = content.encode("utf-8")
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
        )
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
        except Exception:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
            raise

        return {
            "path": str(path.relative_to(context.workspace.root)),
            "old_sha256": old_hash,
            "new_sha256": sha256_bytes(data),
            "bytes_written": len(data),
        }
