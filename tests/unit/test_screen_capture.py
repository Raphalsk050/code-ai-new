from __future__ import annotations

import base64
import subprocess

import pytest

from code_ai.core.errors import ToolExecutionError
from code_ai.core.orchestration import _take_tool_images
from code_ai.providers.models import ImageContent
from code_ai.tools.base import TOOL_IMAGES_KEY
from code_ai.tools.computer import capture

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


@pytest.fixture(autouse=True)
def _on_linux(monkeypatch):
    """These cover the Linux paths, which is where the display server bites.

    Without this the suite would only exercise them on a Linux runner, and the
    ordering and error-message logic they pin down is exactly what a developer
    on macOS or Windows cannot check by running the tool.
    """

    monkeypatch.setattr(capture.platform, "system", lambda: "Linux")


def _no_backends(monkeypatch) -> None:
    monkeypatch.setattr(capture.shutil, "which", lambda name: None)
    monkeypatch.setattr(capture, "_pillow_capture", lambda: None)


# ------------------------------------------------------------------ ordering


def test_the_session_type_decides_which_tool_is_tried_first(monkeypatch) -> None:
    """A tool being installed says nothing about whether it works here.

    wl-clipboard and grim are routinely pulled in as dependencies on X11
    desktops, where they exit complaining about the display. Ordering by the
    session is what keeps the first attempt from being the doomed one.
    """

    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)
    assert [b.name for b in capture.linux_backends()][0] == "grim"

    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    assert [b.name for b in capture.linux_backends()][0] == "maim"

    # Both families stay reachable either way: XWayland runs X11 tools under
    # Wayland, so a mislabelled session should cost ordering, not the feature.
    names = {b.name for b in capture.linux_backends()}
    assert {"grim", "maim", "gnome-screenshot", "import"} <= names


# ------------------------------------------------------------------ failures


def test_a_backend_that_fails_at_runtime_falls_through_to_the_next(monkeypatch) -> None:
    """Wayland refusing grim must not end the attempt; the next tool may work."""

    monkeypatch.setattr(capture.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    tried: list[str] = []

    def fake_run(argv, **kwargs):
        tried.append(argv[0])
        if argv[0] == "maim":  # first choice on X11, and it fails
            return subprocess.CompletedProcess(argv, 1, b"", b"cannot open display")
        return subprocess.CompletedProcess(argv, 0, PNG, b"")

    monkeypatch.setattr(capture, "_run", fake_run)
    monkeypatch.setattr(capture, "downscale_png", lambda data, *a, **k: data)
    monkeypatch.setattr(capture, "_pillow_capture", lambda: None)

    assert capture.capture_screen_png() == PNG
    assert tried[0] == "maim" and len(tried) > 1


def test_a_tool_that_exits_zero_without_writing_is_not_a_success(monkeypatch, tmp_path) -> None:
    """A compositor can refuse a capture and leave the tool reporting success."""

    backend = capture.CaptureBackend("scrot", ("scrot", "-o"))
    monkeypatch.setattr(
        capture, "_run", lambda argv, **k: subprocess.CompletedProcess(argv, 0, b"", b"")
    )
    assert capture._try_backend(backend, tmp_path / "missing.png") is None


def test_a_headless_session_says_so_instead_of_naming_packages(monkeypatch) -> None:
    """Installing grim does not help a container with no display attached."""

    for variable in ("WAYLAND_DISPLAY", "DISPLAY", "XDG_SESSION_TYPE"):
        monkeypatch.delenv(variable, raising=False)
    _no_backends(monkeypatch)

    with pytest.raises(ToolExecutionError) as caught:
        capture.capture_screen_png()
    message = str(caught.value)
    assert "no desktop session" in message.lower()
    assert "install" not in message.lower()


def test_with_a_display_but_no_tools_the_error_names_what_to_install(monkeypatch) -> None:
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    _no_backends(monkeypatch)

    with pytest.raises(ToolExecutionError) as caught:
        capture.capture_screen_png()
    message = str(caught.value)
    assert "maim" in message and "grim" in message


# ------------------------------------------------------------------ delivery


def test_the_screenshot_is_lifted_out_of_the_payload_not_serialised() -> None:
    """base64 in the JSON body would eat the whole tool-output budget."""

    payload = {
        "captured": True,
        TOOL_IMAGES_KEY: [{"data": base64.b64encode(PNG).decode(), "media_type": "image/png"}],
    }
    images = _take_tool_images(payload)

    assert [type(image) for image in images] == [ImageContent]
    assert images[0].media_type == "image/png"
    # Removed from the payload, so what the model reads as text stays small.
    assert TOOL_IMAGES_KEY not in payload
    assert payload == {"captured": True}


def test_a_payload_with_no_attachment_is_left_exactly_as_it_was() -> None:
    payload = {"hits": [1, 2, 3]}
    assert _take_tool_images(payload) == []
    assert payload == {"hits": [1, 2, 3]}
    assert _take_tool_images("not a dict") == []
    assert _take_tool_images({TOOL_IMAGES_KEY: "not a list"}) == []
