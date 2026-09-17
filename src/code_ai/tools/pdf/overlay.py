"""Drawing on top of (or under) existing pages: reportlab draws, pypdf merges."""

from __future__ import annotations

import io
from collections.abc import Callable
from pathlib import Path

from pypdf import PageObject, PdfReader, Transformation

from code_ai.core.errors import ToolArgumentError

POSITIONS = (
    "center",
    "top-left",
    "top",
    "top-right",
    "left",
    "right",
    "bottom-left",
    "bottom",
    "bottom-right",
)


def visible_box(page: PageObject) -> tuple[float, float, float, float]:
    """x0, y0, width, height of what a viewer shows."""

    box = page.cropbox
    return float(box.left), float(box.bottom), float(box.width), float(box.height)


def overlay_onto(
    page: PageObject, draw: Callable[[object, float, float], None], *, under: bool = False
) -> None:
    """Run ``draw(canvas, width, height)`` on a transparent page and merge it into ``page``.

    Coordinates inside ``draw`` start at the visible box's bottom-left corner.
    Callers normalise rotation first (transfer_rotation_to_content).
    """

    from reportlab.pdfgen import canvas as rl_canvas

    x0, y0, width, height = visible_box(page)
    buffer = io.BytesIO()
    canvas = rl_canvas.Canvas(buffer, pagesize=(width, height))
    draw(canvas, width, height)
    canvas.showPage()
    canvas.save()
    overlay = PdfReader(io.BytesIO(buffer.getvalue())).pages[0]
    page.merge_transformed_page(overlay, Transformation().translate(x0, y0), over=not under)


def anchor(
    position: str,
    width: float,
    height: float,
    box_width: float,
    box_height: float,
    margin: float,
) -> tuple[float, float]:
    """Bottom-left corner, in canvas coordinates, of a box placed at a named position."""

    key = position.strip().lower().replace("_", "-").replace(" ", "-")
    if key not in POSITIONS:
        raise ToolArgumentError(f"Unknown position {position!r}. Use one of {list(POSITIONS)}.")
    if "left" in key:
        x = margin
    elif "right" in key:
        x = width - margin - box_width
    else:
        x = (width - box_width) / 2
    if key.startswith("top"):
        y = height - margin - box_height
    elif key.startswith("bottom"):
        y = margin
    else:
        y = (height - box_height) / 2
    return x, y


def image_reader(path: Path, opacity: float):
    """A reportlab image with ``opacity`` baked into its alpha channel."""

    from PIL import Image
    from reportlab.lib.utils import ImageReader

    try:
        image = Image.open(path)
        image.load()
    except OSError as exc:
        raise ToolArgumentError(f"Cannot read image {path.name}: {exc}") from None
    image = image.convert("RGBA")
    if opacity < 1.0:
        alpha = image.getchannel("A").point(lambda value: int(value * opacity))
        image.putalpha(alpha)
    return ImageReader(image), image.size


def draw_text_block(
    canvas,
    lines: list[str],
    *,
    x: float,
    y_top: float,
    font: str,
    size: float,
    color: tuple[float, float, float],
    opacity: float,
    align: str,
    width: float,
) -> None:
    """Lines starting at ``y_top`` (baseline of the first line sits one size below).

    ``x`` is the left edge of a box ``width`` wide that the lines align within.
    """

    canvas.saveState()
    canvas.setFillColorRGB(*color)
    canvas.setFillAlpha(opacity)
    canvas.setFont(font, size)
    leading = size * 1.2
    for number, line in enumerate(lines):
        baseline = y_top - size - number * leading
        if align == "right":
            canvas.drawRightString(x + width, baseline, line)
        elif align == "center":
            canvas.drawCentredString(x + width / 2, baseline, line)
        else:
            canvas.drawString(x, baseline, line)
    canvas.restoreState()


def text_width(lines: list[str], font: str, size: float) -> float:
    from reportlab.pdfbase.pdfmetrics import stringWidth

    return max((stringWidth(line, font, size) for line in lines), default=0.0)
