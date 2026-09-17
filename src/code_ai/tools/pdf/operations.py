"""The operations pdf_edit applies, in order, to one document held in memory.

Positions are in points from the top-left corner of the page as a viewer shows
it, the way a rendered page image reads. Pages are one-based.
"""

from __future__ import annotations

import io
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from pypdf import PageObject, PdfReader, PdfWriter, Transformation
from pypdf.annotations import Link
from pypdf.constants import UserAccessPermissions
from pypdf.generic import NameObject, RectangleObject

from code_ai.core.errors import ToolArgumentError, ToolExecutionError
from code_ai.tools.office.common import parse_page_spec
from code_ai.tools.pdf.common import font_for, page_size, rgb, to_points
from code_ai.tools.pdf.overlay import (
    anchor,
    draw_text_block,
    image_reader,
    overlay_onto,
    text_width,
    visible_box,
)

ALIASES = {
    "merge": "append",
    "select": "keep",
    "extract": "keep",
    "reorder": "keep",
    "remove": "delete",
    "delete_pages": "delete",
    "insert_blank": "blank",
    "stamp_text": "stamp",
    "stamp_image": "stamp",
    "watermark_text": "watermark",
    "watermark_image": "watermark",
    "header": "header_footer",
    "footer": "header_footer",
    "set_metadata": "metadata",
    "outline": "bookmarks",
    "add_link": "link",
    "fill": "fill_form",
    "optimize": "compress",
}


@dataclass
class EditState:
    writer: PdfWriter
    resolve_file: Callable[[str], Path]
    resolve_output: Callable[[str], Path]
    display: Callable[[Path], str]
    title: str = ""
    encryption: dict[str, Any] | None = None
    written: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.writer.pages)

    def pages(self, spec: Any, index: int) -> list[int]:
        try:
            return parse_page_spec(spec, self.total)
        except ToolArgumentError as exc:
            raise ToolArgumentError(f"operation {index}: {exc}") from None


def normalize(operations: Any) -> list[dict[str, Any]]:
    """Check each operation's shape before any page is touched."""

    if isinstance(operations, dict):
        operations = [operations]
    if not isinstance(operations, list) or not operations:
        raise ToolArgumentError("'operations' must be a non-empty JSON array of objects.")
    normalized = []
    for index, raw in enumerate(operations, start=1):
        if not isinstance(raw, dict):
            raise ToolArgumentError(f"operation {index} must be an object with an 'op' key.")
        spec = dict(raw)
        name = str(spec.get("op") or spec.get("type") or "").strip().lower()
        name = ALIASES.get(name, name)
        if name not in HANDLERS:
            raise ToolArgumentError(
                f"operation {index}: unknown op {raw.get('op')!r}. Known: {sorted(HANDLERS)}."
            )
        spec["op"] = name
        for key in REQUIRED.get(name, ()):
            if isinstance(key, tuple):
                if not any(spec.get(k) not in (None, "", []) for k in key):
                    raise ToolArgumentError(
                        f"operation {index} ({name}) needs one of: {', '.join(key)}."
                    )
            elif spec.get(key) in (None, "", []):
                raise ToolArgumentError(f"operation {index} ({name}) needs '{key}'.")
        normalized.append(spec)
    return normalized


def apply(state: EditState, operations: list[dict[str, Any]]) -> list[str]:
    done = []
    for index, spec in enumerate(operations, start=1):
        try:
            done.append(f"{index}. {HANDLERS[spec['op']](state, spec, index)}")
        except (ToolArgumentError, ToolExecutionError):
            raise
        except Exception as exc:  # noqa: BLE001 - pypdf raises many types on odd files
            raise ToolExecutionError(f"operation {index} ({spec['op']}) failed: {exc}") from exc
    return done


def save(state: EditState, target: Path) -> None:
    if state.encryption is not None:
        state.writer.encrypt(**state.encryption)
    with target.open("wb") as stream:
        state.writer.write(stream)


# -- page structure ----------------------------------------------------------------


def _reader(state: EditState, spec: dict[str, Any], index: int) -> PdfReader:
    path = state.resolve_file(str(spec["file"]))
    reader = PdfReader(str(path))
    if reader.is_encrypted:
        password = spec.get("password")
        if not password or not reader.decrypt(str(password)):
            raise ToolArgumentError(
                f"operation {index}: {path.name} is encrypted; give its 'password'."
            )
    return reader


def _append(state: EditState, spec: dict[str, Any], index: int) -> str:
    reader = _reader(state, spec, index)
    pages = parse_page_spec(spec.get("pages"), len(reader.pages))
    for page_index in pages:
        state.writer.add_page(reader.pages[page_index])
    return f"appended {len(pages)} page(s) from {spec['file']}"


def _insert(state: EditState, spec: dict[str, Any], index: int) -> str:
    reader = _reader(state, spec, index)
    at = _position(state, spec.get("at"), index)
    pages = parse_page_spec(spec.get("pages"), len(reader.pages))
    for offset, page_index in enumerate(pages):
        state.writer.insert_page(reader.pages[page_index], at + offset)
    return f"inserted {len(pages)} page(s) from {spec['file']} at page {at + 1}"


def _position(state: EditState, value: Any, index: int) -> int:
    """Zero-based insertion point from a one-based page number or 'end'."""

    if value is None or str(value).strip().lower() in {"end", "last+1", ""}:
        return state.total
    if str(value).strip().lower() in {"start", "first", "begin"}:
        return 0
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ToolArgumentError(
            f"operation {index}: 'at' must be a page number or 'end'."
        ) from None
    if not 1 <= number <= state.total + 1:
        raise ToolArgumentError(f"operation {index}: 'at' must be between 1 and {state.total + 1}.")
    return number - 1


def _keep(state: EditState, spec: dict[str, Any], index: int) -> str:
    wanted = state.pages(spec["pages"], index)
    pages = list(state.writer.pages)
    # Repeated pages need their own objects, or both copies share one annotation list.
    seen: set[int] = set()
    chosen = []
    for page_index in wanted:
        page = pages[page_index]
        if page_index in seen:
            page = _copy_page(page)
        seen.add(page_index)
        chosen.append(page)
    for position in range(state.total - 1, -1, -1):
        state.writer.remove_page(position)
    for page in chosen:
        state.writer.add_page(page)
    return f"kept {len(chosen)} page(s) in the order given"


def _copy_page(page: PageObject) -> PageObject:
    blank = PageObject.create_blank_page(width=page.mediabox.width, height=page.mediabox.height)
    blank.mediabox = page.mediabox
    blank.cropbox = page.cropbox
    blank.merge_page(page)
    return blank


def _delete(state: EditState, spec: dict[str, Any], index: int) -> str:
    doomed = sorted(set(state.pages(spec["pages"], index)), reverse=True)
    if len(doomed) >= state.total:
        raise ToolArgumentError(f"operation {index}: deleting every page leaves an empty PDF.")
    for page_index in doomed:
        state.writer.remove_page(page_index)
    return f"deleted {len(doomed)} page(s)"


def _rotate(state: EditState, spec: dict[str, Any], index: int) -> str:
    try:
        degrees = int(spec.get("degrees", 90))
    except (TypeError, ValueError):
        raise ToolArgumentError(f"operation {index}: 'degrees' must be 90, 180 or 270.") from None
    if degrees % 90:
        raise ToolArgumentError(f"operation {index}: 'degrees' must be a multiple of 90.")
    pages = state.pages(spec.get("pages"), index)
    for page_index in pages:
        state.writer.pages[page_index].rotate(degrees)
    return f"rotated {len(pages)} page(s) by {degrees} degrees"


def _upright(page: PageObject) -> PageObject:
    """Bake /Rotate into the content, so top-left coordinates mean what a viewer shows."""

    if page.rotation % 360:
        page.transfer_rotation_to_content()
    return page


def _crop(state: EditState, spec: dict[str, Any], index: int) -> str:
    pages = state.pages(spec.get("pages"), index)
    margins = spec.get("margins")
    box = spec.get("box")
    if margins is None and box is None:
        raise ToolArgumentError(f"operation {index} (crop) needs 'margins' or 'box'.")
    for page_index in pages:
        page = _upright(state.writer.pages[page_index])
        x0, y0, width, height = visible_box(page)
        if box is not None:
            if not isinstance(box, list) or len(box) != 4:
                raise ToolArgumentError(
                    f"operation {index}: 'box' is [left, top, right, bottom] from the top-left."
                )
            left, top, right, bottom = (to_points(v, "box") for v in box)
            rect = RectangleObject([x0 + left, y0 + height - bottom, x0 + right, y0 + height - top])
        else:
            m = _margins(margins, index)
            rect = RectangleObject(
                [x0 + m["left"], y0 + m["bottom"], x0 + width - m["right"], y0 + height - m["top"]]
            )
        if rect.width <= 1 or rect.height <= 1:
            raise ToolArgumentError(
                f"operation {index}: the crop leaves nothing of page {page_index + 1}."
            )
        page.mediabox = rect
        page.cropbox = rect
    return f"cropped {len(pages)} page(s)"


def _margins(value: Any, index: int) -> dict[str, float]:
    if isinstance(value, int | float | str):
        side = to_points(value, "margins")
        return {"top": side, "right": side, "bottom": side, "left": side}
    if not isinstance(value, dict):
        raise ToolArgumentError(
            f"operation {index}: 'margins' is a length or {{top, right, bottom, left}}."
        )
    sides = ("top", "right", "bottom", "left")
    return {key: to_points(value.get(key, 0), f"margins.{key}") for key in sides}


def _resize(state: EditState, spec: dict[str, Any], index: int) -> str:
    target_w, target_h = page_size(spec.get("size", "a4"))
    pages = state.pages(spec.get("pages"), index)
    for page_index in pages:
        source = _upright(state.writer.pages[page_index])
        x0, y0, width, height = visible_box(source)
        w, h = target_w, target_h
        # Keep each page's orientation unless the caller insists.
        if not spec.get("keep_orientation_of_size") and (width > height) != (w > h):
            w, h = h, w
        factor = min(w / width, h / height)
        dx = (w - width * factor) / 2
        dy = (h - height * factor) / 2
        fresh = PageObject.create_blank_page(width=w, height=h)
        fresh.merge_transformed_page(
            source, Transformation().translate(-x0, -y0).scale(factor, factor).translate(dx, dy)
        )
        state.writer.insert_page(fresh, page_index)
        state.writer.remove_page(page_index + 1)
    return f"resized {len(pages)} page(s) to {target_w:.0f}x{target_h:.0f}pt, content scaled to fit"


def _blank(state: EditState, spec: dict[str, Any], index: int) -> str:
    at = _position(state, spec.get("at"), index)
    count = int(spec.get("count") or 1)
    if spec.get("size"):
        width, height = page_size(spec["size"])
    elif state.total:
        reference = state.writer.pages[min(at, state.total - 1)]
        width, height = float(reference.mediabox.width), float(reference.mediabox.height)
    else:
        width, height = page_size("a4")
    for offset in range(count):
        state.writer.insert_blank_page(width=width, height=height, index=at + offset)
    return f"inserted {count} blank page(s) at page {at + 1}"


def _split(state: EditState, spec: dict[str, Any], index: int) -> str:
    pattern = str(spec.get("output") or "part-{n}.pdf")
    if "{n}" not in pattern:
        raise ToolArgumentError(f"operation {index}: split 'output' needs a {{n}} placeholder.")
    groups: list[list[int]] = []
    if spec.get("ranges"):
        ranges = spec["ranges"]
        if not isinstance(ranges, list):
            raise ToolArgumentError(f"operation {index}: 'ranges' is a list like ['1-3', '4-6'].")
        groups = [state.pages(item, index) for item in ranges]
    else:
        every = int(spec.get("every") or 1)
        if every < 1:
            raise ToolArgumentError(f"operation {index}: 'every' must be at least 1.")
        groups = [list(range(s, min(s + every, state.total))) for s in range(0, state.total, every)]
    buffer = io.BytesIO()
    state.writer.write(buffer)
    source = PdfReader(io.BytesIO(buffer.getvalue()))
    for number, group in enumerate(groups, start=1):
        part = PdfWriter()
        for page_index in group:
            part.add_page(source.pages[page_index])
        target = state.resolve_output(pattern.replace("{n}", str(number)))
        tmp = target.with_name(target.name + ".tmp")
        with tmp.open("wb") as stream:
            part.write(stream)
        tmp.replace(target)
        state.written.append(state.display(target))
    return f"split into {len(groups)} file(s)"


# -- drawing -------------------------------------------------------------------------


def _opacity(spec: dict[str, Any], default: float) -> float:
    try:
        return max(0.0, min(1.0, float(spec.get("opacity", default))))
    except (TypeError, ValueError):
        raise ToolArgumentError("'opacity' must be a number between 0 and 1.") from None


def _font_size(spec: dict[str, Any], default: float) -> float:
    try:
        return max(2.0, min(400.0, float(spec.get("font_size", default))))
    except (TypeError, ValueError):
        raise ToolArgumentError("'font_size' must be a number.") from None


def _font(state: EditState, spec: dict[str, Any], text: str) -> str:
    font_file = spec.get("font_file")
    return font_for(
        text, spec.get("font"), state.resolve_file(str(font_file)) if font_file else None
    )


def _watermark(state: EditState, spec: dict[str, Any], index: int) -> str:
    pages = state.pages(spec.get("pages"), index)
    opacity = _opacity(spec, 0.15)
    under = bool(spec.get("under", False))
    angle = float(spec.get("angle", 45))
    if spec.get("image"):
        reader, (px_w, px_h) = image_reader(state.resolve_file(str(spec["image"])), opacity)
        scale = float(spec.get("scale", 0.6))

        def draw(canvas, width, height):
            fit = min(width * scale / px_w, height * scale / px_h)
            w, h = px_w * fit, px_h * fit
            canvas.saveState()
            canvas.translate(width / 2, height / 2)
            canvas.rotate(angle if spec.get("angle") is not None else 0)
            canvas.drawImage(reader, -w / 2, -h / 2, w, h, mask="auto")
            canvas.restoreState()

        what = f"image {spec['image']}"
    else:
        text = str(spec["text"])
        lines = text.splitlines() or [text]
        font = _font(state, spec, text)
        color = rgb(spec.get("color", "#808080"))

        def draw(canvas, width, height):
            size = spec.get("font_size")
            if size is None:
                # Largest size whose rotated text box still fits 85% of the page.
                cos, sin = abs(math.cos(math.radians(angle))), abs(math.sin(math.radians(angle)))
                run, rise = max(text_width(lines, font, 1.0), 1.0), 1.2 * len(lines)
                size = min(
                    0.85 * width / (run * cos + rise * sin),
                    0.85 * height / (run * sin + rise * cos),
                )
                size = max(8.0, min(200.0, size))
            size = float(size)
            canvas.saveState()
            canvas.translate(width / 2, height / 2)
            canvas.rotate(angle)
            block = size * 1.2 * len(lines)
            draw_text_block(
                canvas,
                lines,
                x=-width,
                y_top=block / 2 + size * 0.2,
                font=font,
                size=size,
                color=color,
                opacity=opacity,
                align="center",
                width=2 * width,
            )
            canvas.restoreState()

        what = f"text {text!r}"
    for page_index in pages:
        overlay_onto(_upright(state.writer.pages[page_index]), draw, under=under)
    return f"watermarked {len(pages)} page(s) with {what}"


def _stamp(state: EditState, spec: dict[str, Any], index: int) -> str:
    pages = state.pages(spec.get("pages"), index)
    opacity = _opacity(spec, 1.0)
    margin = to_points(spec.get("margin", "1cm"), "margin")
    position = str(spec.get("position") or "top-right")
    explicit = spec.get("x") is not None and spec.get("y") is not None
    total = state.total

    if spec.get("image"):
        reader, (px_w, px_h) = image_reader(state.resolve_file(str(spec["image"])), opacity)
        if spec.get("width") is not None:
            w = to_points(spec["width"], "width")
            h = to_points(spec["height"], "height") if spec.get("height") else w * px_h / px_w
        elif spec.get("height") is not None:
            h = to_points(spec["height"], "height")
            w = h * px_w / px_h
        else:
            w, h = px_w * 0.75, px_h * 0.75

        def draw_for(_page_number):
            def draw(canvas, width, height):
                if explicit:
                    x = to_points(spec["x"], "x")
                    y = height - to_points(spec["y"], "y") - h
                else:
                    x, y = anchor(position, width, height, w, h, margin)
                canvas.drawImage(reader, x, y, w, h, mask="auto")

            return draw

        what = f"image {spec['image']}"
    else:
        template = str(spec["text"])
        size = _font_size(spec, 12)
        color = rgb(spec.get("color", "#000000"))
        align = str(spec.get("align") or "left").lower()
        font = _font(state, spec, template)

        def draw_for(page_number):
            lines = _fill(template, page_number, total, state.title).splitlines() or [""]

            def draw(canvas, width, height):
                block_w = text_width(lines, font, size)
                block_h = size * 1.2 * len(lines)
                if explicit:
                    x = to_points(spec["x"], "x")
                    top = height - to_points(spec["y"], "y")
                    if align == "right":
                        x -= block_w
                    elif align == "center":
                        x -= block_w / 2
                else:
                    x, bottom = anchor(position, width, height, block_w, block_h, margin)
                    top = bottom + block_h
                text_align = "right" if "right" in position and not explicit else "left"
                draw_text_block(
                    canvas,
                    lines,
                    x=x,
                    y_top=top,
                    font=font,
                    size=size,
                    color=color,
                    opacity=opacity,
                    align=text_align,
                    width=block_w,
                )

            return draw

        what = f"text {template!r}"
    for page_index in pages:
        page = _upright(state.writer.pages[page_index])
        overlay_onto(page, draw_for(page_index + 1), under=bool(spec.get("under", False)))
    return f"stamped {len(pages)} page(s) with {what}"


def _fill(template: str, page: int, total: int, title: str) -> str:
    return (
        template.replace("{page}", str(page))
        .replace("{total}", str(total))
        .replace("{title}", title)
        .replace("{date}", date.today().strftime("%d/%m/%Y"))
    )


def _header_footer(state: EditState, spec: dict[str, Any], index: int) -> str:
    if spec.get("op") == "header_footer" and not (spec.get("header") or spec.get("footer")):
        raise ToolArgumentError(f"operation {index}: give 'header' and/or 'footer' text.")
    pages = state.pages(spec.get("pages"), index)
    size = _font_size(spec, 9)
    color = rgb(spec.get("color", "#555555"))
    margin = to_points(spec.get("margin", "1.2cm"), "margin")
    align = str(spec.get("align") or "center").lower()
    if align not in {"left", "center", "right"}:
        raise ToolArgumentError(f"operation {index}: 'align' is left, center or right.")
    start = int(spec.get("start_number") or 1)
    skip_first = bool(spec.get("skip_first", False))
    numbered_total = len(pages) + start - 1
    header, footer = spec.get("header"), spec.get("footer")
    font = _font(state, spec, f"{header or ''}{footer or ''}")
    stamped = 0
    for order, page_index in enumerate(pages):
        if skip_first and order == 0:
            continue
        number = order + start

        def draw(canvas, width, height, number=number):
            for text, top in ((header, height - margin), (footer, margin + size * 1.2)):
                if not text:
                    continue
                lines = _fill(str(text), number, numbered_total, state.title).splitlines()
                draw_text_block(
                    canvas,
                    lines,
                    x=margin,
                    y_top=top,
                    font=font,
                    size=size,
                    color=color,
                    opacity=1.0,
                    align=align,
                    width=width - 2 * margin,
                )

        overlay_onto(_upright(state.writer.pages[page_index]), draw)
        stamped += 1
    return f"added header/footer to {stamped} page(s)"


def _page_numbers(state: EditState, spec: dict[str, Any], index: int) -> str:
    spec = dict(spec)
    template = str(spec.get("format") or "{page} / {total}")
    where = str(spec.get("position") or "bottom-center").lower()
    spec["align"] = "left" if "left" in where else "right" if "right" in where else "center"
    spec["header" if where.startswith("top") else "footer"] = template
    return _header_footer(state, spec, index)


# -- document level ------------------------------------------------------------------


def _fill_form(state: EditState, spec: dict[str, Any], index: int) -> str:
    fields = spec.get("fields")
    if not isinstance(fields, dict) or not fields:
        raise ToolArgumentError(f"operation {index}: 'fields' is an object of name: value.")
    known = state.writer.get_fields() or {}
    unknown = [name for name in fields if name not in known]
    if unknown:
        listed = ", ".join(sorted(known)[:40]) or "none - this PDF has no form"
        raise ToolArgumentError(f"operation {index}: unknown field(s) {unknown}. Fields: {listed}.")
    values = {name: _field_value(known[name], value) for name, value in fields.items()}
    flatten = bool(spec.get("flatten", False))
    state.writer.set_need_appearances_writer(True)
    for page in state.writer.pages:
        if "/Annots" in page:
            state.writer.update_page_form_field_values(
                page, values, auto_regenerate=False, flatten=flatten
            )
    if flatten:
        for page in state.writer.pages:
            _drop_widgets(page)
        if "/AcroForm" in state.writer._root_object:
            del state.writer._root_object["/AcroForm"]
    return f"filled {len(values)} field(s)" + (" and flattened the form" if flatten else "")


def _field_value(field_info: Any, value: Any) -> str:
    if isinstance(value, bool):
        # Checkboxes take their "on" appearance name, usually /Yes or /On.
        states = [s for s in (field_info.get("/_States_") or []) if s != "/Off"]
        return (states[0] if states else "/Yes") if value else "/Off"
    return str(value)


def _drop_widgets(page: PageObject) -> None:
    annotations = page.get("/Annots")
    if not annotations:
        return
    kept = [a for a in annotations if a.get_object().get("/Subtype") != "/Widget"]
    if kept:
        from pypdf.generic import ArrayObject

        page[NameObject("/Annots")] = ArrayObject(kept)
    else:
        del page["/Annots"]


def _metadata(state: EditState, spec: dict[str, Any], index: int) -> str:
    keys = {
        "title": "/Title",
        "author": "/Author",
        "subject": "/Subject",
        "keywords": "/Keywords",
        "creator": "/Creator",
        "producer": "/Producer",
    }
    values = {pdf_key: str(spec[key]) for key, pdf_key in keys.items() if spec.get(key) is not None}
    if not values:
        raise ToolArgumentError(f"operation {index}: give at least one of {sorted(keys)}.")
    state.writer.add_metadata(values)
    if "/Title" in values:
        state.title = values["/Title"]
    return f"set metadata: {', '.join(k.lstrip('/').lower() for k in values)}"


def _bookmarks(state: EditState, spec: dict[str, Any], index: int) -> str:
    items = spec.get("items")
    if not isinstance(items, list) or not items:
        raise ToolArgumentError(
            f"operation {index}: 'items' is a list of {{title, page, level}} (level 1 = top)."
        )
    if spec.get("replace", True) and "/Outlines" in state.writer._root_object:
        del state.writer._root_object["/Outlines"]
    parents: dict[int, Any] = {}
    for number, item in enumerate(items, start=1):
        if not isinstance(item, dict) or not item.get("title") or item.get("page") is None:
            raise ToolArgumentError(f"operation {index}: bookmark {number} needs title and page.")
        page_index = state.pages(item["page"], index)[0]
        level = max(1, int(item.get("level") or 1))
        parent = parents.get(level - 1) if level > 1 else None
        parents[level] = state.writer.add_outline_item(
            str(item["title"]), page_index, parent=parent, bold=bool(item.get("bold"))
        )
        for deeper in [key for key in parents if key > level]:
            del parents[deeper]
    return f"wrote {len(items)} bookmark(s)"


def _link(state: EditState, spec: dict[str, Any], index: int) -> str:
    page_index = state.pages(spec["page"], index)[0]
    rect = spec.get("rect")
    if not isinstance(rect, list) or len(rect) != 4:
        raise ToolArgumentError(
            f"operation {index}: 'rect' is [left, top, right, bottom] in points from the top-left."
        )
    page = _upright(state.writer.pages[page_index])
    x0, y0, _, height = visible_box(page)
    left, top, right, bottom = (to_points(v, "rect") for v in rect)
    box = (x0 + left, y0 + height - bottom, x0 + right, y0 + height - top)
    if spec.get("url"):
        annotation = Link(rect=box, url=str(spec["url"]))
        target = spec["url"]
    elif spec.get("to_page") is not None:
        destination = state.pages(spec["to_page"], index)[0]
        annotation = Link(rect=box, target_page_index=destination)
        target = f"page {destination + 1}"
    else:
        raise ToolArgumentError(f"operation {index}: a link needs 'url' or 'to_page'.")
    state.writer.add_annotation(page_index, annotation)
    return f"linked an area of page {page_index + 1} to {target}"


def _encrypt(state: EditState, spec: dict[str, Any], index: int) -> str:
    permissions = UserAccessPermissions(0)
    P = UserAccessPermissions
    grants = {
        "allow_print": P.PRINT | P.PRINT_TO_REPRESENTATION,
        "allow_copy": P.EXTRACT | P.EXTRACT_TEXT_AND_GRAPHICS,
        "allow_modify": P.MODIFY | P.ASSEMBLE_DOC,
        "allow_annotate": P.ADD_OR_MODIFY | P.FILL_FORM_FIELDS,
    }
    defaults = {
        "allow_print": True,
        "allow_copy": True,
        "allow_modify": False,
        "allow_annotate": True,
    }
    for key, flag in grants.items():
        if spec.get(key, defaults[key]):
            permissions |= flag
    state.encryption = {
        "user_password": str(spec["user_password"]),
        "owner_password": str(spec.get("owner_password") or spec["user_password"]),
        "permissions_flag": permissions,
        "algorithm": "AES-256",
    }
    return "will encrypt with AES-256 on save"


def _compress(state: EditState, spec: dict[str, Any], index: int) -> str:
    quality = spec.get("image_quality")
    max_side = spec.get("max_image_side")
    recompressed = 0
    if quality is not None or max_side is not None:
        from PIL import Image

        quality = int(quality or 80)
        for page in state.writer.pages:
            for image_file in page.images:
                try:
                    image = image_file.image
                    if image is None:
                        continue
                    if max_side and max(image.size) > int(max_side):
                        image = image.copy()
                        image.thumbnail((int(max_side), int(max_side)), Image.Resampling.LANCZOS)
                    if image.mode not in {"RGB", "L"}:
                        # Keeping transparency means keeping it lossless.
                        continue
                    image_file.replace(image, quality=quality)
                    recompressed += 1
                except Exception:  # noqa: BLE001 - odd colour spaces stay as they were
                    continue
    for page in state.writer.pages:
        page.compress_content_streams()
    state.writer.compress_identical_objects(remove_duplicates=True, remove_unreferenced=True)
    extra = f", recompressed {recompressed} image(s)" if recompressed else ""
    return f"compressed content streams and merged duplicate objects{extra}"


def _remove_annotations(state: EditState, spec: dict[str, Any], index: int) -> str:
    subtypes = spec.get("subtypes")
    if subtypes:
        if not isinstance(subtypes, list):
            subtypes = [subtypes]
        subtypes = ["/" + str(s).lstrip("/").capitalize() for s in subtypes]
    state.writer.remove_annotations(subtypes=subtypes)
    return "removed annotations" + (f" of type {', '.join(subtypes)}" if subtypes else "")


def _remove_javascript(state: EditState, spec: dict[str, Any], index: int) -> str:
    root = state.writer._root_object
    removed = 0
    names = root.get("/Names")
    if names is not None and "/JavaScript" in names.get_object():
        del names.get_object()["/JavaScript"]
        removed += 1
    for key in ("/OpenAction", "/AA"):
        if key in root:
            del root[key]
            removed += 1
    for page in state.writer.pages:
        if "/AA" in page:
            del page["/AA"]
            removed += 1
    return f"removed {removed} script/action entr{'y' if removed == 1 else 'ies'}"


def _redact(state: EditState, spec: dict[str, Any], index: int) -> str:
    from code_ai.tools.pdf.redact import redact

    return redact(state, spec, index)


HANDLERS: dict[str, Callable[[EditState, dict[str, Any], int], str]] = {
    "append": _append,
    "insert": _insert,
    "keep": _keep,
    "delete": _delete,
    "rotate": _rotate,
    "crop": _crop,
    "resize": _resize,
    "blank": _blank,
    "split": _split,
    "watermark": _watermark,
    "stamp": _stamp,
    "header_footer": _header_footer,
    "page_numbers": _page_numbers,
    "fill_form": _fill_form,
    "metadata": _metadata,
    "bookmarks": _bookmarks,
    "link": _link,
    "encrypt": _encrypt,
    "compress": _compress,
    "redact": _redact,
    "remove_annotations": _remove_annotations,
    "remove_javascript": _remove_javascript,
}

REQUIRED: dict[str, tuple[Any, ...]] = {
    "append": ("file",),
    "insert": ("file",),
    "keep": ("pages",),
    "delete": ("pages",),
    "watermark": (("text", "image"),),
    "stamp": (("text", "image"),),
    "fill_form": ("fields",),
    "bookmarks": ("items",),
    "link": ("page", "rect"),
    "encrypt": ("user_password",),
    "redact": (("areas", "text"),),
}
