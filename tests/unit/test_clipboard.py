from __future__ import annotations

import subprocess

from code_ai.ui.terminal import clipboard


def test_paste_returns_clipboard_text(monkeypatch) -> None:
    monkeypatch.setattr(clipboard, "_resolve_paste_command", lambda: ("fake-paste",))

    def fake_run(command, capture_output, check):  # noqa: ARG001 - signature match
        assert command == ("fake-paste",)
        return subprocess.CompletedProcess(command, 0, stdout=b"hello world\n", stderr=b"")

    monkeypatch.setattr(clipboard.subprocess, "run", fake_run)
    assert clipboard.paste_from_system_clipboard() == "hello world\n"


def test_paste_returns_none_when_no_tool(monkeypatch) -> None:
    monkeypatch.setattr(clipboard, "_resolve_paste_command", lambda: None)
    assert clipboard.paste_from_system_clipboard() is None


def test_paste_returns_none_on_failure(monkeypatch) -> None:
    monkeypatch.setattr(clipboard, "_resolve_paste_command", lambda: ("fake-paste",))

    def boom(*_args, **_kwargs):
        raise subprocess.CalledProcessError(1, "fake-paste")

    monkeypatch.setattr(clipboard.subprocess, "run", boom)
    assert clipboard.paste_from_system_clipboard() is None
