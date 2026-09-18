"""Typing @ in the prompt offers the workspace's files.

The matching rules get their own tests because they are the part that decides
whether the picker saves a keystroke or costs one; the widget test below proves
the panel and the Tab key are really wired to them.
"""

from __future__ import annotations

from textual.widgets import Static, TextArea

from code_ai.ui.terminal.app import create_terminal_app
from code_ai.ui.terminal.mentions import (
    file_suggestions,
    mention_completion,
    mention_prefix,
    render_mentions,
)
from tests.unit.test_terminal_ui import FakeTerminalApplication


def workspace(tmp_path):
    (tmp_path / "src" / "code_ai" / "core").mkdir(parents=True)
    (tmp_path / "src" / "code_ai" / "core" / "orchestration.py").write_text("x", encoding="utf-8")
    (tmp_path / "src" / "code_ai" / "prompts.py").write_text("x", encoding="utf-8")
    (tmp_path / "README.md").write_text("x", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    return tmp_path


# ------------------------------------------------------------------ prefix


def test_a_bare_at_opens_the_picker() -> None:
    # "" is a real answer - offer everything - and must not read as "no mention".
    assert mention_prefix("read @") == ""


def test_the_partial_after_the_at_is_what_is_matched() -> None:
    assert mention_prefix("please read @src/app") == "src/app"


def test_an_at_mid_word_is_not_a_mention() -> None:
    # An email address is the case that matters here.
    assert mention_prefix("mail me at rafael@gmail.com") is None


def test_a_finished_mention_stops_offering() -> None:
    assert mention_prefix("read @src/app.py and tell me") is None


def test_no_at_means_no_picker() -> None:
    assert mention_prefix("just a normal message") is None


# ------------------------------------------------------------------ matching


def test_a_bare_at_lists_the_workspace(tmp_path) -> None:
    paths = file_suggestions(workspace(tmp_path), "")

    assert "README.md" in paths
    assert "src/code_ai/prompts.py" in paths


def test_a_partial_matches_anywhere_in_the_path(tmp_path) -> None:
    paths = file_suggestions(workspace(tmp_path), "orch")

    assert paths == ["src/code_ai/core/orchestration.py"]


def test_a_name_match_outranks_a_directory_match(tmp_path) -> None:
    root = workspace(tmp_path)
    (root / "core.py").write_text("x", encoding="utf-8")

    paths = file_suggestions(root, "core")

    # core.py beats src/code_ai/core/orchestration.py: the name is what was typed.
    assert paths[0] == "core.py"


def test_matching_ignores_case(tmp_path) -> None:
    assert file_suggestions(workspace(tmp_path), "readme") == ["README.md"]


def test_nothing_matching_offers_nothing(tmp_path) -> None:
    assert file_suggestions(workspace(tmp_path), "zzzzz") == []


def test_ignored_directories_are_never_offered(tmp_path) -> None:
    root = workspace(tmp_path)
    (root / ".gitignore").write_text("secrets/\n", encoding="utf-8")
    (root / "secrets").mkdir()
    (root / "secrets" / "key.txt").write_text("x", encoding="utf-8")

    assert file_suggestions(root, "key") == []


def test_a_missing_workspace_is_not_a_crash(tmp_path) -> None:
    assert file_suggestions(tmp_path / "gone", "x") == []


def test_the_list_is_capped(tmp_path) -> None:
    root = tmp_path
    for index in range(30):
        (root / f"file{index}.txt").write_text("x", encoding="utf-8")

    assert len(file_suggestions(root, "file")) == 8


# ------------------------------------------------------------------ accepting


def test_accepting_replaces_the_partial_and_leaves_a_space() -> None:
    completed = mention_completion("read @orch", ["src/core/orchestration.py"])

    assert completed == "read @src/core/orchestration.py "


def test_accepting_a_bare_at_works_too() -> None:
    assert mention_completion("read @", ["README.md"]) == "read @README.md "


def test_nothing_to_accept_returns_none() -> None:
    assert mention_completion("read @orch", []) is None
    assert mention_completion("no mention here", ["README.md"]) is None


def test_the_panel_renders_one_path_per_line() -> None:
    assert render_mentions(["a.py", "b.py"]) == "@a.py\n@b.py"
    assert render_mentions([]) == ""


# ------------------------------------------------------------------ the prompt


async def test_typing_an_at_shows_the_files_and_tab_accepts_one(tmp_path) -> None:
    root = workspace(tmp_path)
    fake_app = FakeTerminalApplication(root)
    terminal_app = create_terminal_app(fake_app)

    async with terminal_app.run_test(size=(100, 40)) as pilot:
        input_widget = terminal_app.query_one("#input", TextArea)
        input_widget.value = "leia @orch"
        await pilot.pause(0.2)

        panel = terminal_app.query_one("#command-suggestions", Static)
        assert panel.display is True
        assert "orchestration.py" in str(panel.render())

        await pilot.press("tab")
        await pilot.pause(0.2)

        assert input_widget.text == "leia @src/code_ai/core/orchestration.py "

        await pilot.press("enter")
        await pilot.pause(0.2)
        assert fake_app.submitted == ["leia @src/code_ai/core/orchestration.py"]


async def test_the_slash_panel_still_works_when_no_mention_is_open(tmp_path) -> None:
    fake_app = FakeTerminalApplication(workspace(tmp_path))
    terminal_app = create_terminal_app(fake_app)

    async with terminal_app.run_test(size=(100, 40)) as pilot:
        terminal_app.query_one("#input", TextArea).value = "/doc"
        await pilot.pause(0.2)

        panel = terminal_app.query_one("#command-suggestions", Static)
        assert "/doctor" in str(panel.render())
