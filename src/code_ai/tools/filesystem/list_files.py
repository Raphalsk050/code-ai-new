from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Any

from code_ai.core.errors import ToolArgumentError
from code_ai.tools.base import ToolCapability, ToolContext
from code_ai.tools.locations import LOCATION_SCHEMA, for_context
from code_ai.tools.schema import tool_schema

DEFAULT_EXCLUDES = {
    ".cache",
    ".git",
    ".mypy_cache",
    ".nox",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "cmake-build-debug",
    "cmake-build-release",
    "dist",
    "node_modules",
    "out",
    "target",
    "venv",
}


class ListFilesTool:
    name = "list_files"
    description = (
        "List bounded files and directories with deterministic ordering. Lists the "
        "workspace by default; pass location 'sandbox' to inspect what this session "
        "built or captured."
    )
    capabilities = frozenset({ToolCapability.LOCAL_READ})
    input_schema = tool_schema(
        {
            "path": {
                "type": "string",
                "description": (
                    "Directory to list, relative to the chosen location. Defaults to its root."
                ),
            },
            "location": LOCATION_SCHEMA,
        },
    )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        location = for_context(context, arguments.get("location"))
        root = location.resolve(str(arguments.get("path") or "."), must_exist=True)
        if not root.is_dir():
            raise ToolArgumentError("path must be a directory.")
        max_depth = max(0, min(20, int(arguments.get("max_depth") or 2)))
        max_entries = max(1, min(5000, int(arguments.get("max_entries") or 200)))
        include_hidden = bool(arguments.get("include_hidden", False))
        include_sizes = bool(arguments.get("include_sizes", False))
        use_default_excludes = bool(arguments.get("use_default_excludes", True))
        include_globs = _string_list(arguments.get("include_globs"))
        exclude_globs = _string_list(arguments.get("exclude_globs"))

        entries: list[dict[str, Any]] = []
        skipped_count = 0
        truncated = False

        def walk(directory: Path, depth: int) -> None:
            nonlocal skipped_count, truncated
            if truncated:
                return
            try:
                children = sorted(directory.iterdir(), key=lambda item: item.name.casefold())
            except OSError:
                skipped_count += 1
                return
            for child in children:
                if len(entries) >= max_entries:
                    truncated = True
                    return
                relative = child.relative_to(location.root).as_posix()
                if _should_skip(
                    child,
                    relative=relative,
                    include_hidden=include_hidden,
                    include_globs=include_globs,
                    exclude_globs=exclude_globs,
                    use_default_excludes=use_default_excludes,
                ):
                    skipped_count += 1
                    continue
                if child.is_symlink() and not _symlink_stays_inside(child, location.root):
                    skipped_count += 1
                    continue
                entry = _entry(child, relative=relative, include_size=include_sizes)
                entries.append(entry)
                if entry["type"] == "directory" and depth < max_depth and not child.is_symlink():
                    walk(child, depth + 1)

        walk(root, 0)
        return {
            "path": root.relative_to(location.root).as_posix()
            if root != location.root
            else ".",
            "max_depth": max_depth,
            "max_entries": max_entries,
            "entries": entries,
            "truncated": truncated,
            "skipped_count": skipped_count,
        }


def _entry(path: Path, *, relative: str, include_size: bool) -> dict[str, Any]:
    if path.is_symlink():
        entry_type = "symlink"
    elif path.is_dir():
        entry_type = "directory"
    else:
        entry_type = "file"
    data: dict[str, Any] = {
        "path": relative,
        "type": entry_type,
    }
    if include_size and path.is_file() and not path.is_symlink():
        data["size_bytes"] = path.stat().st_size
    return data


def _string_list(value: object) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ToolArgumentError("glob lists must contain only strings.")
    return [item for item in value if item]


def _should_skip(
    path: Path,
    *,
    relative: str,
    include_hidden: bool,
    include_globs: list[str],
    exclude_globs: list[str],
    use_default_excludes: bool,
) -> bool:
    if not include_hidden and any(part.startswith(".") for part in path.parts):
        return True
    if use_default_excludes and any(part in DEFAULT_EXCLUDES for part in path.parts):
        return True
    if exclude_globs and _matches_any(relative, exclude_globs):
        return True
    if include_globs and not _matches_any(relative, include_globs):
        return True
    return False


def _matches_any(relative: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(relative, pattern) for pattern in patterns)


def _symlink_stays_inside(path: Path, workspace_root: Path) -> bool:
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(workspace_root)
    except Exception:
        return False
    return True
