from __future__ import annotations

import subprocess

from code_ai.ui.terminal import clipboard

_PNG = b"\x89PNG\r\n\x1a\n" + b"pixels"
_JPEG = b"\xff\xd8\xff\xe0" + b"pixels"
_WEBP = b"RIFF\x00\x00\x00\x00WEBP" + b"pixels"


def _completed(command, stdout: bytes) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr=b"")


def test_paste_returns_clipboard_text(monkeypatch) -> None:
    monkeypatch.setattr(clipboard, "_paste_commands", lambda: [("fake-paste",)])

    def fake_run(command, **_kwargs):
        assert command == ("fake-paste",)
        return _completed(command, b"hello world\n")

    monkeypatch.setattr(clipboard.subprocess, "run", fake_run)
    assert clipboard.paste_from_system_clipboard() == "hello world\n"


def test_paste_returns_none_when_no_tool(monkeypatch) -> None:
    monkeypatch.setattr(clipboard, "_paste_commands", lambda: [])
    assert clipboard.paste_from_system_clipboard() is None


def test_paste_falls_through_to_next_tool_on_runtime_failure(monkeypatch) -> None:
    # wl-paste can be installed yet unable to connect (X11 session); the next
    # candidate must still get its chance instead of the paste dying silently.
    monkeypatch.setattr(clipboard, "_paste_commands", lambda: [("wl-paste",), ("xclip", "-o")])

    def fake_run(command, **_kwargs):
        if command[0] == "wl-paste":
            raise subprocess.CalledProcessError(1, command)
        return _completed(command, b"from xclip")

    monkeypatch.setattr(clipboard.subprocess, "run", fake_run)
    assert clipboard.paste_from_system_clipboard() == "from xclip"


def test_paste_returns_none_when_every_tool_fails(monkeypatch) -> None:
    monkeypatch.setattr(clipboard, "_paste_commands", lambda: [("fake-paste",)])

    def boom(*_args, **_kwargs):
        raise subprocess.CalledProcessError(1, "fake-paste")

    monkeypatch.setattr(clipboard.subprocess, "run", boom)
    assert clipboard.paste_from_system_clipboard() is None


def test_copy_falls_through_to_next_tool_on_runtime_failure(monkeypatch) -> None:
    monkeypatch.setattr(clipboard, "_copy_commands", lambda: [("wl-copy",), ("xclip",)])
    used: list[tuple[str, ...]] = []

    def fake_run(command, **_kwargs):
        used.append(command)
        if command[0] == "wl-copy":
            raise subprocess.CalledProcessError(1, command)
        return _completed(command, b"")

    monkeypatch.setattr(clipboard.subprocess, "run", fake_run)
    assert clipboard.copy_to_system_clipboard("text") is True
    assert used == [("wl-copy",), ("xclip",)]


def test_linux_ordering_prefers_wayland_tools_under_wayland(monkeypatch) -> None:
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setattr(clipboard.shutil, "which", lambda _name: "/usr/bin/tool")
    ordered = clipboard._linux_ordered(clipboard._LINUX_PASTE_CANDIDATES)
    assert ordered[0][0] == "wl-paste"


def test_linux_ordering_prefers_x11_tools_without_wayland(monkeypatch) -> None:
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(clipboard.shutil, "which", lambda _name: "/usr/bin/tool")
    ordered = clipboard._linux_ordered(clipboard._LINUX_PASTE_CANDIDATES)
    assert ordered[0][0] == "xclip"
    # The Wayland tool stays available as a fallback, it is not dropped.
    assert ordered[-1][0] == "wl-paste"


def test_linux_ordering_skips_missing_tools(monkeypatch) -> None:
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(clipboard.shutil, "which", lambda name: name == "xsel" or None)
    ordered = clipboard._linux_ordered(clipboard._LINUX_PASTE_CANDIDATES)
    assert [command[0] for command in ordered] == ["xsel"]


def test_paste_image_returns_bytes_and_sniffed_media_type(monkeypatch) -> None:
    monkeypatch.setattr(clipboard, "_read_image_darwin", lambda: _PNG)
    monkeypatch.setattr(clipboard, "_read_image_linux", lambda: _PNG)
    monkeypatch.setattr(clipboard, "_read_image_windows", lambda: _PNG)
    assert clipboard.paste_image_from_system_clipboard() == (_PNG, "image/png")


def test_paste_image_accepts_non_png_formats(monkeypatch) -> None:
    # A JPEG copied from a browser is a perfectly good attachment; only the
    # media type has to follow the payload.
    monkeypatch.setattr(clipboard, "_read_image_darwin", lambda: _JPEG)
    monkeypatch.setattr(clipboard, "_read_image_linux", lambda: _JPEG)
    monkeypatch.setattr(clipboard, "_read_image_windows", lambda: _JPEG)
    assert clipboard.paste_image_from_system_clipboard() == (_JPEG, "image/jpeg")


def test_paste_image_rejects_non_image_payloads(monkeypatch) -> None:
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


def test_sniff_image_type_recognises_webp() -> None:
    assert clipboard._sniff_image_type(_WEBP) == "image/webp"


def test_darwin_reader_prefers_pngpaste(monkeypatch) -> None:
    monkeypatch.setattr(clipboard.shutil, "which", lambda name: name == "pngpaste")

    def fake_run(command, **_kwargs):
        assert command == ("pngpaste", "-")
        return _completed(command, _PNG)

    monkeypatch.setattr(clipboard.subprocess, "run", fake_run)
    assert clipboard._read_image_darwin() == _PNG


def test_linux_reader_negotiates_offered_mime_type(monkeypatch) -> None:
    # The clipboard offers a JPEG (a picture copied from a browser); the
    # reader must ask for that type instead of insisting on image/png.
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(clipboard.shutil, "which", lambda name: name == "xclip" or None)

    def fake_run(command, **_kwargs):
        assert command[0] == "xclip"
        if "TARGETS" in command:
            return _completed(command, b"TARGETS\ntext/html\nimage/jpeg\n")
        assert command[-1] == "image/jpeg"
        return _completed(command, _JPEG)

    monkeypatch.setattr(clipboard.subprocess, "run", fake_run)
    assert clipboard._read_image_linux() == _JPEG


def test_linux_reader_prefers_png_when_offered(monkeypatch) -> None:
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setattr(clipboard.shutil, "which", lambda name: name == "wl-paste" or None)

    def fake_run(command, **_kwargs):
        assert command[0] == "wl-paste"
        if "--list-types" in command:
            return _completed(command, b"image/jpeg\nimage/png\n")
        assert command[-1] == "image/png"
        return _completed(command, _PNG)

    monkeypatch.setattr(clipboard.subprocess, "run", fake_run)
    assert clipboard._read_image_linux() == _PNG


def test_linux_reader_returns_none_when_no_image_type_offered(monkeypatch) -> None:
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setattr(clipboard.shutil, "which", lambda name: name == "wl-paste" or None)

    def fake_run(command, **_kwargs):
        assert "--list-types" in command
        return _completed(command, b"text/plain;charset=utf-8\n")

    monkeypatch.setattr(clipboard.subprocess, "run", fake_run)
    assert clipboard._read_image_linux() is None


def test_linux_reader_returns_none_without_tools(monkeypatch) -> None:
    monkeypatch.setattr(clipboard.shutil, "which", lambda _name: None)
    assert clipboard._read_image_linux() is None


def test_windows_reader_decodes_base64_stdout(monkeypatch) -> None:
    import base64

    encoded = base64.b64encode(_PNG)

    def fake_run(command, **_kwargs):
        assert command[0] == "powershell"
        return _completed(command, encoded)

    monkeypatch.setattr(clipboard.subprocess, "run", fake_run)
    assert clipboard._read_image_windows() == _PNG


def test_windows_reader_returns_none_on_empty_output(monkeypatch) -> None:
    def fake_run(command, **_kwargs):
        return _completed(command, b"")

    monkeypatch.setattr(clipboard.subprocess, "run", fake_run)
    assert clipboard._read_image_windows() is None


def test_linux_clipboard_packages_suggests_wayland_package(monkeypatch) -> None:
    monkeypatch.setattr(clipboard.sys, "platform", "linux")
    monkeypatch.setattr(clipboard.shutil, "which", lambda _name: None)
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    assert clipboard.linux_clipboard_packages() == ("wl-clipboard",)


def test_linux_clipboard_packages_suggests_x11_package(monkeypatch) -> None:
    monkeypatch.setattr(clipboard.sys, "platform", "linux")
    monkeypatch.setattr(clipboard.shutil, "which", lambda _name: None)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setenv("DISPLAY", ":0")
    assert clipboard.linux_clipboard_packages() == ("xclip",)


def test_linux_clipboard_packages_empty_when_tool_installed(monkeypatch) -> None:
    monkeypatch.setattr(clipboard.sys, "platform", "linux")
    monkeypatch.setattr(clipboard.shutil, "which", lambda name: name == "xclip" or None)
    assert clipboard.linux_clipboard_packages() == ()


def test_linux_clipboard_packages_empty_on_other_platforms(monkeypatch) -> None:
    monkeypatch.setattr(clipboard.sys, "platform", "darwin")
    assert clipboard.linux_clipboard_packages() == ()
