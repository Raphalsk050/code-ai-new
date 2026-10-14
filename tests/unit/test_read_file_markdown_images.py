"""read_file attaches the images a Markdown file references.

A document's figures carry part of what it says, so a Markdown read hands them
to the model as pictures - the same channel a screenshot takes - and names
each one in the result. What it must never do is follow a reference out of the
workspace, fetch from the network, or attach a picture whose reference the
model was not shown.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import threading
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from PIL import Image

from code_ai.bootstrap import build_application
from code_ai.config.models import AppConfig
from code_ai.events.bus import AsyncEventBus
from code_ai.providers.models import (
    FinishReason,
    ImageContent,
    ModelRequest,
    ModelResponse,
    ProviderCapabilities,
    ProviderEvent,
    ToolCall,
)
from code_ai.sandbox.session import SessionSandbox
from code_ai.tools.base import TOOL_IMAGES_KEY, ToolContext
from code_ai.tools.filesystem import ReadFileTool
from code_ai.tools.filesystem import markdown_images as module
from code_ai.tools.output import bound_text, bound_text_kept
from code_ai.util.paths import WorkspacePolicy

RED = (255, 0, 0)
GREEN = (0, 255, 0)
BLUE = (0, 0, 255)
YELLOW = (255, 255, 0)
CYAN = (0, 255, 255)
MAGENTA = (255, 0, 255)


def make_context(tmp_path: Path, *, max_chars: int | None = None, sandbox: bool = False):
    # The workspace is a child of tmp_path, so tmp_path itself is "outside".
    workspace = tmp_path / "project"
    workspace.mkdir(exist_ok=True)
    data: dict = {"api_mode": "ollama", "workspace": str(workspace)}
    if max_chars is not None:
        data["budgets"] = {"max_tool_output_chars": max_chars}
    return ToolContext(
        config=AppConfig.from_mapping(data),
        workspace=WorkspacePolicy.from_path(workspace),
        event_bus=AsyncEventBus(session_id="session"),
        cancel_event=asyncio.Event(),
        sandbox=(
            SessionSandbox.create(
                session_id="session-under-test",
                workspace=workspace,
                base_dir=tmp_path / "sandboxes",
            )
            if sandbox
            else None
        ),
    )


def image_bytes(color=RED, size=(8, 8), fmt="PNG", **save) -> bytes:
    buffer = io.BytesIO()
    mode = "RGB" if fmt in {"JPEG", "BMP"} else "RGBA"
    Image.new(mode, size, color).save(buffer, format=fmt, **save)
    return buffer.getvalue()


def write_image(path: Path, color=RED, size=(8, 8), fmt="PNG") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(image_bytes(color, size, fmt))
    return path


def write_doc(context: ToolContext, name: str, text: str) -> Path:
    path = context.workspace.root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def pictures(result: dict) -> list[tuple[str, tuple[int, int], tuple]]:
    """Media type, size and top-left colour of each attached picture, in order."""

    seen = []
    for entry in result.get(TOOL_IMAGES_KEY, []):
        image = Image.open(io.BytesIO(base64.b64decode(entry["data"])))
        seen.append((entry["media_type"], image.size, image.convert("RGB").getpixel((0, 0))))
    return seen


def colors(result: dict) -> list[tuple]:
    return [color for _, _, color in pictures(result)]


def reasons(result: dict) -> dict[str, str]:
    return {entry["ref"]: entry["reason"] for entry in result["images"].get("skipped", [])}


async def read(context: ToolContext, path: str, **arguments) -> dict:
    return await ReadFileTool().execute({"path": path, **arguments}, context)


# --------------------------------------------------------------- what is found


async def test_every_kind_of_reference_is_attached_in_the_order_it_appears(tmp_path) -> None:
    context = make_context(tmp_path)
    root = context.workspace.root
    write_image(root / "docs/img/one.png", RED)
    absolute = write_image(root / "assets/two.png", GREEN)
    write_image(root / "docs/img/three.png", BLUE)
    write_image(root / "docs/img/four.png", YELLOW)
    write_image(root / "docs/img/five.png", CYAN)
    embedded = base64.b64encode(image_bytes(MAGENTA)).decode()
    write_doc(
        context,
        "docs/guide.md",
        "# Guide\n"
        "\n"
        '![relative](img/one.png "The title")\n'
        f"![absolute]({absolute})\n"
        "![by reference][three]\n"
        '<img src="img/four.png" alt="html block">\n'
        "\n"
        "Inline <img src='img/five.png'> in a sentence.\n"
        f"![embedded](data:image/png;base64,{embedded})\n"
        "\n"
        "[three]: img/three.png\n",
    )

    result = await read(context, "docs/guide.md")

    assert colors(result) == [RED, GREEN, BLUE, YELLOW, CYAN, MAGENTA]
    attached = result["images"]["attached"]
    assert [entry["image"] for entry in attached] == [1, 2, 3, 4, 5, 6]
    assert [(entry["ref"], entry["line"]) for entry in attached] == [
        ("img/one.png", 3),
        (str(absolute), 4),
        ("img/three.png", 5),
        ("img/four.png", 6),
        ("img/five.png", 8),
        ("data:image/png;base64,...", 9),
    ]
    assert attached[0]["alt"] == "relative" and attached[0]["path"] == "docs/img/one.png"
    assert attached[3]["alt"] == "html block"
    assert "skipped" not in result["images"]
    # The text itself is exactly what it was, base64 and all.
    assert embedded in result["content"]


async def test_references_written_as_code_or_comments_are_not_images(tmp_path) -> None:
    context = make_context(tmp_path)
    write_image(context.workspace.root / "shot.png")
    write_doc(
        context,
        "notes.md",
        "```markdown\n"
        "![fenced](shot.png)\n"
        "```\n"
        "~~~\n"
        '<img src="shot.png">\n'
        "~~~\n"
        "Write `![inline code](shot.png)` to show one.\n"
        "\n"
        "    ![indented code](shot.png)\n"
        "\n"
        "<!-- ![commented](shot.png) -->\n"
        '<p><!-- <img src="shot.png"> --></p>\n',
    )

    result = await read(context, "notes.md")

    assert TOOL_IMAGES_KEY not in result
    assert "images" not in result


async def test_only_the_lines_returned_bring_their_images(tmp_path) -> None:
    context = make_context(tmp_path)
    root = context.workspace.root
    write_image(root / "a.png", RED)
    write_image(root / "b.png", GREEN)
    write_image(root / "c.png", BLUE)
    write_doc(
        context,
        "doc.md",
        "```\n"  # 1: a fence opened above the range still decides what is code
        "intro\n"  # 2
        "![in a fence](a.png)\n"  # 3
        "```\n"  # 4
        "![first](a.png)\n"  # 5
        "\n"  # 6
        "![second][b]\n"  # 7
        "\n"  # 8
        "![third](c.png)\n"  # 9
        "\n"
        "[b]: b.png\n",  # 11: defined below the range, still resolved
    )

    middle = await read(context, "doc.md", start_line=3, end_line=7)
    tail = await read(context, "doc.md", start_line=9)

    assert colors(middle) == [RED, GREEN]
    assert [entry["line"] for entry in middle["images"]["attached"]] == [5, 7]
    assert colors(tail) == [BLUE]


async def test_a_reference_cut_out_of_a_long_read_brings_no_image(tmp_path) -> None:
    """Truncated text keeps its head and tail; the middle was never shown."""

    context = make_context(tmp_path, max_chars=400)
    root = context.workspace.root
    write_image(root / "head.png", RED)
    write_image(root / "middle.png", GREEN)
    write_image(root / "tail.png", BLUE)
    filler = "".join(f"filler line {index}\n" for index in range(60))
    write_doc(
        context,
        "long.md",
        "![head](head.png)\n" + filler + "![middle](middle.png)\n" + filler + "![tail](tail.png)\n",
    )

    result = await read(context, "long.md")

    assert result["truncated"] is True
    assert "middle.png" not in result["content"]
    assert colors(result) == [RED, BLUE]


async def test_spellings_a_renderer_accepts_find_the_file(tmp_path) -> None:
    context = make_context(tmp_path)
    write_image(context.workspace.root / "img/my picture.png", RED)
    write_image(context.workspace.root / "img/logo.png", GREEN)
    write_doc(
        context,
        "README.md",
        "![a](img/my%20picture.png)\n"
        "![b](<img/my picture.png>)\n"
        "![c](img/logo.png?raw=true)\n"
        "![d](img/logo.png#gh-dark-mode-only)\n",
    )

    result = await read(context, "README.md")

    # The same two files, each attached once and then referred back to.
    assert colors(result) == [RED, GREEN]
    assert reasons(result) == {
        "img/my picture.png": "same image as image 1",
        "img/logo.png#gh-dark-mode-only": "same image as image 2",
    }


# ------------------------------------------------------- what is not attached


async def test_what_cannot_be_attached_is_listed_with_the_reason(tmp_path) -> None:
    context = make_context(tmp_path)
    root = context.workspace.root
    write_image(tmp_path / "secret.png")
    (root / "diagram.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
    (root / "fake.png").write_text("not a picture at all")
    write_image(root / "scan.tiff", fmt="TIFF")
    (root / "folder.png").mkdir()
    svg_uri = "data:image/svg+xml;base64," + base64.b64encode(b"<svg/>").decode()
    write_doc(
        context,
        "doc.md",
        "![missing](nowhere.png)\n"
        "![escape](../secret.png)\n"
        f"![absolute escape]({tmp_path / 'secret.png'})\n"
        "![remote](https://example.com/chart.png)\n"
        "![plain http](http://example.com/chart.png)\n"
        "![scheme relative](//cdn.example.com/chart.png)\n"
        "![vector](diagram.svg)\n"
        f"![vector inline]({svg_uri})\n"
        "![impostor](fake.png)\n"
        "![tiff](scan.tiff)\n"
        "![directory](folder.png)\n"
        "![ftp](ftp://example.com/a.png)\n",
    )

    result = await read(context, "doc.md")

    assert TOOL_IMAGES_KEY not in result
    assert "attached" not in result["images"]
    found = reasons(result)
    assert found["nowhere.png"] == "not found"
    assert found["../secret.png"] == "outside the workspace"
    assert found[str(tmp_path / "secret.png")] == "outside the workspace"
    for remote in (
        "https://example.com/chart.png",
        "http://example.com/chart.png",
        "//cdn.example.com/chart.png",
    ):
        assert found[remote] == "remote image, not fetched"
    assert found["diagram.svg"].startswith("unsupported format (svg)")
    assert found["data:image/svg+xml;base64,..."].startswith("unsupported format (svg)")
    assert found["fake.png"] == "not a readable image"
    assert found["scan.tiff"] == "unsupported format (tiff)"
    assert found["folder.png"] == "not a file"
    assert found["ftp://example.com/a.png"] == "unsupported reference (ftp:)"


async def test_a_name_the_filesystem_cannot_look_up_does_not_fail_the_read(tmp_path) -> None:
    context = make_context(tmp_path)
    write_image(context.workspace.root / "ok.png", GREEN)
    long_name = "x" * 5000 + ".png"
    write_doc(context, "doc.md", f"![long]({long_name})\n![ok](ok.png)\n")

    result = await read(context, "doc.md")

    assert colors(result) == [GREEN]
    assert result["images"]["skipped"][0]["reason"] == "not found"


async def test_nothing_outside_the_workspace_is_ever_opened(tmp_path, monkeypatch) -> None:
    context = make_context(tmp_path)
    secret = write_image(tmp_path / "secret.png")
    link = context.workspace.root / "inside.png"
    try:
        link.symlink_to(secret)
    except OSError as exc:  # Windows grants this only under Developer Mode/admin
        pytest.skip(f"creating a symlink is not permitted here: {exc}")
    write_doc(context, "doc.md", "![a](../secret.png)\n![b](inside.png)\n")
    opened: list[Path] = []
    real = module.read_bytes
    monkeypatch.setattr(
        module, "read_bytes", lambda path, **kw: opened.append(path) or real(path, **kw)
    )

    result = await read(context, "doc.md")

    assert opened == []
    assert set(reasons(result).values()) == {"outside the workspace"}


async def test_a_sandbox_read_stays_inside_the_sandbox(tmp_path) -> None:
    context = make_context(tmp_path, sandbox=True)
    sandbox_root = context.sandbox.root
    write_image(sandbox_root / "out/plot.png", GREEN)
    write_image(context.workspace.root / "project.png", RED)
    (sandbox_root / "out/report.md").write_text(
        f"![plot](plot.png)\n![project]({context.workspace.root / 'project.png'})\n",
        encoding="utf-8",
    )

    result = await read(context, "out/report.md", location="sandbox")

    assert colors(result) == [GREEN]
    assert result["images"]["attached"][0]["path"] == "out/plot.png"
    assert list(reasons(result).values()) == ["outside the sandbox"]


async def test_past_the_limit_images_are_listed_and_not_read(tmp_path, monkeypatch) -> None:
    context = make_context(tmp_path)
    total = module.MAX_IMAGES_PER_READ + 2
    for index in range(total):
        write_image(context.workspace.root / f"fig{index}.png", (index * 20, 0, 0))
    write_doc(context, "doc.md", "".join(f"![f{i}](fig{i}.png)\n" for i in range(total)))
    opened: list[Path] = []
    real = module.read_bytes
    monkeypatch.setattr(
        module, "read_bytes", lambda path, **kw: opened.append(path) or real(path, **kw)
    )

    result = await read(context, "doc.md")

    assert len(result[TOOL_IMAGES_KEY]) == module.MAX_IMAGES_PER_READ
    assert len(opened) == module.MAX_IMAGES_PER_READ
    skipped = result["images"]["skipped"]
    limit = module.MAX_IMAGES_PER_READ
    assert [entry["ref"] for entry in skipped] == [f"fig{i}.png" for i in range(limit, total)]
    assert all(entry["reason"].startswith(f"over the limit of {limit}") for entry in skipped)


async def test_a_long_list_of_skipped_references_is_counted_not_spelled_out(tmp_path) -> None:
    context = make_context(tmp_path)
    count = module.MAX_REPORTED_SKIPS + 5
    write_doc(
        context,
        "badges.md",
        "".join(f"![b{i}](https://img.shields.io/badge/{i}.svg)\n" for i in range(count)),
    )

    result = await read(context, "badges.md")

    assert len(result["images"]["skipped"]) == module.MAX_REPORTED_SKIPS
    assert result["images"]["more_skipped"] == 5


async def test_a_file_over_the_size_cap_is_not_read(tmp_path, monkeypatch) -> None:
    context = make_context(tmp_path)
    write_image(context.workspace.root / "huge.png", size=(64, 64))
    write_doc(context, "doc.md", "![huge](huge.png)\n")
    monkeypatch.setattr(module, "MAX_IMAGE_FILE_BYTES", 16)

    result = await read(context, "doc.md")

    assert TOOL_IMAGES_KEY not in result
    assert reasons(result)["huge.png"].startswith("file too large")


async def test_an_image_too_many_pixels_to_decode_is_skipped(tmp_path, monkeypatch) -> None:
    context = make_context(tmp_path)
    write_image(context.workspace.root / "wide.png", size=(300, 200))
    write_doc(context, "doc.md", "![wide](wide.png)\n")
    monkeypatch.setattr(module, "MAX_IMAGE_PIXELS", 1000)

    result = await read(context, "doc.md")

    assert reasons(result)["wide.png"] == "image too large (300x200 pixels)"


# ------------------------------------------------------------ what is sent


async def test_a_large_image_is_downscaled_before_it_is_attached(tmp_path) -> None:
    context = make_context(tmp_path)
    write_image(context.workspace.root / "screen.png", RED, size=(3840, 2160))
    write_image(context.workspace.root / "photo.jpg", GREEN, size=(4000, 3000), fmt="JPEG")
    write_image(context.workspace.root / "icon.png", BLUE, size=(32, 32))
    write_doc(context, "doc.md", "![s](screen.png)\n![p](photo.jpg)\n![i](icon.png)\n")

    result = await read(context, "doc.md")

    edge = module.MAX_IMAGE_EDGE_PX
    assert pictures(result) == [
        ("image/png", (edge, round(edge * 2160 / 3840)), RED),
        # A photo stays a JPEG: as PNG it would be several times the bytes.
        ("image/jpeg", (edge, edge * 3 // 4), pytest.approx(GREEN, abs=8)),
        ("image/png", (32, 32), BLUE),
    ]
    attached = result["images"]["attached"]
    assert attached[0]["downscaled_from"] == "3840x2160"
    assert attached[1]["downscaled_from"] == "4000x3000"
    assert "downscaled_from" not in attached[2]


async def test_a_small_png_or_jpeg_travels_untouched(tmp_path) -> None:
    context = make_context(tmp_path)
    png = write_image(context.workspace.root / "a.png", RED, size=(40, 30)).read_bytes()
    write_doc(context, "doc.md", "![a](a.png)\n")

    result = await read(context, "doc.md")

    assert base64.b64decode(result[TOOL_IMAGES_KEY][0]["data"]) == png


async def test_gif_webp_and_bmp_are_sent_as_png(tmp_path) -> None:
    context = make_context(tmp_path)
    root = context.workspace.root
    frames = [Image.new("RGB", (10, 10), color) for color in (RED, GREEN, BLUE)]
    frames[0].save(root / "anim.gif", save_all=True, append_images=frames[1:], duration=50)
    (root / "pic.webp").write_bytes(image_bytes(GREEN, fmt="WEBP", lossless=True))
    write_image(root / "pic.bmp", BLUE, fmt="BMP")
    write_doc(context, "doc.md", "![g](anim.gif) ![w](pic.webp) ![b](pic.bmp)\n")

    result = await read(context, "doc.md")

    # The first frame of the animation; every one of them as a PNG.
    assert pictures(result) == [
        ("image/png", (10, 10), RED),
        ("image/png", (8, 8), GREEN),
        ("image/png", (8, 8), BLUE),
    ]


async def test_a_truncated_image_is_skipped_rather_than_sent(tmp_path) -> None:
    """An endpoint refusing a broken image would cost every image in the request."""

    context = make_context(tmp_path)
    whole = image_bytes(RED, size=(200, 200))
    (context.workspace.root / "broken.png").write_bytes(whole[: len(whole) // 2])
    write_doc(context, "doc.md", "![broken](broken.png)\n")

    result = await read(context, "doc.md")

    assert TOOL_IMAGES_KEY not in result
    assert reasons(result)["broken.png"] == "not a readable image"


# ------------------------------------------------------------ and otherwise


async def test_a_file_that_is_not_markdown_gets_no_images(tmp_path) -> None:
    context = make_context(tmp_path)
    write_image(context.workspace.root / "a.png")
    write_doc(context, "notes.txt", "![a](a.png)\n<img src='a.png'>\n")

    result = await read(context, "notes.txt")

    assert set(result) == {
        "path",
        "location",
        "sha256",
        "start_line",
        "end_line",
        "content",
        "truncated",
    }


async def test_markdown_without_images_reads_exactly_as_before(tmp_path) -> None:
    context = make_context(tmp_path)
    write_doc(context, "plain.md", "# Title\n\nJust [a link](other.md) and text.\n")

    result = await read(context, "plain.md")

    assert "images" not in result and TOOL_IMAGES_KEY not in result
    assert result["content"] == "# Title\n\nJust [a link](other.md) and text.\n"


async def test_images_are_read_off_the_event_loop(tmp_path, monkeypatch) -> None:
    context = make_context(tmp_path)
    write_image(context.workspace.root / "a.png")
    write_doc(context, "doc.md", "![a](a.png)\n")
    seen: list[int] = []
    real = module.read_bytes
    monkeypatch.setattr(
        module,
        "read_bytes",
        lambda path, **kw: seen.append(threading.get_ident()) or real(path, **kw),
    )

    result = await read(context, "doc.md")

    assert result[TOOL_IMAGES_KEY]
    assert seen and threading.get_ident() not in seen


def test_the_description_says_markdown_images_are_attached() -> None:
    description = ReadFileTool().description
    assert "Markdown" in description and "images" in description


@pytest.mark.parametrize("length", [0, 10, 63, 64, 100, 1000, 5000])
@pytest.mark.parametrize("max_chars", [10, 63, 100, 400, 12000])
def test_the_kept_ranges_are_exactly_what_bound_text_keeps(length, max_chars) -> None:
    text = "".join(chr(0x4E00 + index) for index in range(length))

    kept = bound_text_kept(length, max_chars)

    assert bound_text(text, max_chars) == "\n...[truncated]...\n".join(
        text[start:end] for start, end in kept
    )
    assert sum(end - start for start, end in kept) <= max(max_chars, 0)


def test_bound_text_still_keeps_half_the_budget_ahead_and_the_rest_behind() -> None:
    text = "".join(chr(0x4E00 + index) for index in range(1000))

    assert bound_text(text, 100) == text[:50] + "\n...[truncated]...\n" + text[-10:]


# ------------------------------------------------------------ delivery


def _config(tmp_path: Path, **overrides) -> AppConfig:
    data = {
        "api_mode": "ollama",
        "workspace": str(tmp_path),
        "model": "main-model",
        "permission_mode": "bypass",
        "memories_dir": str(tmp_path / "memories"),
        "vision_model": "",
        # The image path is under test here, not planning.
        "planner": {"enabled": False},
    }
    data.update(overrides)
    return AppConfig.from_mapping(data)


class _ReadsTheGuide:
    """Asks for docs/guide.md, then answers; records every request."""

    def __init__(self) -> None:
        self.stream_requests: list[ModelRequest] = []
        self.complete_requests: list[ModelRequest] = []

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(streaming=True, tool_calling=True, image_support=True)

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        self.stream_requests.append(request)
        if len(self.stream_requests) == 1:
            yield ProviderEvent(
                kind="completed",
                response=ModelResponse(
                    tool_calls=[
                        ToolCall(id="read-1", name="read_file", arguments={"path": "docs/guide.md"})
                    ],
                    finish_reason=FinishReason.TOOL_CALLS,
                ),
            )
            return
        yield ProviderEvent(kind="text_delta", text_delta="done")
        yield ProviderEvent(
            kind="completed",
            response=ModelResponse(text="done", finish_reason=FinishReason.STOP),
        )

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.complete_requests.append(request)
        return ModelResponse(text="A box diagram.", finish_reason=FinishReason.STOP)

    async def close(self) -> None:
        return None


def _write_guide(tmp_path: Path) -> list[bytes]:
    figures = [image_bytes(RED, size=(20, 10)), image_bytes(GREEN, size=(10, 20))]
    (tmp_path / "docs/img").mkdir(parents=True)
    (tmp_path / "docs/img/arch.png").write_bytes(figures[0])
    (tmp_path / "docs/img/flow.png").write_bytes(figures[1])
    (tmp_path / "docs/guide.md").write_text(
        "# Guide\n\n![Architecture](img/arch.png)\n\n![Flow](img/flow.png)\n",
        encoding="utf-8",
    )
    return figures


async def _run(tmp_path: Path, provider: _ReadsTheGuide, **overrides) -> None:
    app = build_application(config=_config(tmp_path, **overrides), provider=provider)
    await app.start()
    result = await app.submit_user_message("what does the guide show?")
    await app.close()
    assert result.error is None


async def test_the_images_reach_the_next_request_attached_to_the_tool_result(tmp_path) -> None:
    figures = _write_guide(tmp_path)
    provider = _ReadsTheGuide()

    await _run(tmp_path, provider)

    messages = provider.stream_requests[1].messages
    index = next(i for i, m in enumerate(messages) if m.role == "tool" and m.name == "read_file")
    tool_message, following = messages[index], messages[index + 1]
    # The result names the pictures; the pixels are not in its JSON.
    report = json.loads(tool_message.content)["images"]
    assert [entry["ref"] for entry in report["attached"]] == ["img/arch.png", "img/flow.png"]
    assert TOOL_IMAGES_KEY not in tool_message.content
    assert not tool_message.images
    # They follow it, in order, as the attachment of that result.
    assert following.role == "user"
    assert following.content.startswith("Output of read_file (see the 2 attached images)")
    assert following.images == [
        ImageContent(data=base64.b64encode(figure).decode("ascii"), media_type="image/png")
        for figure in figures
    ]
    assert provider.complete_requests == []


async def test_a_model_that_cannot_see_gets_the_images_described(tmp_path) -> None:
    _write_guide(tmp_path)
    provider = _ReadsTheGuide()
    events: list[str] = []

    app = build_application(
        config=_config(tmp_path, vision_model="vision-model"), provider=provider
    )
    app.subscribe(lambda event: events.append(event.event_type))
    await app.start()
    await app.submit_user_message("what does the guide show?")
    await app.close()

    # The vision model saw both pictures, in a call of its own.
    (vision_request,) = provider.complete_requests
    assert vision_request.model == "vision-model"
    assert len(vision_request.messages[-1].images) == 2
    # The main model got the description and no pixels at all.
    request = provider.stream_requests[1]
    assert all(not message.images for message in request.messages)
    assert any("A box diagram." in message.content for message in request.messages)
    assert "vision.analysis.started" in events


async def test_the_per_request_image_cap_applies_to_them_too(tmp_path) -> None:
    _write_guide(tmp_path)
    provider = _ReadsTheGuide()

    await _run(tmp_path, provider, max_images_per_request=1)

    # One rides along as pixels; the other was transcribed by the main model.
    request = provider.stream_requests[1]
    assert sum(len(message.images) for message in request.messages) == 1
    assert len(provider.complete_requests) == 1
