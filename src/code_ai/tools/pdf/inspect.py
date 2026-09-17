"""What is inside a PDF, bounded so a 500-page file does not flood the context."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pypdf import PdfReader

from code_ai.core.errors import ToolArgumentError
from code_ai.tools.office.common import parse_page_spec
from code_ai.tools.pdf.common import PAGE_SIZES

MAX_OUTLINE = 150
MAX_FIELDS = 150
MAX_HITS = 60


def open_reader(path: Path, password: str | None) -> PdfReader:
    reader = PdfReader(str(path))
    if reader.is_encrypted:
        # Many PDFs are encrypted with an empty user password: readable, not editable.
        if not reader.decrypt(password or ""):
            raise ToolArgumentError(f"{path.name} is encrypted; pass its 'password'.")
    return reader


def inspect_pdf(
    path: Path,
    *,
    password: str | None,
    text_pages: Any,
    max_chars: int,
    search: str | None,
) -> dict[str, Any]:
    reader = open_reader(path, password)
    total = len(reader.pages)
    report: dict[str, Any] = {
        "pages": total,
        "pdf_version": reader.pdf_header.replace("%PDF-", ""),
        "encrypted": reader.is_encrypted,
        "metadata": _metadata(reader),
        "page_sizes": _page_sizes(reader),
    }
    outline = _outline(reader)
    if outline:
        report["outline"] = outline
    fields = _fields(reader)
    if fields:
        report["form_fields"] = fields
    report.update(_page_objects(reader))
    attachments = list((reader.attachments or {}).keys())
    if attachments:
        report["attachments"] = attachments[:50]

    selected = parse_page_spec(text_pages, total) if text_pages else []
    text = _text(path, password, selected, max_chars)
    if text is not None:
        report["text"] = text
    if search:
        report["search"] = _search(path, password, search)
    if text and all(entry.get("chars", 1) == 0 for entry in text) and not report.get("fonts"):
        report["note"] = "No text layer found: this looks like a scanned PDF (images only)."
    return report


def _metadata(reader: PdfReader) -> dict[str, str]:
    raw = reader.metadata or {}
    return {
        str(key).lstrip("/").lower(): str(value)[:300]
        for key, value in raw.items()
        if value not in (None, "")
    }


def _size_name(width: float, height: float) -> str:
    short, long_ = sorted((width, height))
    for name, (w, h) in PAGE_SIZES.items():
        if abs(short - w) < 3 and abs(long_ - h) < 3:
            orientation = "landscape" if width > height else "portrait"
            return f"{name.upper() if name.startswith('a') else name} {orientation}"
    return "custom"


def _page_sizes(reader: PdfReader) -> list[str]:
    """Runs of identical pages, e.g. '1-10: A4 portrait 595x842pt'."""

    runs: list[tuple[int, int, str]] = []
    for number, page in enumerate(reader.pages, start=1):
        box = page.cropbox
        width, height = float(box.width), float(box.height)
        if page.rotation % 180:
            width, height = height, width
        label = f"{_size_name(width, height)} {width:.0f}x{height:.0f}pt"
        if page.rotation % 360:
            label += f" rotated {page.rotation % 360}"
        if runs and runs[-1][2] == label and runs[-1][1] == number - 1:
            runs[-1] = (runs[-1][0], number, label)
        else:
            runs.append((number, number, label))
    return [f"{a}-{b}: {label}" if a != b else f"{a}: {label}" for a, b, label in runs[:40]]


def _outline(reader: PdfReader) -> list[str]:
    entries: list[str] = []

    def walk(items, depth):
        for item in items:
            if len(entries) >= MAX_OUTLINE:
                return
            if isinstance(item, list):
                walk(item, depth + 1)
                continue
            try:
                page = reader.get_destination_page_number(item) + 1
            except Exception:  # noqa: BLE001 - dangling destinations are common
                page = "?"
            entries.append(f"{'  ' * depth}{item.title} (p. {page})")

    try:
        walk(reader.outline, 0)
    except Exception:  # noqa: BLE001
        return entries
    return entries


def _fields(reader: PdfReader) -> list[dict[str, Any]]:
    try:
        fields = reader.get_fields() or {}
    except Exception:  # noqa: BLE001
        return []
    kinds = {"/Tx": "text", "/Btn": "button/checkbox", "/Ch": "choice", "/Sig": "signature"}
    listed = []
    for name, info in list(fields.items())[:MAX_FIELDS]:
        entry: dict[str, Any] = {"name": name, "type": kinds.get(info.get("/FT"), "unknown")}
        value = info.get("/V")
        if value not in (None, ""):
            entry["value"] = str(value)[:200]
        states = info.get("/_States_")
        if states:
            entry["options"] = [str(s) for s in states]
        options = info.get("/Opt")
        if options:
            entry["options"] = [str(o[-1] if isinstance(o, list) else o) for o in options][:50]
        listed.append(entry)
    return listed


def _page_objects(reader: PdfReader) -> dict[str, Any]:
    annotations: dict[str, int] = {}
    fonts: set[str] = set()
    images_per_page: dict[int, int] = {}
    for number, page in enumerate(reader.pages, start=1):
        for annotation in page.get("/Annots") or []:
            try:
                subtype = str(annotation.get_object().get("/Subtype", "?")).lstrip("/")
            except Exception:  # noqa: BLE001
                continue
            annotations[subtype] = annotations.get(subtype, 0) + 1
        resources = page.get("/Resources")
        if resources is None:
            continue
        resources = resources.get_object()
        for font in (resources.get("/Font") or {}).values():
            try:
                fonts.add(str(font.get_object().get("/BaseFont", "?")).lstrip("/").split("+")[-1])
            except Exception:  # noqa: BLE001
                continue
        count = 0
        for xobject in (resources.get("/XObject") or {}).values():
            try:
                if xobject.get_object().get("/Subtype") == "/Image":
                    count += 1
            except Exception:  # noqa: BLE001
                continue
        if count:
            images_per_page[number] = count
    result: dict[str, Any] = {"fonts": sorted(fonts)[:60]}
    if annotations:
        result["annotations"] = annotations
    if images_per_page:
        result["images"] = {
            "total": sum(images_per_page.values()),
            "pages_with_images": len(images_per_page),
        }
    return result


def _text(path: Path, password: str | None, pages: list[int], max_chars: int) -> list[dict] | None:
    if not pages:
        return None
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(str(path), password=password)
    budget = max_chars
    out: list[dict[str, Any]] = []
    try:
        for page_index in pages:
            if budget <= 0:
                out.append({"truncated": f"character budget reached before page {page_index + 1}"})
                break
            page = document[page_index]
            text_page = page.get_textpage()
            try:
                content = text_page.get_text_range().replace("\r\n", "\n").strip()
            finally:
                text_page.close()
                page.close()
            entry: dict[str, Any] = {"page": page_index + 1, "chars": len(content)}
            if len(content) > budget:
                content = content[:budget] + " ...[truncated]"
            entry["text"] = content
            budget -= len(content)
            out.append(entry)
    finally:
        document.close()
    return out


def _search(path: Path, password: str | None, term: str) -> dict[str, Any]:
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(str(path), password=password)
    hits: list[dict[str, Any]] = []
    total = 0
    try:
        for page_index in range(len(document)):
            page = document[page_index]
            text_page = page.get_textpage()
            try:
                searcher = text_page.search(term, match_case=False)
                while (occurrence := searcher.get_next()) is not None:
                    total += 1
                    if len(hits) >= MAX_HITS:
                        continue
                    start, count = occurrence
                    before = text_page.get_text_range(max(0, start - 60), min(start, 60))
                    after = text_page.get_text_range(start + count, 60)
                    match = text_page.get_text_range(start, count)
                    left, bottom, right, top = _rect(text_page, start, count)
                    height = page.get_height()
                    hits.append(
                        {
                            "page": page_index + 1,
                            "context": " ".join(f"{before}[{match}]{after}".split()),
                            "rect_top_left": [
                                round(left, 1),
                                round(height - top, 1),
                                round(right, 1),
                                round(height - bottom, 1),
                            ],
                        }
                    )
                searcher.close()
            finally:
                text_page.close()
                page.close()
    finally:
        document.close()
    return {"term": term, "matches": total, "hits": hits}


def _rect(text_page, start: int, count: int) -> tuple[float, float, float, float]:
    rects = [text_page.get_rect(i) for i in range(text_page.count_rects(start, count))]
    if not rects:
        return 0.0, 0.0, 0.0, 0.0
    return (
        min(r[0] for r in rects),
        min(r[1] for r in rects),
        max(r[2] for r in rects),
        max(r[3] for r in rects),
    )
