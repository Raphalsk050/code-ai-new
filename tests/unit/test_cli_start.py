"""A start that waits on something says so, instead of sitting silent."""

from __future__ import annotations

import time

from code_ai.cli.main import main
from code_ai.ui.terminal import app


class _Terminal:
    """stdin as a person's terminal: nothing piped in, and reading it would wait."""

    def isatty(self) -> bool:
        return True

    def read(self) -> str:
        raise AssertionError("read a terminal for a task: that waits for Ctrl+D in silence")


def test_headless_in_a_terminal_says_what_it_wants_instead_of_waiting(
    monkeypatch, capsys, tmp_path
) -> None:
    monkeypatch.setattr("sys.stdin", _Terminal())

    code = main(["--headless", "--workspace", str(tmp_path)])

    assert code == 2
    assert "reads the task from stdin" in capsys.readouterr().err


def test_a_slow_start_says_it_is_still_starting(monkeypatch, capsys) -> None:
    monkeypatch.setattr(app, "_SLOW_START_S", 0.05)

    with app._startup_watch():
        time.sleep(0.4)

    assert "Code-AI is still starting" in capsys.readouterr().err


def test_a_quick_start_says_nothing(monkeypatch, capsys) -> None:
    monkeypatch.setattr(app, "_SLOW_START_S", 5.0)

    with app._startup_watch():
        pass
    time.sleep(0.1)

    assert capsys.readouterr().err == ""
