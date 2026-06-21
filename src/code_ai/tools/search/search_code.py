from __future__ import annotations

import asyncio
import fnmatch
import os
import re
import shutil
from pathlib import Path
from re import Pattern
from typing import Any

from code_ai.core.errors import ToolArgumentError, ToolExecutionError
from code_ai.tools.base import ToolCapability, ToolContext
from code_ai.tools.filesystem.list_files import DEFAULT_EXCLUDES
from code_ai.tools.output import bound_text
from code_ai.tools.schema import tool_schema


class SearchCodeTool:
    name = "search_code"
    description = "Search bounded text or regex matches in workspace source files."
    capabilities = frozenset({ToolCapability.LOCAL_READ})
    input_schema = tool_schema(
        {
            "query": {
                "type": "string",
                "description": "Literal text to search for across workspace files.",
            },
            "path": {
                "type": "string",
                "description": "Workspace-relative directory to search. Defaults to the root.",
            },
        },
        required=("query",),
    )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        query = str(arguments.get("query") or "")
        if not query:
            raise ToolArgumentError("query is required.")
        root = context.workspace.resolve(str(arguments.get("path") or "."), must_exist=True)
        case_sensitive = bool(arguments.get("case_sensitive", False))
        regex_mode = bool(arguments.get("regex", False))
        include_globs = _string_list(arguments.get("include_globs"))
        exclude_globs = _string_list(arguments.get("exclude_globs"))
        max_matches = max(1, min(1000, int(arguments.get("max_matches") or 100)))
        context_lines = max(0, min(5, int(arguments.get("context_lines") or 0)))
        include_hidden = bool(arguments.get("include_hidden", False))
        use_default_excludes = bool(arguments.get("use_default_excludes", True))

        if regex_mode:
            _compile_regex(query, case_sensitive=case_sensitive)

        rg_path = shutil.which("rg")
        if rg_path and context_lines == 0:
            return await _run_rg(
                rg_path=rg_path,
                query=query,
                root=root,
                workspace_root=context.workspace.root,
                case_sensitive=case_sensitive,
                regex_mode=regex_mode,
                include_globs=include_globs,
                exclude_globs=exclude_globs,
                max_matches=max_matches,
                include_hidden=include_hidden,
                use_default_excludes=use_default_excludes,
                timeout=context.config.budgets.default_tool_timeout_s,
                max_output_chars=context.config.budgets.max_tool_output_chars,
            )

        return _python_search(
            query=query,
            root=root,
            workspace_root=context.workspace.root,
            case_sensitive=case_sensitive,
            regex_mode=regex_mode,
            include_globs=include_globs,
            exclude_globs=exclude_globs,
            max_matches=max_matches,
            context_lines=context_lines,
            include_hidden=include_hidden,
            use_default_excludes=use_default_excludes,
            max_output_chars=context.config.budgets.max_tool_output_chars,
        )


async def _run_rg(
    *,
    rg_path: str,
    query: str,
    root: Path,
    workspace_root: Path,
    case_sensitive: bool,
    regex_mode: bool,
    include_globs: list[str],
    exclude_globs: list[str],
    max_matches: int,
    include_hidden: bool,
    use_default_excludes: bool,
    timeout: int,
    max_output_chars: int,
) -> dict[str, Any]:
    argv = [
        rg_path,
        "--line-number",
        "--with-filename",
        "--no-heading",
        "--color",
        "never",
    ]
    if not case_sensitive:
        argv.append("--ignore-case")
    if not regex_mode:
        argv.append("--fixed-strings")
    if include_hidden:
        argv.append("--hidden")
    if use_default_excludes:
        for name in sorted(DEFAULT_EXCLUDES):
            argv.extend(["--glob", f"!{name}/**"])
            argv.extend(["--glob", f"!**/{name}/**"])
    for pattern in include_globs:
        argv.extend(["--glob", pattern])
    for pattern in exclude_globs:
        argv.extend(["--glob", f"!{pattern}"])
    argv.extend(["--", query, str(root)])

    process = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_raw, stderr_raw = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError as exc:
        process.kill()
        await process.wait()
        raise ToolExecutionError(f"search_code timed out after {timeout}s.") from exc

    stdout = stdout_raw.decode("utf-8", errors="replace")
    stderr = stderr_raw.decode("utf-8", errors="replace")
    if process.returncode not in {0, 1}:
        raise ToolExecutionError(f"rg failed: {bound_text(stderr.strip(), 500)}")

    matches = []
    for line in stdout.splitlines():
        parsed = _parse_rg_line(
            line,
            workspace_root=workspace_root,
            max_output_chars=max_output_chars,
        )
        if parsed is None:
            continue
        matches.append(parsed)
        if len(matches) >= max_matches:
            break
    return {
        "query": query,
        "path": root.relative_to(workspace_root).as_posix() if root != workspace_root else ".",
        "engine": "rg",
        "matches": matches,
        "truncated": len(stdout.splitlines()) > len(matches),
    }


def _parse_rg_line(
    line: str, *, workspace_root: Path, max_output_chars: int
) -> dict[str, Any] | None:
    parts = line.split(":", 2)
    if len(parts) != 3:
        return None
    path_value, line_number, snippet = parts
    try:
        line_no = int(line_number)
        relative = Path(path_value).resolve(strict=False).relative_to(workspace_root).as_posix()
    except Exception:
        return None
    return {
        "path": relative,
        "line": line_no,
        "snippet": bound_text(snippet, min(max_output_chars, 600)),
        "context_before": [],
        "context_after": [],
    }


def _python_search(
    *,
    query: str,
    root: Path,
    workspace_root: Path,
    case_sensitive: bool,
    regex_mode: bool,
    include_globs: list[str],
    exclude_globs: list[str],
    max_matches: int,
    context_lines: int,
    include_hidden: bool,
    use_default_excludes: bool,
    max_output_chars: int,
) -> dict[str, Any]:
    regex = _compile_regex(query, case_sensitive=case_sensitive) if regex_mode else None
    matches: list[dict[str, Any]] = []
    truncated = False
    for path in _iter_text_candidates(
        root,
        workspace_root=workspace_root,
        include_hidden=include_hidden,
        include_globs=include_globs,
        exclude_globs=exclude_globs,
        use_default_excludes=use_default_excludes,
    ):
        if len(matches) >= max_matches:
            truncated = True
            break
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if b"\x00" in data:
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        lines = text.splitlines()
        for index, line in enumerate(lines):
            if _line_matches(line, query=query, regex=regex, case_sensitive=case_sensitive):
                matches.append(
                    _python_match(
                        path,
                        workspace_root=workspace_root,
                        lines=lines,
                        index=index,
                        context_lines=context_lines,
                        max_output_chars=max_output_chars,
                    )
                )
                if len(matches) >= max_matches:
                    truncated = True
                    break
    return {
        "query": query,
        "path": root.relative_to(workspace_root).as_posix() if root != workspace_root else ".",
        "engine": "python",
        "matches": matches,
        "truncated": truncated,
    }


def _iter_text_candidates(
    root: Path,
    *,
    workspace_root: Path,
    include_hidden: bool,
    include_globs: list[str],
    exclude_globs: list[str],
    use_default_excludes: bool,
):
    if root.is_file():
        relative = root.relative_to(workspace_root).as_posix()
        if _path_allowed(
            root,
            relative=relative,
            include_hidden=include_hidden,
            include_globs=include_globs,
            exclude_globs=exclude_globs,
            use_default_excludes=use_default_excludes,
        ):
            yield root
        return
    for current, dirs, files in os.walk(root):
        current_path = Path(current)
        dirs[:] = sorted(
            [
                directory
                for directory in dirs
                if _path_allowed(
                    current_path / directory,
                    relative=(current_path / directory).relative_to(workspace_root).as_posix(),
                    include_hidden=include_hidden,
                    include_globs=[],
                    exclude_globs=exclude_globs,
                    use_default_excludes=use_default_excludes,
                )
                and not (current_path / directory).is_symlink()
            ],
            key=str.casefold,
        )
        for filename in sorted(files, key=str.casefold):
            path = current_path / filename
            relative = path.relative_to(workspace_root).as_posix()
            if _path_allowed(
                path,
                relative=relative,
                include_hidden=include_hidden,
                include_globs=include_globs,
                exclude_globs=exclude_globs,
                use_default_excludes=use_default_excludes,
            ):
                yield path


def _path_allowed(
    path: Path,
    *,
    relative: str,
    include_hidden: bool,
    include_globs: list[str],
    exclude_globs: list[str],
    use_default_excludes: bool,
) -> bool:
    if not include_hidden and any(part.startswith(".") for part in path.parts):
        return False
    if use_default_excludes and any(part in DEFAULT_EXCLUDES for part in path.parts):
        return False
    if exclude_globs and any(fnmatch.fnmatch(relative, pattern) for pattern in exclude_globs):
        return False
    if include_globs and not any(fnmatch.fnmatch(relative, pattern) for pattern in include_globs):
        return False
    return True


def _compile_regex(query: str, *, case_sensitive: bool) -> Pattern[str]:
    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        return re.compile(query, flags)
    except re.error as exc:
        raise ToolArgumentError(f"Invalid regex: {exc}") from exc


def _line_matches(
    line: str,
    *,
    query: str,
    regex: Pattern[str] | None,
    case_sensitive: bool,
) -> bool:
    if regex:
        return bool(regex.search(line))
    if case_sensitive:
        return query in line
    return query.casefold() in line.casefold()


def _python_match(
    path: Path,
    *,
    workspace_root: Path,
    lines: list[str],
    index: int,
    context_lines: int,
    max_output_chars: int,
) -> dict[str, Any]:
    before_start = max(0, index - context_lines)
    after_end = min(len(lines), index + context_lines + 1)
    return {
        "path": path.relative_to(workspace_root).as_posix(),
        "line": index + 1,
        "snippet": bound_text(lines[index], min(max_output_chars, 600)),
        "context_before": lines[before_start:index],
        "context_after": lines[index + 1 : after_end],
    }


def _string_list(value: object) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ToolArgumentError("glob lists must contain only strings.")
    return [item for item in value if item]
