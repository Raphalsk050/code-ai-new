"""Redaction that cannot be undone by selecting the text under a black box.

Drawing a rectangle over text leaves the text in the file. Affected pages are
rasterised instead, so the covered content is gone - and so is the selectable
text on those pages, which the result says.
"""

from __future__ import annotations

import io
from typing import Any

from pypdf import PdfReader
from pypdf.generic import ArrayObject, NameObject

from code_ai.core.errors import ToolArgumentError
from code_ai.tools.pdf.common import rgb, to_points
from code_ai.tools.pdf.overlay import visible_box

_PADDING_PT = 1.5


def redact(state, spec: dict[str, Any], index: int) -> str:
    import pypdfium2 as pdfium
    from PIL import ImageDraw
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas as rl_canvas

    dpi = max(72, min(600, int(spec.get("dpi") or 200)))
    fill = tuple(int(c * 255) for c in rgb(spec.get("color", "#000000")))

    # Rects per page, in points from the top-left of the visible box.
    boxes: dict[int, list[tuple[float, float, float, float]]] = {}
    for number, area in enumerate(spec.get("areas") or [], start=1):
        rect = area.get("rect") if isinstance(area, dict) else None
        if not isinstance(rect, list) or len(rect) != 4 or area.get("page") is None:
            raise ToolArgumentError(
                f"operation {index}: area {number} is {{page, rect: [left, top, right, bottom]}}."
            )
        page_index = state.pages(area["page"], index)[0]
        boxes.setdefault(page_index, []).append(tuple(to_points(v, "rect") for v in rect))

    for page in state.writer.pages:
        if page.rotation % 360:
            page.transfer_rotation_to_content()

    buffer = io.BytesIO()
    state.writer.write(buffer)
    document = pdfium.PdfDocument(buffer.getvalue())
    document.init_forms()
    missing: list[str] = []
    try:
        terms = spec.get("text") or []
        if isinstance(terms, str):
            terms = [terms]
        scope = state.pages(spec.get("pages"), index)
        for term in terms:
            found = 0
            for page_index in scope:
                page = document[page_index]
                text_page = page.get_textpage()
                try:
                    found += _find(text_page, str(term), page_index, state, boxes)
                finally:
                    text_page.close()
                    page.close()
            if not found:
                missing.append(str(term))

        scale = dpi / 72.0
        for page_index, rects in sorted(boxes.items()):
            page = document[page_index]
            try:
                image = page.render(scale=scale, may_draw_forms=True).to_pil().convert("RGB")
            finally:
                page.close()
            draw = ImageDraw.Draw(image)
            for left, top, right, bottom in rects:
                draw.rectangle(
                    [
                        (left - _PADDING_PT) * scale,
                        (top - _PADDING_PT) * scale,
                        (right + _PADDING_PT) * scale,
                        (bottom + _PADDING_PT) * scale,
                    ],
                    fill=fill,
                )
            _, _, width, height = visible_box(state.writer.pages[page_index])
            out = io.BytesIO()
            canvas = rl_canvas.Canvas(out, pagesize=(width, height))
            canvas.drawImage(ImageReader(image), 0, 0, width, height)
            canvas.showPage()
            canvas.save()
            flat = PdfReader(io.BytesIO(out.getvalue())).pages[0]
            # Links survive; form widgets are already burnt into the image.
            kept = [
                annotation
                for annotation in state.writer.pages[page_index].get("/Annots") or []
                if annotation.get_object().get("/Subtype") != "/Widget"
            ]
            if kept:
                flat[NameObject("/Annots")] = ArrayObject(kept)
            state.writer.insert_page(flat, page_index)
            state.writer.remove_page(page_index + 1)
    finally:
        document.close()

    redacted = sum(len(rects) for rects in boxes.values())
    pages = sorted(page + 1 for page in boxes)
    if pages:
        state.notes.append(
            f"Redacted page(s) {pages} were flattened to {dpi} dpi images: "
            "their text is no longer selectable or searchable."
        )
    if missing:
        state.notes.append(f"Not found, nothing redacted for: {missing}")
    return f"redacted {redacted} area(s) on {len(pages)} page(s)"


def _find(text_page, term: str, page_index: int, state, boxes) -> int:
    """Add a top-left rect for every occurrence of ``term``; returns how many were found."""

    x0, y0, _, height = visible_box(state.writer.pages[page_index])
    top_edge = y0 + height
    searcher = text_page.search(term, match_case=False, match_whole_word=False)
    found = 0
    try:
        while True:
            occurrence = searcher.get_next()
            if not occurrence:
                break
            start, count = occurrence
            found += 1
            for rect_index in range(text_page.count_rects(start, count)):
                left, bottom, right, top = text_page.get_rect(rect_index)
                boxes.setdefault(page_index, []).append(
                    (left - x0, top_edge - top, right - x0, top_edge - bottom)
                )
    finally:
        searcher.close()
    return found
