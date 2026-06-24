from __future__ import annotations

import difflib
import os
import tempfile
from dataclasses import dataclass
from typing import Any

from code_ai.core.errors import ToolArgumentError, ToolExecutionError
from code_ai.tools.base import ToolCapability, ToolContext
from code_ai.tools.filesystem.common import read_text_file, sha256_bytes
from code_ai.tools.output import bound_text
from code_ai.tools.schema import tool_schema


@dataclass(frozen=True, slots=True)
class _Replacement:
    start: int
    end: int
    new: str


class EditCodeTool:
    name = "edit_code"
    description = (
        "Apply all-or-nothing literal text replacements with SHA-256 guard and unified diff."
    )
    capabilities = frozenset({ToolCapability.LOCAL_WRITE})
    input_schema = tool_schema(
        {
            "path": {
                "type": "string",
                "description": "Workspace-relative path of the file to edit. Must already exist.",
            },
            "old_text": {
                "type": "string",
                "description": "Exact literal text to replace. Must match the file verbatim.",
            },
            "new_text": {
                "type": "string",
                "description": "Replacement text inserted in place of old_text.",
            },
            "expected_occurrences": {
                "type": "integer",
                "description": "Required match count for old_text; fails on mismatch. Default 1.",
            },
            "expected_sha256": {
                "type": "string",
                "description": "Optional current-file SHA-256; edit aborts on mismatch.",
            },
            "reason": {
                "type": "string",
                "description": (
                    "One or two plain-language sentences explaining why this edit is needed and "
                    "what it accomplishes. Shown to the user in the approval prompt before they "
                    "decide whether to allow it."
                ),
            },
        },
        required=("path", "old_text", "new_text"),
    )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        path_value = str(arguments.get("path", ""))
        edits = _coerce_edits(arguments)
        if not path_value:
            raise ToolArgumentError("path is required.")

        path = context.workspace.resolve(path_value, must_exist=True)
        original, old_hash = read_text_file(path)
        expected_hash = arguments.get("expected_sha256")
        if expected_hash and expected_hash != old_hash:
            raise ToolExecutionError("expected_sha256 does not match existing file.")

        replacements = self._build_replacements(original, edits)
        edited = self._apply(original, replacements)
        diff = "".join(
            difflib.unified_diff(
                original.splitlines(keepends=True),
                edited.splitlines(keepends=True),
                fromfile=str(path),
                tofile=str(path),
            )
        )

        data = edited.encode("utf-8")
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
            "changed": original != edited,
            "diff": bound_text(diff, context.config.budgets.max_tool_output_chars),
        }

    def _build_replacements(self, original: str, edits: list[Any]) -> list[_Replacement]:
        replacements: list[_Replacement] = []
        for index, edit in enumerate(edits):
            if not isinstance(edit, dict):
                raise ToolArgumentError(f"edit {index} must be an object.")
            old = edit.get("old")
            new = edit.get("new")
            if not isinstance(old, str) or not old:
                raise ToolArgumentError(f"edit {index} old text must be a non-empty string.")
            if not isinstance(new, str):
                raise ToolArgumentError(f"edit {index} new text must be a string.")
            expected = int(edit.get("expected_occurrences") or 1)
            spans = self._find_all(original, old)
            if not spans:
                raise ToolExecutionError(f"edit {index} old text was not found.")
            if len(spans) != expected:
                raise ToolExecutionError(
                    f"edit {index} expected {expected} occurrence(s), found {len(spans)}."
                )
            replacements.extend(_Replacement(start, end, new) for start, end in spans)

        replacements.sort(key=lambda item: item.start)
        for left, right in zip(replacements, replacements[1:], strict=False):
            if left.end > right.start:
                raise ToolExecutionError("Edit operations overlap.")
        return replacements

    @staticmethod
    def _find_all(text: str, needle: str) -> list[tuple[int, int]]:
        spans: list[tuple[int, int]] = []
        cursor = 0
        while True:
            index = text.find(needle, cursor)
            if index < 0:
                return spans
            spans.append((index, index + len(needle)))
            cursor = index + len(needle)

    @staticmethod
    def _apply(original: str, replacements: list[_Replacement]) -> str:
        parts: list[str] = []
        cursor = 0
        for replacement in replacements:
            parts.append(original[cursor : replacement.start])
            parts.append(replacement.new)
            cursor = replacement.end
        parts.append(original[cursor:])
        return "".join(parts)


def _coerce_edits(arguments: dict[str, Any]) -> list[Any]:
    legacy = arguments.get("edits")
    if legacy is not None:
        if not isinstance(legacy, list) or not legacy:
            raise ToolArgumentError("edits must be a non-empty list.")
        return legacy
    old_text = arguments.get("old_text")
    new_text = arguments.get("new_text")
    if not isinstance(old_text, str) or not old_text:
        raise ToolArgumentError("old_text is required.")
    if not isinstance(new_text, str):
        raise ToolArgumentError("new_text is required.")
    edit: dict[str, Any] = {"old": old_text, "new": new_text}
    if arguments.get("expected_occurrences") is not None:
        edit["expected_occurrences"] = arguments.get("expected_occurrences")
    return [edit]
