from __future__ import annotations

from rich.console import Console

from code_ai.ui.terminal.code_view import (
    LIVE_CODE_MAX_ROWS,
    guess_lexer,
    live_code_header,
    render_live_code,
)


def _plain(renderable, width: int = 60) -> str:
    console = Console(width=width, force_terminal=False, no_color=True)
    with console.capture() as capture:
        console.print(renderable)
    return capture.get()


def test_guess_lexer_uses_the_path_extension() -> None:
    assert guess_lexer("src/app.py", "x = 1") == "python"


def test_guess_lexer_falls_back_to_text_for_an_unknown_target() -> None:
    assert guess_lexer("", "") == "text"


def test_header_reads_as_in_progress_then_done() -> None:
    active = live_code_header(
        tool="write_file", path="src/app.py", code_key="content", lines=3, complete=False, glyph="✎"
    )
    done = live_code_header(
        tool="write_file", path="src/app.py", code_key="content", lines=3, complete=True, glyph="✎"
    )
    assert "writing src/app.py" in active.plain
    assert "3 linhas" in active.plain
    assert "wrote src/app.py" in done.plain
    assert done.plain.startswith("✓")


def test_header_names_the_edit_verb_for_a_replacement() -> None:
    header = live_code_header(
        tool="edit_code", path="a.py", code_key="new_text", lines=1, complete=False, glyph="✎"
    )
    assert "replacing in a.py" in header.plain


def test_header_falls_back_to_the_tool_when_there_is_no_path() -> None:
    header = live_code_header(
        tool="create_skill", path="", code_key="instructions", lines=0, complete=False, glyph="✎"
    )
    assert "create_skill" in header.plain


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

    assert "1 linha" in rendered


def test_live_window_handles_an_empty_start() -> None:
    rendered = _plain(render_live_code(tool="write_file", path="a.py", code=""))

    assert "writing a.py" in rendered
