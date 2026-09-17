"""Into and out of PDF: Markdown, HTML, URLs, images and office files in; images and text out."""

from __future__ import annotations

import html
import io
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from code_ai.core.errors import ToolArgumentError, ToolExecutionError
from code_ai.tools.pdf.common import PAGE_SIZES, page_size, to_points

MARKDOWN_SUFFIXES = {".md", ".markdown", ".mdown", ".txt"}
HTML_SUFFIXES = {".html", ".htm", ".xhtml"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp", ".gif"}
OFFICE_SUFFIXES = {
    ".docx",
    ".doc",
    ".odt",
    ".rtf",
    ".pptx",
    ".ppt",
    ".odp",
    ".xlsx",
    ".xls",
    ".ods",
}

PRINT_CSS = """
html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body { font-family: "Segoe UI", "Helvetica Neue", Arial, "Liberation Sans", sans-serif;
  font-size: 11pt; line-height: 1.55; color: #1f2328; margin: 0; }
h1, h2, h3, h4 { line-height: 1.25; margin: 1.4em 0 0.5em; page-break-after: avoid;
  color: #0d1b2a; }
h1 { font-size: 22pt; border-bottom: 2px solid #d0d7de; padding-bottom: 0.2em; margin-top: 0; }
h2 { font-size: 16pt; border-bottom: 1px solid #d8dee4; padding-bottom: 0.15em; }
h3 { font-size: 13pt; }
p, ul, ol, blockquote, table, pre { margin: 0 0 0.8em; }
a { color: #0b5cad; text-decoration: none; }
code { font-family: Consolas, "SFMono-Regular", "Liberation Mono", monospace; font-size: 0.9em;
  background: #f2f4f7; padding: 0.1em 0.3em; border-radius: 3px; }
pre { background: #f6f8fa; border: 1px solid #e1e4e8; border-radius: 6px; padding: 0.8em 1em;
  overflow-x: hidden; white-space: pre-wrap; page-break-inside: avoid; }
pre code { background: none; padding: 0; }
blockquote { border-left: 4px solid #d0d7de; color: #57606a; padding: 0.1em 1em; margin-left: 0; }
table { border-collapse: collapse; width: 100%; page-break-inside: auto; }
tr { page-break-inside: avoid; }
th, td { border: 1px solid #d0d7de; padding: 0.35em 0.6em; text-align: left; vertical-align: top; }
th { background: #f0f3f6; font-weight: 600; }
tbody tr:nth-child(even) td { background: #fafbfc; }
img { max-width: 100%; }
hr { border: 0; border-top: 1px solid #d0d7de; margin: 1.5em 0; }
.page-break { page-break-after: always; break-after: page; }
"""

_PAGEBREAK = re.compile(r"^\s*(\\newpage|\\pagebreak|<!--\s*pagebreak\s*-->)\s*$", re.MULTILINE)


@dataclass
class PrintOptions:
    size: tuple[float, float] = PAGE_SIZES["a4"]
    landscape: bool = False
    margins: tuple[float, float, float, float] = (56.7, 56.7, 56.7, 56.7)
    header: str = ""
    footer: str = ""
    css: str = ""
    title: str = ""
    notes: list[str] = field(default_factory=list)


def print_options(arguments: dict[str, Any]) -> PrintOptions:
    options = PrintOptions()
    if arguments.get("page_size"):
        options.size = page_size(arguments["page_size"], "page_size")
    options.landscape = bool(arguments.get("landscape") or False)
    if arguments.get("margin") is not None:
        parts = str(arguments["margin"]).split()
        values = [to_points(part, "margin") for part in parts]
        if len(values) == 1:
            values *= 4
        elif len(values) == 2:
            values = [values[0], values[1], values[0], values[1]]
        elif len(values) != 4:
            raise ToolArgumentError("'margin' is one length, 'vertical horizontal' or four values.")
        options.margins = tuple(values)  # type: ignore[assignment]
    options.header = str(arguments.get("header") or "")
    options.footer = str(arguments.get("footer") or "")
    options.css = str(arguments.get("css") or "")
    options.title = str(arguments.get("title") or "")
    return options


def markdown_to_html(text: str, *, title: str, css: str, base: Path | None) -> str:
    from markdown_it import MarkdownIt

    parser = MarkdownIt("commonmark", {"html": True, "linkify": False, "typographer": True})
    parser.enable(["table", "strikethrough"])
    text = _PAGEBREAK.sub('<div class="page-break"></div>', text)
    body = parser.render(text)
    if not title:
        match = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
        title = match.group(1).strip() if match else "Document"
    base_tag = f'<base href="{base.as_uri()}/">' if base is not None else ""
    return (
        f'<!doctype html><html><head><meta charset="utf-8">{base_tag}'
        f"<title>{html.escape(title)}</title><style>{PRINT_CSS}{css}</style></head>"
        f"<body>{body}</body></html>"
    )


def _template(text: str) -> str:
    """Header/footer text for Chromium, with {page} {total} {title} {date} filled at print."""

    if not text:
        return "<span></span>"
    escaped = html.escape(text)
    for key, css_class in (
        ("{page}", "pageNumber"),
        ("{total}", "totalPages"),
        ("{title}", "title"),
        ("{date}", "date"),
    ):
        escaped = escaped.replace(key, f'<span class="{css_class}"></span>')
    return (
        '<div style="font-size:8pt;color:#666;width:100%;padding:0 1.5cm;'
        f'font-family:Arial,sans-serif;text-align:center">{escaped}</div>'
    )


async def html_to_pdf(
    output: Path,
    *,
    options: PrintOptions,
    verify_ssl: bool,
    url: str | None = None,
    markup: str | None = None,
) -> int:
    from code_ai.tools.office.chromium import headless_page

    width, height = options.size
    if options.landscape:
        width, height = max(width, height), min(width, height)
    top, right, bottom, left = options.margins
    async with headless_page(verify_ssl=verify_ssl) as page:
        failures: list[str] = []
        page.on("requestfailed", lambda request: failures.append(request.url))
        with tempfile.TemporaryDirectory(prefix="code-ai-pdf-") as scratch:
            if markup is not None:
                # From a file, so file:// images next to the source are allowed to load.
                staged = Path(scratch, "document.html")
                staged.write_text(markup, encoding="utf-8")
                url = staged.as_uri()
            try:
                await page.goto(url, wait_until="networkidle", timeout=60_000)
            except Exception as exc:  # noqa: BLE001 - playwright's own error types
                raise ToolExecutionError(f"Could not load {url}: {exc}") from exc
            if options.css and markup is None:
                await page.add_style_tag(content=options.css)
            await page.emulate_media(media="print")
            # Playwright takes px, in, cm or mm - not pt.
            data = await page.pdf(
                width=_inches(width),
                height=_inches(height),
                margin={
                    "top": _inches(top),
                    "right": _inches(right),
                    "bottom": _inches(bottom),
                    "left": _inches(left),
                },
                print_background=True,
                display_header_footer=bool(options.header or options.footer),
                header_template=_template(options.header),
                footer_template=_template(options.footer),
                outline=True,
                tagged=True,
            )
        if failures:
            options.notes.append(f"{len(failures)} resource(s) failed to load: {failures[:5]}")
    output.write_bytes(data)
    from pypdf import PdfReader

    return len(PdfReader(io.BytesIO(data)).pages)


def _inches(points: float) -> str:
    return f"{points / 72.0:.4f}in"


def images_to_pdf(
    images: list[Path], output: Path, *, size: str | None, margin: float, dpi: int
) -> int:
    """One page per image. size 'fit' makes each page the image's own size."""

    from PIL import Image, ImageOps
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas as rl_canvas

    buffer = io.BytesIO()
    canvas = rl_canvas.Canvas(buffer)
    for path in images:
        try:
            with Image.open(path) as opened:
                frames = []
                for frame_index in range(getattr(opened, "n_frames", 1)):
                    opened.seek(frame_index)
                    frames.append(ImageOps.exif_transpose(opened.copy()))
        except OSError as exc:
            raise ToolArgumentError(f"Cannot read image {path.name}: {exc}") from None
        for image in frames:
            if image.mode not in {"RGB", "L", "RGBA"}:
                image = image.convert("RGBA" if "A" in image.mode else "RGB")
            px_w, px_h = image.size
            natural = (px_w * 72.0 / dpi, px_h * 72.0 / dpi)
            if not size or size == "fit":
                page_w, page_h = natural
                box = (0.0, 0.0, page_w, page_h)
            else:
                page_w, page_h = page_size(size, "page_size")
                if (px_w > px_h) != (page_w > page_h):
                    page_w, page_h = page_h, page_w
                avail_w, avail_h = page_w - 2 * margin, page_h - 2 * margin
                scale = min(avail_w / natural[0], avail_h / natural[1])
                w, h = natural[0] * scale, natural[1] * scale
                box = ((page_w - w) / 2, (page_h - h) / 2, w, h)
            canvas.setPageSize((page_w, page_h))
            canvas.drawImage(ImageReader(image), *box, mask="auto")
            canvas.showPage()
    canvas.save()
    output.write_bytes(buffer.getvalue())
    from pypdf import PdfReader

    return len(PdfReader(io.BytesIO(buffer.getvalue())).pages)


def pdf_to_images(
    pdf: Path,
    pages: list[int],
    *,
    pattern: Path,
    image_format: str,
    dpi: int,
    password: str | None,
) -> list[Path]:
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(str(pdf), password=password)
    written: list[Path] = []
    try:
        width = max(3, len(str(len(document))))
        for page_index in pages:
            page = document[page_index]
            try:
                image = page.render(scale=dpi / 72.0, may_draw_forms=True).to_pil()
            finally:
                page.close()
            target = Path(str(pattern).replace("{page}", str(page_index + 1).zfill(width)))
            target.parent.mkdir(parents=True, exist_ok=True)
            if image_format == "jpg":
                image.convert("RGB").save(target, format="JPEG", quality=90, dpi=(dpi, dpi))
            else:
                image.save(target, format="PNG", optimize=True, dpi=(dpi, dpi))
            written.append(target)
    finally:
        document.close()
    return written


def pdf_to_text(pdf: Path, output: Path, *, pages: list[int], password: str | None) -> int:
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(str(pdf), password=password)
    chunks = []
    try:
        for page_index in pages:
            page = document[page_index]
            text_page = page.get_textpage()
            try:
                content = text_page.get_text_range().replace("\r\n", "\n").strip()
            finally:
                text_page.close()
                page.close()
            chunks.append(f"--- page {page_index + 1} ---\n{content}")
    finally:
        document.close()
    text = "\n\n".join(chunks) + "\n"
    output.write_text(text, encoding="utf-8")
    return len(text)
