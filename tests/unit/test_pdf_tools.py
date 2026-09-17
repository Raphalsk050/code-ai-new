from __future__ import annotations

import asyncio
import json

import pytest
from pypdf import PdfReader
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from code_ai.config.models import AppConfig
from code_ai.core.errors import ToolArgumentError
from code_ai.events.bus import AsyncEventBus
from code_ai.tools.base import TOOL_IMAGES_KEY, ToolCapability, ToolContext
from code_ai.tools.office.converters import find_libreoffice
from code_ai.tools.pdf import PdfConvertTool, PdfEditTool, PdfInspectTool
from code_ai.tools.pdf.common import rgb, to_points
from code_ai.util.paths import WorkspacePolicy


def make_context(tmp_path) -> ToolContext:
    config = AppConfig.from_mapping({"api_mode": "ollama", "workspace": str(tmp_path)})
    return ToolContext(
        config=config,
        workspace=WorkspacePolicy.from_path(tmp_path),
        event_bus=AsyncEventBus(session_id="session"),
        cancel_event=asyncio.Event(),
    )


def make_pdf(path, pages=3, form=False) -> None:
    doc = canvas.Canvas(str(path), pagesize=A4)
    for number in range(1, pages + 1):
        doc.setFont("Helvetica", 14)
        doc.drawString(72, 760, f"Page {number} marker")
        doc.drawString(72, 700, "Secret 123-456")
        if form and number == 1:
            doc.acroForm.textfield(name="name", x=72, y=560, width=200, height=20)
            doc.acroForm.checkbox(name="agree", x=72, y=520, size=16)
        doc.showPage()
    doc.save()


def page_texts(path) -> list[str]:
    return [page.extract_text() for page in PdfReader(str(path)).pages]


async def edit(tmp_path, operations, **extra):
    arguments = {"path": "in.pdf", "output": "out.pdf", "operations": json.dumps(operations)}
    arguments.update(extra)
    return await PdfEditTool().execute(arguments, make_context(tmp_path))


def test_units_and_colours() -> None:
    assert to_points("1in", "x") == 72
    assert round(to_points("2.54cm", "x"), 3) == 72
    assert to_points(10, "x") == 10
    assert rgb("#fff") == (1.0, 1.0, 1.0)
    with pytest.raises(ToolArgumentError):
        to_points("wide", "x")
    with pytest.raises(ToolArgumentError):
        rgb("#12")


def test_capabilities() -> None:
    assert ToolCapability.LOCAL_WRITE not in PdfInspectTool.capabilities
    assert ToolCapability.LOCAL_WRITE in PdfEditTool.capabilities


async def test_inspect_reports_structure_text_search_and_render(tmp_path) -> None:
    make_pdf(tmp_path / "in.pdf", form=True)
    result = await PdfInspectTool().execute(
        {"path": "in.pdf", "text_pages": "2", "search": "secret", "render_pages": "1"},
        make_context(tmp_path),
    )
    assert result["pages"] == 3
    assert result["page_sizes"] == ["1-3: A4 portrait 595x842pt"]
    assert {field["name"] for field in result["form_fields"]} == {"name", "agree"}
    assert "Page 2 marker" in result["text"][0]["text"]
    assert result["search"]["matches"] == 3
    assert len(result[TOOL_IMAGES_KEY]) == 1


async def test_structure_operations(tmp_path) -> None:
    make_pdf(tmp_path / "in.pdf", pages=4)
    make_pdf(tmp_path / "other.pdf", pages=2)
    result = await edit(
        tmp_path,
        [
            {"op": "append", "file": "other.pdf", "pages": "2"},
            {"op": "insert", "file": "other.pdf", "pages": "1", "at": 1},
            {"op": "delete", "pages": "2"},
            {"op": "keep", "pages": "last,1,1"},
            {"op": "rotate", "degrees": 90, "pages": "1"},
            {"op": "blank", "at": "end"},
        ],
    )
    assert result["pages"] == 4
    reader = PdfReader(str(tmp_path / "out.pdf"))
    assert reader.pages[0].rotation == 90
    assert reader.pages[3].extract_text() == ""


async def test_split_writes_parts(tmp_path) -> None:
    make_pdf(tmp_path / "in.pdf", pages=5)
    result = await edit(tmp_path, [{"op": "split", "every": 2, "output": "parts/p-{n}.pdf"}])
    assert result["files_written"] == ["parts/p-1.pdf", "parts/p-2.pdf", "parts/p-3.pdf"]
    assert len(PdfReader(str(tmp_path / "parts/p-3.pdf")).pages) == 1


async def test_drawing_operations_add_text(tmp_path) -> None:
    make_pdf(tmp_path / "in.pdf", pages=2)
    await edit(
        tmp_path,
        [
            {"op": "watermark", "text": "DRAFT"},
            {"op": "stamp", "text": "OK {page}", "position": "top-right"},
            {"op": "page_numbers", "format": "Page {page} of {total}"},
            {"op": "header_footer", "header": "Report", "footer": "Internal"},
        ],
    )
    texts = page_texts(tmp_path / "out.pdf")
    assert "DRAFT" in texts[1] and "OK 2" in texts[1]
    assert "Page 2 of 2" in texts[1] and "Report" in texts[0] and "Internal" in texts[0]


async def test_crop_and_resize(tmp_path) -> None:
    make_pdf(tmp_path / "in.pdf", pages=2)
    await edit(
        tmp_path,
        [
            {"op": "crop", "margins": "1in", "pages": "1"},
            {"op": "resize", "size": "letter", "pages": "2"},
        ],
    )
    reader = PdfReader(str(tmp_path / "out.pdf"))
    assert round(float(reader.pages[0].cropbox.width)) == round(A4[0] - 144)
    assert round(float(reader.pages[1].mediabox.width)) == 612


async def test_form_fill_and_flatten(tmp_path) -> None:
    make_pdf(tmp_path / "in.pdf", form=True)
    await edit(tmp_path, [{"op": "fill_form", "fields": {"name": "Ana", "agree": True}}])
    fields = PdfReader(str(tmp_path / "out.pdf")).get_fields()
    assert fields["name"]["/V"] == "Ana"
    assert fields["agree"]["/V"] == "/Yes"
    await PdfEditTool().execute(
        {
            "path": "out.pdf",
            "operations": [{"op": "fill_form", "fields": {"name": "Bia"}, "flatten": True}],
        },
        make_context(tmp_path),
    )
    assert not PdfReader(str(tmp_path / "out.pdf")).get_fields()


async def test_unknown_form_field_lists_the_real_ones(tmp_path) -> None:
    make_pdf(tmp_path / "in.pdf", form=True)
    with pytest.raises(ToolArgumentError, match="agree"):
        await edit(tmp_path, [{"op": "fill_form", "fields": {"nope": "x"}}])


async def test_metadata_bookmarks_links(tmp_path) -> None:
    make_pdf(tmp_path / "in.pdf", pages=3)
    await edit(
        tmp_path,
        [
            {"op": "metadata", "title": "Relatório", "author": "Ana"},
            {
                "op": "bookmarks",
                "items": [{"title": "One", "page": 1}, {"title": "Sub", "page": 2, "level": 2}],
            },
            {"op": "link", "page": 1, "rect": [72, 72, 200, 90], "url": "https://example.com"},
            {"op": "link", "page": 1, "rect": [72, 100, 200, 120], "to_page": 3},
        ],
    )
    reader = PdfReader(str(tmp_path / "out.pdf"))
    assert reader.metadata.title == "Relatório"
    assert reader.outline[0].title == "One"
    assert len(reader.pages[0]["/Annots"]) == 2


async def test_encrypt_then_inspect_with_password(tmp_path) -> None:
    make_pdf(tmp_path / "in.pdf")
    await edit(tmp_path, [{"op": "encrypt", "user_password": "pw"}])
    context = make_context(tmp_path)
    with pytest.raises(ToolArgumentError, match="password"):
        await PdfInspectTool().execute({"path": "out.pdf"}, context)
    result = await PdfInspectTool().execute({"path": "out.pdf", "password": "pw"}, context)
    assert result["encrypted"] is True


async def test_redaction_removes_the_text(tmp_path) -> None:
    make_pdf(tmp_path / "in.pdf", pages=2)
    result = await edit(
        tmp_path,
        [
            {"op": "redact", "text": ["123-456", "absent"], "pages": "1"},
            {"op": "redact", "areas": [{"page": 2, "rect": [0, 0, 300, 120]}]},
        ],
    )
    texts = page_texts(tmp_path / "out.pdf")
    assert "123-456" not in texts[0]
    assert "Page 2" not in texts[1]
    assert any("absent" in note for note in result["notes"])


async def test_compress_and_cleanup(tmp_path) -> None:
    make_pdf(tmp_path / "in.pdf")
    result = await edit(
        tmp_path,
        [{"op": "compress"}, {"op": "remove_annotations"}, {"op": "remove_javascript"}],
    )
    assert len(result["applied"]) == 3


async def test_a_bad_operation_leaves_the_file_untouched(tmp_path) -> None:
    make_pdf(tmp_path / "in.pdf")
    before = (tmp_path / "in.pdf").read_bytes()
    with pytest.raises(ToolArgumentError, match="operation 2"):
        await PdfEditTool().execute(
            {
                "path": "in.pdf",
                "operations": [{"op": "rotate", "degrees": 90}, {"op": "delete", "pages": "9"}],
            },
            make_context(tmp_path),
        )
    assert (tmp_path / "in.pdf").read_bytes() == before
    with pytest.raises(ToolArgumentError, match="unknown op"):
        await edit(tmp_path, [{"op": "explode"}])
    with pytest.raises(ToolArgumentError, match="needs one of"):
        await edit(tmp_path, [{"op": "watermark"}])
    with pytest.raises(ToolArgumentError, match="JSON"):
        await edit(tmp_path, "[not json")


async def test_pdf_to_images_and_text(tmp_path) -> None:
    make_pdf(tmp_path / "in.pdf", pages=2)
    context = make_context(tmp_path)
    images = await PdfConvertTool().execute(
        {"source": "in.pdf", "output": "img/page.png", "dpi": 50}, context
    )
    assert images["files"] == ["img/page-001.png", "img/page-002.png"]
    text = await PdfConvertTool().execute({"source": "in.pdf", "output": "all.txt"}, context)
    assert "Page 2 marker" in (tmp_path / "all.txt").read_text(encoding="utf-8")
    assert text["pages"] == 2


async def test_images_to_pdf(tmp_path) -> None:
    from PIL import Image

    Image.new("RGB", (300, 200), "red").save(tmp_path / "a.png")
    Image.new("RGB", (200, 300), "blue").save(tmp_path / "b.jpg")
    result = await PdfConvertTool().execute(
        {"sources": ["a.png", "b.jpg"], "output": "album.pdf", "page_size": "a4"},
        make_context(tmp_path),
    )
    reader = PdfReader(str(tmp_path / "album.pdf"))
    assert result["pages"] == 2
    assert float(reader.pages[0].mediabox.width) > float(reader.pages[0].mediabox.height)


async def _chromium_or_skip(coro):
    try:
        return await coro
    except Exception as exc:  # noqa: BLE001
        if "browser" in str(exc).lower() or "playwright" in str(exc).lower():
            pytest.skip(f"Chromium unavailable: {exc}")
        raise


async def test_markdown_to_pdf(tmp_path) -> None:
    (tmp_path / "doc.md").write_text(
        "# Title\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n\\newpage\n\n## Second\n", encoding="utf-8"
    )
    result = await _chromium_or_skip(
        PdfConvertTool().execute(
            {"source": "doc.md", "output": "doc.pdf", "footer": "p {page}/{total}"},
            make_context(tmp_path),
        )
    )
    assert result["pages"] == 2
    assert "Second" in page_texts(tmp_path / "doc.pdf")[1]


@pytest.mark.skipif(find_libreoffice() is None, reason="LibreOffice not installed")
async def test_office_to_pdf(tmp_path) -> None:
    import docx

    document = docx.Document()
    document.add_paragraph("Converted by office")
    document.save(tmp_path / "a.docx")
    result = await PdfConvertTool().execute(
        {"source": "a.docx", "output": "a.pdf"}, make_context(tmp_path)
    )
    assert "Converted by office" in page_texts(tmp_path / "a.pdf")[0]
    assert result["from"].startswith("office")
