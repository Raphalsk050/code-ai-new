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
# make the typing stutter.
LIVE_CODE_MAX_ROWS = 14

# How many lines *above* the window are lexed along with it, without being
# shown. A lexer has no memory: handed the visible rows alone it cannot know it
# is standing inside a docstring or a block comment, so it paints the prose as
# code and then reads the closing quotes as an *opening* one - every remaining
# line comes out the colour of a string. Feeding it this much preceding source
# rebuilds the state it needs. The cost stays flat as the file grows, which is
# the whole point: what gets lexed is a window, never the whole file.
LIVE_CODE_LOOKBACK_LINES = 120

# The operation each writing tool performs, worded like the approval dialog's
# summary so the live window and the dialog read as one flow.
_OPERATION_LABELS = {
    "write_file": "Create / overwrite",
    "edit_code": "Edit",
    "create_rule": "Create rule",
    "create_skill": "Create skill",
}

# Languages for writing tools that name their target instead of pathing it.
# A deterministic answer beats guessing from a half-written fragment, which
# changes its mind as the file grows and repaints the window in a new palette.
_TOOL_DEFAULT_LEXERS = {
    "create_rule": "markdown",
    "create_skill": "markdown",
}

# Below this much source, a content-based guess is worthless - too little to
# tell one language from another. Only reached when there is no path and no
# per-tool default to go by.
_CONTENT_GUESS_MIN_CHARS = 200

_LIVE_ACTIVE_STYLE = Style(color="#ff9f1c", bold=True)
_LIVE_DONE_STYLE = Style(color="#48d17a", bold=True)
_LIVE_OPERATION_STYLE = Style(color="#9fb3c8")
_LIVE_TARGET_STYLE = Style(color="#d7dee8")
_LIVE_DETAIL_STYLE = Style(color="#6b7280")
# Same amber the approval dialog uses for the model's own explanation.
_LIVE_REASON_STYLE = Style(color="#f5c84c", bold=True)
_LIVE_REASON_MAX_CHARS = 220


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


def live_code_lexer(tool: str, path: str, code: str, complete: bool) -> str:
    """The language for a file being written, settled as early as possible.

    Colour that only shows up once the file is finished is not live colour, so
    this never waits: a path settles the language on the first fragment, and a
    tool that writes without one (a skill, a rule) has a known format. Guessing
    from the content is the last resort, and only once there is enough of it to
    guess from - before that the window stays plain rather than flickering
    through a wrong palette.
    """

    if path:
        return guess_lexer(path, code)
    default = _TOOL_DEFAULT_LEXERS.get(tool)
    if default:
        return default
    if complete or len(code) >= _CONTENT_GUESS_MIN_CHARS:
        return guess_lexer("", code)
    return "text"


def syntax_block(
    code: str,
    *,
    path: str = "",
    lexer: str | None = None,
    start_line: int = 1,
    line_range: tuple[int, int] | None = None,
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
        line_range=line_range,
    )


def live_code_title(tool: str, path: str) -> tuple[str, str]:
    """Border title and subtitle for the live window: the call and its target."""
    return tool or "tool", path


def live_code_header(
    *,
    tool: str,
    path: str,
    code_key: str = "",
    lines: int = 0,
    complete: bool = False,
    glyph: str = "✎",
) -> Text:
    """The window's caption: which operation is running, on what, how far along."""

    text = Text()
    text.append(
        ("✓ " if complete else f"{glyph} "),
        style=_LIVE_DONE_STYLE if complete else _LIVE_ACTIVE_STYLE,
    )
    text.append(_OPERATION_LABELS.get(tool, tool or "Write"), style=_LIVE_OPERATION_STYLE)
    if path:
        text.append(":  ", style=_LIVE_OPERATION_STYLE)
        text.append(path, style=_LIVE_TARGET_STYLE)
    if code_key == "new_text":
        # Be honest that this is the replacement going in, not the diff the
        # approval dialog will show once both halves of the edit have arrived.
        text.append("   replacement", style=_LIVE_DETAIL_STYLE)
    if lines:
        text.append(f"   ·   {lines} lines", style=_LIVE_DETAIL_STYLE)
    return text


def live_code_reason(reason: str) -> Text:
    """The model's own explanation of the change, worded like the dialog's."""
    collapsed = " ".join(reason.split())
    if len(collapsed) > _LIVE_REASON_MAX_CHARS:
        collapsed = collapsed[: _LIVE_REASON_MAX_CHARS - 1].rstrip() + "…"
    return Text("Why: ", style=_LIVE_REASON_STYLE).append(collapsed, style=_LIVE_REASON_STYLE)


def render_live_code(
    *,
    tool: str,
    path: str,
    code: str,
    code_key: str = "",
    reason: str = "",
    complete: bool = False,
    lexer: str | None = None,
    glyph: str = "✎",
    max_rows: int = LIVE_CODE_MAX_ROWS,
) -> RenderableType:
    """Render the window for a write in progress: its context over its source.

    The caption and the reason are drawn even before a single character of
    source has arrived, so the window opens with the *why* already on screen
    and fills in underneath - the same order the approval dialog presents them
    in once the call is complete.

    Only the newest rows are shown, but they are lexed together with the source
    above them (see :data:`LIVE_CODE_LOOKBACK_LINES`), so the colours are the
    ones the finished file would get rather than whatever a fragment read in
    isolation happens to suggest.
    """

    body = code[:-1] if code.endswith("\n") else code
    rows = body.split("\n") if body else []
    total = len(rows)
    header = live_code_header(
        tool=tool,
        path=path,
        code_key=code_key,
        lines=total,
        complete=complete,
        glyph=glyph,
    )
    parts: list[RenderableType] = [header]
    if reason.strip():
        parts.append(live_code_reason(reason))
    parts.append(Text(""))

    if not rows:
        parts.append(
            Text("done" if complete else "receiving…", style=_LIVE_DETAIL_STYLE)
        )
        return Group(*parts)

    first_visible = max(0, total - max_rows)
    anchor = max(0, first_visible - LIVE_CODE_LOOKBACK_LINES)
    parts.append(
        syntax_block(
            "\n".join(rows[anchor:]),
            path=path,
            lexer=lexer,
            start_line=anchor + 1,
            # 1-based and inclusive, relative to the region handed over: the
            # look-back is lexed for context but never drawn.
            line_range=(first_visible - anchor + 1, total - anchor),
        )
    )
    return Group(*parts)
