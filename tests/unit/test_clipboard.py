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


_PNG = b"\x89PNG\r\n\x1a\n" + b"pixels"


def test_paste_image_returns_png_bytes(monkeypatch) -> None:
    monkeypatch.setattr(clipboard, "_read_image_darwin", lambda: _PNG)
    monkeypatch.setattr(clipboard, "_read_image_linux", lambda: _PNG)
    monkeypatch.setattr(clipboard, "_read_image_windows", lambda: _PNG)
    assert clipboard.paste_image_from_system_clipboard() == _PNG


def test_paste_image_rejects_non_png_payloads(monkeypatch) -> None:
    # A reader that returns text (e.g. xclip echoing the text selection) must
    # not be mistaken for an image.
    monkeypatch.setattr(clipboard, "_read_image_darwin", lambda: b"not an image")
    monkeypatch.setattr(clipboard, "_read_image_linux", lambda: b"not an image")
    monkeypatch.setattr(clipboard, "_read_image_windows", lambda: b"not an image")
    assert clipboard.paste_image_from_system_clipboard() is None


def test_paste_image_returns_none_when_clipboard_has_no_image(monkeypatch) -> None:
    monkeypatch.setattr(clipboard, "_read_image_darwin", lambda: None)
    monkeypatch.setattr(clipboard, "_read_image_linux", lambda: None)
    monkeypatch.setattr(clipboard, "_read_image_windows", lambda: None)
    assert clipboard.paste_image_from_system_clipboard() is None


def test_darwin_reader_prefers_pngpaste(monkeypatch) -> None:
    monkeypatch.setattr(clipboard.shutil, "which", lambda name: name == "pngpaste")

    def fake_run(command, capture_output, check):  # noqa: ARG001 - signature match
        assert command == ("pngpaste", "-")
        return subprocess.CompletedProcess(command, 0, stdout=_PNG, stderr=b"")

    monkeypatch.setattr(clipboard.subprocess, "run", fake_run)
    assert clipboard._read_image_darwin() == _PNG


def test_linux_reader_uses_first_available_tool(monkeypatch) -> None:
    monkeypatch.setattr(clipboard.shutil, "which", lambda name: name == "xclip")

    def fake_run(command, capture_output, check):  # noqa: ARG001 - signature match
        assert command[0] == "xclip"
        assert "image/png" in command
        return subprocess.CompletedProcess(command, 0, stdout=_PNG, stderr=b"")

    monkeypatch.setattr(clipboard.subprocess, "run", fake_run)
    assert clipboard._read_image_linux() == _PNG


def test_linux_reader_returns_none_without_tools(monkeypatch) -> None:
    monkeypatch.setattr(clipboard.shutil, "which", lambda _name: None)
    assert clipboard._read_image_linux() is None


def test_windows_reader_decodes_base64_stdout(monkeypatch) -> None:
    import base64

    encoded = base64.b64encode(_PNG)

    def fake_run(command, capture_output, check):  # noqa: ARG001 - signature match
        assert command[0] == "powershell"
        return subprocess.CompletedProcess(command, 0, stdout=encoded, stderr=b"")

    monkeypatch.setattr(clipboard.subprocess, "run", fake_run)
    assert clipboard._read_image_windows() == _PNG


def test_windows_reader_returns_none_on_empty_output(monkeypatch) -> None:
    def fake_run(command, capture_output, check):  # noqa: ARG001 - signature match
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(clipboard.subprocess, "run", fake_run)
    assert clipboard._read_image_windows() is None
