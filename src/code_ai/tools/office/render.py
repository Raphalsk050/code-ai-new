"""PDF pages as PNGs, so the model can look at what it produced.

pdfium does the rasterising: the same engine Chrome renders PDFs with, shipped
as a wheel, with no system dependency to install.
"""

from __future__ import annotations

import io
from pathlib import Path

from code_ai.core.errors import ToolExecutionError

# Past this, a page image costs more context than it tells. Long side in pixels.
DEFAULT_MAX_SIDE = 1400
MAX_RENDERED_PAGES = 12


def page_count(pdf: Path) -> int:
    pdfium = _pdfium()
    document = pdfium.PdfDocument(str(pdf))
    try:
        return len(document)
    finally:
        document.close()


def render_pages(
    pdf: Path,
    pages: list[int],
    *,
    max_side: int = DEFAULT_MAX_SIDE,
    password: str | None = None,
) -> list[bytes]:
    """PNG bytes for each zero-based page index, scaled to fit ``max_side``."""

    pdfium = _pdfium()
    try:
        document = pdfium.PdfDocument(str(pdf), password=password)
    except pdfium.PdfiumError as exc:
        raise ToolExecutionError(f"Could not open {pdf.name} to render it: {exc}") from exc
    try:
        # Without a form environment, filled fields that lack appearance streams draw empty.
        document.init_forms()
        images: list[bytes] = []
        for index in pages[:MAX_RENDERED_PAGES]:
            page = document[index]
            try:
                width, height = page.get_size()
                scale = max_side / max(width, height, 1.0)
                bitmap = page.render(scale=scale, may_draw_forms=True)
                image = bitmap.to_pil()
                buffer = io.BytesIO()
                image.save(buffer, format="PNG", optimize=True)
                images.append(buffer.getvalue())
            finally:
                page.close()
        return images
    finally:
        document.close()


def png_thumbnail(png: bytes, max_side: int) -> bytes:
    """Shrink an already-rendered image; used for screenshots and slide previews."""

    from PIL import Image

    image = Image.open(io.BytesIO(png))
    if max(image.size) <= max_side:
        return png
    image.thumbnail((max_side, max_side))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _pdfium():
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:  # pragma: no cover - a required dependency
        raise ToolExecutionError(
            "pypdfium2 is missing from this install, so pages cannot be rendered."
        ) from exc
    return pdfium
