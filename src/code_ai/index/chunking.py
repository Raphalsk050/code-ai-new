"""Split a source file into retrievable chunks.

A chunk is the unit the index stores, ranks and hands back: small enough that a
hit points at one thing (a function, a class, a config block), large enough to
be read on its own. Python files are cut on their real symbol boundaries via
``ast``; other languages get a regex pass over the declaration keywords most of
them share; anything else falls back to fixed windows with a little overlap so
a match on a boundary is not lost to it.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import PurePosixPath

# Declaration openers shared by the C family, JS/TS, Go, Rust, Kotlin, Ruby,
# C#, Java, Lua and shell. Matching is deliberately loose: a false boundary
# only splits a chunk in two, whereas a missed one merges two symbols.
_SYMBOL_LINE = re.compile(
    r"""^(?P<indent>[ \t]*)
        (?:(?:export|public|private|protected|internal|static|abstract|final|
            async|override|virtual|inline|extern|unsafe|pub(?:\([^)]*\))?|default)\s+)*
        (?P<kind>class|struct|enum|interface|trait|impl|fn|func|function|def|
            module|namespace|type|record|object|protocol|extension|sub|proc)
        \s+(?P<name>[A-Za-z_][\w:.<>]*)""",
    re.VERBOSE,
)
# ``name = function(...)``, ``const name = (...) =>``, ``name(...) {`` (C/Java
# method definitions) and Go/TS method receivers are the other common shapes.
_ASSIGNED_FUNCTION = re.compile(
    r"""^(?P<indent>[ \t]*)
        (?:(?:export|const|let|var|static|public|private|protected)\s+)*
        (?P<name>[A-Za-z_][\w$]*)\s*
        (?:=\s*(?:async\s+)?(?:function\b|\([^)]*\)\s*=>)|
           \([^;{}]*\)\s*(?::\s*[\w<>\[\],\s|?]+)?\s*\{\s*$)""",
    re.VERBOSE,
)

MAX_CHUNK_LINES = 400


@dataclass(slots=True, frozen=True)
class Chunk:
    path: str
    start_line: int  # 1-based, inclusive
    end_line: int  # 1-based, inclusive
    text: str
    symbol: str = ""
    kind: str = "block"

    @property
    def line_count(self) -> int:
        return self.end_line - self.start_line + 1


def chunk_file(
    path: str,
    text: str,
    *,
    window_lines: int = 60,
    overlap_lines: int = 10,
) -> list[Chunk]:
    """Chunk ``text`` for retrieval. ``path`` is workspace-relative, POSIX style."""

    lines = text.splitlines()
    if not any(line.strip() for line in lines):
        return []
    suffix = PurePosixPath(path).suffix.lower()
    symbols: list[tuple[int, int, str, str]] = []
    if suffix in {".py", ".pyi"}:
        symbols = _python_symbols(text, lines)
    if not symbols:
        symbols = _regex_symbols(lines)
    if not symbols:
        return _windowed(path, lines, window_lines=window_lines, overlap_lines=overlap_lines)
    return _from_symbols(
        path, lines, symbols, window_lines=window_lines, overlap_lines=overlap_lines
    )


def _python_symbols(text: str, lines: list[str]) -> list[tuple[int, int, str, str]]:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError):
        return []
    found: list[tuple[int, int, str, str]] = []

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                name = f"{prefix}{child.name}"
                start = child.lineno
                # Decorators belong to the symbol: they are often the most
                # searchable line ("@app.route", "@pytest.fixture").
                if child.decorator_list:
                    start = min(start, min(d.lineno for d in child.decorator_list))
                end = child.end_lineno or start
                kind = "class" if isinstance(child, ast.ClassDef) else "function"
                if isinstance(child, ast.ClassDef) and end - start + 1 > MAX_CHUNK_LINES:
                    # A big class is split into its methods; the class header
                    # (up to the first method) stays as its own chunk.
                    methods = [
                        m
                        for m in child.body
                        if isinstance(m, ast.FunctionDef | ast.AsyncFunctionDef)
                    ]
                    if methods:
                        first = min(
                            min([m.lineno, *[d.lineno for d in m.decorator_list]]) for m in methods
                        )
                        found.append((start, first - 1, name, "class"))
                        visit(child, f"{name}.")
                        continue
                found.append((start, end, name, kind))
            elif isinstance(child, ast.If | ast.Try | ast.With):
                visit(child, prefix)

    visit(tree, "")
    found.sort()
    return found


def _regex_symbols(lines: list[str]) -> list[tuple[int, int, str, str]]:
    starts: list[tuple[int, int, str, str]] = []  # (line, indent, name, kind)
    for index, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith(("//", "#", "*", "/*", "--")):
            continue
        match = _SYMBOL_LINE.match(line)
        if match:
            starts.append(
                (index, len(match.group("indent")), match.group("name"), match.group("kind"))
            )
            continue
        match = _ASSIGNED_FUNCTION.match(line)
        if match and match.group("name") not in {
            "if",
            "for",
            "while",
            "switch",
            "return",
            "catch",
            "else",
        }:
            starts.append((index, len(match.group("indent")), match.group("name"), "function"))
    if len(starts) < 1:
        return []
    # Only top-level-ish declarations bound chunks: a symbol ends where the
    # next declaration at the same or shallower indent begins.
    result: list[tuple[int, int, str, str]] = []
    for position, (start, indent, name, kind) in enumerate(starts):
        end = len(lines)
        for later_start, later_indent, _, _ in starts[position + 1 :]:
            if later_indent <= indent:
                end = later_start - 1
                break
        if result and start <= result[-1][1] and indent > 0:
            # Nested inside the previous symbol; the parent chunk covers it.
            continue
        result.append((start, end, name, kind))
    return result


def _from_symbols(
    path: str,
    lines: list[str],
    symbols: list[tuple[int, int, str, str]],
    *,
    window_lines: int,
    overlap_lines: int,
) -> list[Chunk]:
    chunks: list[Chunk] = []
    cursor = 1

    def emit_gap(start: int, end: int) -> None:
        # Text between symbols (imports, module docstring, constants) is kept
        # too: an import block or a config table is exactly what some searches
        # are after.
        if end < start:
            return
        if not any(line.strip() for line in lines[start - 1 : end]):
            return
        chunks.extend(
            _windowed(
                path,
                lines,
                window_lines=window_lines,
                overlap_lines=overlap_lines,
                first_line=start,
                last_line=end,
                kind="block",
            )
        )

    for start, end, name, kind in symbols:
        if start > cursor:
            emit_gap(cursor, start - 1)
        if end < cursor:
            continue
        start = max(start, cursor)
        body = lines[start - 1 : end]
        if len(body) > MAX_CHUNK_LINES:
            chunks.extend(
                _windowed(
                    path,
                    lines,
                    window_lines=MAX_CHUNK_LINES // 2,
                    overlap_lines=overlap_lines,
                    first_line=start,
                    last_line=end,
                    symbol=name,
                    kind=kind,
                )
            )
        else:
            chunks.append(
                Chunk(
                    path=path,
                    start_line=start,
                    end_line=end,
                    text="\n".join(body),
                    symbol=name,
                    kind=kind,
                )
            )
        cursor = end + 1
    if cursor <= len(lines):
        emit_gap(cursor, len(lines))
    return chunks


def _windowed(
    path: str,
    lines: list[str],
    *,
    window_lines: int,
    overlap_lines: int,
    first_line: int = 1,
    last_line: int | None = None,
    symbol: str = "",
    kind: str = "block",
) -> list[Chunk]:
    last = len(lines) if last_line is None else last_line
    window = max(5, window_lines)
    overlap = max(0, min(overlap_lines, window - 1))
    chunks: list[Chunk] = []
    start = first_line
    while start <= last:
        end = min(last, start + window - 1)
        body = lines[start - 1 : end]
        if any(line.strip() for line in body):
            chunks.append(
                Chunk(
                    path=path,
                    start_line=start,
                    end_line=end,
                    text="\n".join(body),
                    symbol=symbol,
                    kind=kind,
                )
            )
        if end >= last:
            break
        start = end + 1 - overlap
    return chunks
