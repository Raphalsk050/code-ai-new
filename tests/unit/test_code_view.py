from __future__ import annotations

from rich.console import Console

from code_ai.ui.terminal.code_view import (
    LIVE_CODE_MAX_ROWS,
    guess_lexer,
    live_code_header,
    live_code_lexer,
    live_code_reason,
    live_code_title,
    render_live_code,
)


def _plain(renderable, width: int = 60) -> str:
    console = Console(width=width, force_terminal=False, no_color=True)
    with console.capture() as capture:
        console.print(renderable)
    return capture.get()


def _colour_of(renderable, token: str, width: int = 70) -> str:
    """The foreground colour the renderable paints ``token`` in."""
    console = Console(width=width, force_terminal=True)
    for segment in console.render(renderable, console.options.update_width(width)):
        if segment.text.strip() == token and segment.style and segment.style.color:
            return segment.style.color.name
    raise AssertionError(f"{token!r} was never painted")


def test_guess_lexer_uses_the_path_extension() -> None:
    assert guess_lexer("src/app.py", "x = 1") == "python"


def test_guess_lexer_falls_back_to_text_for_an_unknown_target() -> None:
    assert guess_lexer("", "") == "text"


def test_header_names_the_operation_and_marks_it_done() -> None:
    active = live_code_header(
        tool="write_file", path="src/app.py", code_key="content", lines=3, glyph="✎"
    )
    done = live_code_header(
        tool="write_file", path="src/app.py", code_key="content", lines=3, complete=True
    )
    # The same wording the approval dialog uses for the finished call.
    assert "Create / overwrite:  src/app.py" in active.plain
    assert "3 lines" in active.plain
    assert done.plain.startswith("✓")


def test_header_says_a_replacement_is_not_the_diff() -> None:
    header = live_code_header(tool="edit_code", path="a.py", code_key="new_text", lines=1)
    assert "Edit:  a.py" in header.plain
    assert "replacement" in header.plain


def test_header_falls_back_to_the_tool_when_there_is_no_label() -> None:
    header = live_code_header(tool="some_writer", path="", code_key="content")
    assert "some_writer" in header.plain


def test_reason_is_collapsed_and_capped() -> None:
    rendered = live_code_reason("keep the cache\n   bounded   so the budget holds")
    assert rendered.plain == "Why: keep the cache bounded so the budget holds"
    assert live_code_reason("x" * 500).plain.endswith("…")


def test_live_window_shows_only_the_newest_rows_with_real_line_numbers() -> None:
    code = "".join(f"line{index}\n" for index in range(1, 41))

    rendered = _plain(render_live_code(tool="write_file", path="a.py", code=code))

    assert "line40" in rendered
    assert "line1\n" not in rendered  # scrolled out of the window
    # Line numbers keep naming the real lines of the file, not the window.
    first_visible = 40 - LIVE_CODE_MAX_ROWS + 1
    assert f"{first_visible} line{first_visible}" in rendered


def test_live_window_keeps_a_short_file_whole() -> None:
    rendered = _plain(render_live_code(tool="write_file", path="a.py", code="a = 1\nb = 2\n"))

    assert "a = 1" in rendered
    assert "b = 2" in rendered


def test_live_window_counts_lines_without_the_trailing_blank() -> None:
    rendered = _plain(render_live_code(tool="write_file", path="a.py", code="a = 1\n"))

    assert "1 lines" in rendered


def test_window_opens_with_its_context_before_any_source() -> None:
    # The whole point of the window: the frame, the target and the reason are
    # on screen before there is a single character of code to show.
    rendered = _plain(
        render_live_code(
            tool="write_file", path="a.py", code="", reason="bound the cache"
        )
    )

    assert "Create / overwrite:  a.py" in rendered
    assert "Why: bound the cache" in rendered
    assert "receiving" in rendered


def test_window_border_title_names_the_call_and_its_target() -> None:
    assert live_code_title("write_file", "src/app.py") == ("write_file", "src/app.py")


# --- highlighting the visible tail in context --------------------------------


def _long_docstring_source() -> str:
    # The window's first visible row lands deep inside the docstring.
    body = "".join(f"    doc line {index}\n" for index in range(30))
    return f'def f():\n    """\n{body}    """\n    return 1\n'


def test_tail_is_lexed_with_the_source_above_it() -> None:
    # Regression: lexing only the visible rows left the lexer with no idea it
    # was standing inside a docstring, so the prose came out as identifiers and
    # the closing quotes read as an *opening* one - painting the rest of the
    # file as a string.
    window = render_live_code(tool="write_file", path="a.py", code=_long_docstring_source())

    keyword = _colour_of(window, "return")
    docstring = _colour_of(window, "doc line 25")
    assert keyword != docstring, "the keyword after the docstring is still a keyword"
    # monokai: keywords cyan, strings yellow.
    assert keyword == "#66d9ef"
    assert docstring == "#e6db74"


def test_look_back_is_bounded_so_a_huge_file_costs_the_same() -> None:
    from code_ai.ui.terminal.code_view import LIVE_CODE_LOOKBACK_LINES

    big = "".join(f"x{index} = {index}\n" for index in range(5000))
    small = "".join(f"x{index} = {index}\n" for index in range(200))
    console = Console(width=70, force_terminal=True)

    def segments(code: str) -> int:
        window = render_live_code(tool="write_file", path="a.py", code=code)
        return len(list(console.render(window, console.options.update_width(70))))

    # Same work either way: only the window plus its look-back is ever lexed.
    assert segments(big) == segments(small)
    assert LIVE_CODE_LOOKBACK_LINES > LIVE_CODE_MAX_ROWS


# --- settling the language ---------------------------------------------------


def test_language_settles_from_the_path_on_the_first_fragment() -> None:
    assert live_code_lexer("write_file", "src/app.py", "d", complete=False) == "python"


def test_pathless_writers_have_a_known_format() -> None:
    assert live_code_lexer("create_skill", "", "#", complete=False) == "markdown"
    assert live_code_lexer("create_rule", "", "#", complete=False) == "markdown"


def test_content_guessing_waits_for_enough_source_to_guess_from() -> None:
    assert live_code_lexer("some_writer", "", "de", complete=False) == "text"
    guessed = live_code_lexer("some_writer", "", "def f():\n    return 1\n" * 20, complete=False)
    assert guessed != "text"
