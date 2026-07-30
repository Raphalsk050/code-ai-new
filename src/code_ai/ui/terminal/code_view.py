"""Syntax-highlighted code rendering shared by the terminal's code surfaces.

Two places show source: the approval dialog, which presents a finished call for
a decision, and the live window, which shows a file taking shape while the model
streams it. They must look like the same thing seen at two moments, so the
theme, the lexer guess and the truncation rules live here rather than being
reinvented per surface.
"""

from __future__ import annotations

from rich.console import Group, RenderableType
from rich.style import Style
from rich.syntax import Syntax
from rich.text import Text

# A dark Pygments theme that blends with the dialog background. The colours are
# language-agnostic: Pygments ships lexers for every supported language and we
# let it pick the right one from the file name (or the content itself).
SYNTAX_THEME = "monokai"
CODE_BACKGROUND = "#111820"
MAX_PREVIEW_CHARS = 40000

# How many rows of the live window are kept on screen. The window shows the
# newest lines, like a terminal tail: it cannot scroll while it is being written
# to, and re-highlighting an entire large file on every fragment is what would
# make the typing stutter. Highlighting a fixed handful of lines is constant
# work per update no matter how big the file gets.
LIVE_CODE_MAX_ROWS = 14

# What each writing argument is doing to the file, as a verb the header can use.
_CODE_KEY_VERBS = {
    "content": ("writing", "wrote"),
    "new_text": ("replacing in", "replaced in"),
    "instructions": ("writing", "wrote"),
}
_DEFAULT_VERBS = ("writing", "wrote")

_LIVE_ACTIVE_STYLE = Style(color="#ff9f1c", bold=True)
_LIVE_DONE_STYLE = Style(color="#48d17a", bold=True)
_LIVE_TARGET_STYLE = Style(color="#d7dee8")
_LIVE_DETAIL_STYLE = Style(color="#6b7280")


def guess_lexer(path: str, code: str) -> str:
    """Best-effort lexer name, agnostic of the language.

    When a path is available we match on its extension; otherwise we let
    Pygments analyse the content. Falls back to plain text on any failure so an
    unknown language never breaks the view.
    """

    try:
        if path:
            return Syntax.guess_lexer(path, code)
        from pygments.lexers import guess_lexer as pygments_guess_lexer

        return pygments_guess_lexer(code).aliases[0]
    except Exception:
        return "text"


def syntax_block(
    code: str,
    *,
    path: str = "",
    lexer: str | None = None,
    start_line: int = 1,
) -> Syntax:
    """Highlight ``code``, capped so a huge file cannot stall the terminal."""

    if len(code) > MAX_PREVIEW_CHARS:
        code = code[:MAX_PREVIEW_CHARS] + "\n… (truncated)"
    return Syntax(
        code or "",
        lexer or guess_lexer(path, code),
        theme=SYNTAX_THEME,
        line_numbers=True,
        indent_guides=True,
        word_wrap=False,
        background_color=CODE_BACKGROUND,
        padding=0,
        start_line=start_line,
    )


def live_code_header(
    *,
    tool: str,
    path: str,
    code_key: str,
    lines: int,
    complete: bool,
    glyph: str,
) -> Text:
    """The one-line caption above the live window: what is happening, to what."""

    active, done = _CODE_KEY_VERBS.get(code_key, _DEFAULT_VERBS)
    text = Text()
    text.append(
        ("✓ " if complete else f"{glyph} "),
        style=_LIVE_DONE_STYLE if complete else _LIVE_ACTIVE_STYLE,
    )
    text.append(f"{done if complete else active} ", style=_LIVE_DETAIL_STYLE)
    text.append(path or tool or "file", style=_LIVE_TARGET_STYLE)
    if lines:
        text.append(f"  ·  {lines} linha{'s' if lines != 1 else ''}", style=_LIVE_DETAIL_STYLE)
    return text


def render_live_code(
    *,
    tool: str,
    path: str,
    code: str,
    code_key: str = "",
    complete: bool = False,
    lexer: str | None = None,
    glyph: str = "✎",
    max_rows: int = LIVE_CODE_MAX_ROWS,
) -> RenderableType:
    """Render the file being written right now: a caption over its newest rows.

    Only the tail is highlighted (see :data:`LIVE_CODE_MAX_ROWS`), with the line
    numbers offset so they still name the real lines of the file. The result is
    a complete replacement for the widget's content, which is what keeps the
    update flicker-free: the window is repainted, never appended to.
    """

    body = code[:-1] if code.endswith("\n") else code
    rows = body.split("\n") if body else []
    total = len(rows)
    start = max(0, total - max_rows)
    header = live_code_header(
        tool=tool,
        path=path,
        code_key=code_key,
        lines=total,
        complete=complete,
        glyph=glyph,
    )
    if not rows:
        return Group(header, Text("…", style=_LIVE_DETAIL_STYLE))
    visible = "\n".join(rows[start:])
    return Group(header, syntax_block(visible, path=path, lexer=lexer, start_line=start + 1))
