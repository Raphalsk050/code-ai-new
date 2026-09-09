from __future__ import annotations

import base64
import subprocess
from types import SimpleNamespace

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


# ------------------------------------------------------------------ clicking


def _geometry(**overrides):
    base = dict(left=0, top=0, width=3000, height=1920, image_width=1500, image_height=960)
    base.update(overrides)
    return capture.ScreenGeometry(**base)


def test_a_point_read_off_the_shrunken_image_maps_back_to_the_real_pixel() -> None:
    """The image is half-size here, so every coordinate is off by 2x unscaled."""

    geometry = _geometry()
    assert geometry.scale == 0.5
    assert geometry.to_screen(0, 0) == (0, 0)
    assert geometry.to_screen(750, 480) == (1500, 960)
    assert geometry.to_screen(1500, 960) == (3000, 1920)


def test_a_monitor_left_of_the_primary_one_shifts_the_whole_desktop() -> None:
    """The virtual desktop's origin is negative there, and clicks must follow.

    Without the offset every click on the left-hand monitor lands on the
    primary one instead - on whatever happens to be at the mirrored position.
    """

    geometry = _geometry(left=-1920, top=-200)
    assert geometry.to_screen(0, 0) == (-1920, -200)
    assert geometry.to_screen(1500, 960) == (1080, 1720)


def test_an_image_coordinate_is_converted_before_the_pointer_moves() -> None:
    from code_ai.tools.computer.common import resolve_point

    controller = SimpleNamespace(last_capture=_geometry())
    # Declared as image coordinates: scaled and offset on the way through.
    assert resolve_point(controller, {"coordinate_space": "image"}, 750, 480) == (1500, 960)
    # Declared as screen coordinates, or not declared: passed straight through,
    # so an existing caller that already knows real pixels is unaffected.
    assert resolve_point(controller, {"coordinate_space": "screen"}, 750, 480) == (750, 480)
    assert resolve_point(controller, {}, 750, 480) == (750, 480)


def test_clicking_from_an_image_before_looking_at_one_is_refused() -> None:
    """Guessing a scale would be a click at a plausible-looking wrong place."""

    from code_ai.core.errors import ToolArgumentError
    from code_ai.tools.computer.common import resolve_point

    controller = SimpleNamespace(last_capture=None)
    with pytest.raises(ToolArgumentError) as caught:
        resolve_point(controller, {"coordinate_space": "image"}, 10, 10)
    assert "capture_screen" in str(caught.value)


def test_an_unknown_coordinate_space_is_rejected_rather_than_assumed() -> None:
    from code_ai.core.errors import ToolArgumentError
    from code_ai.tools.computer.common import resolve_point

    controller = SimpleNamespace(last_capture=_geometry())
    with pytest.raises(ToolArgumentError):
        resolve_point(controller, {"coordinate_space": "pixels"}, 1, 1)


# ------------------------------------------------------------------ packaging


def _png_header(width: int, height: int) -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00" * 8
        + width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + b"\x00" * 40
    )


def test_windows_says_it_needs_pillow_rather_than_naming_nothing(monkeypatch) -> None:
    """Windows has no command-line screenshotter to fall back on.

    The generic message lists the CLI tools that could be installed, and on
    Windows that list is empty - it used to read "Install one of: ." which
    tells the reader nothing at all.
    """

    monkeypatch.setattr(capture.platform, "system", lambda: "Windows")
    monkeypatch.setattr(capture, "_pillow_installed", lambda: False)
    _no_backends(monkeypatch)

    with pytest.raises(ToolExecutionError) as caught:
        capture.capture_screen_png()
    assert "pillow" in str(caught.value).lower()


def test_a_capture_too_big_to_send_is_refused_when_it_cannot_be_shrunk(monkeypatch) -> None:
    """Sending it would spend the context window the downscale protects.

    Without Pillow the downscale is a no-op, so a full-resolution screenshot
    would travel as megabytes of base64 on every later request in the
    conversation - which is far harder to diagnose than a missing package.
    """

    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def no_pillow(name, *args, **kwargs):
        if name.startswith("PIL"):
            raise ImportError("Pillow is not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", no_pillow)

    with pytest.raises(ToolExecutionError) as caught:
        capture.downscale_png(_png_header(4000, 2000))
    assert "pillow" in str(caught.value).lower()

    # One that already fits needed nothing from Pillow, so it is left alone.
    small = _png_header(800, 600)
    assert capture.downscale_png(small) == small
