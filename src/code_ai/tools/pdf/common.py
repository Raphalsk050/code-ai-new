"""Units, colours, fonts and JSON arguments shared by the PDF tools."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from code_ai.core.errors import ToolArgumentError

POINTS_PER_UNIT = {"pt": 1.0, "in": 72.0, "cm": 72.0 / 2.54, "mm": 72.0 / 25.4, "px": 0.75}

PAGE_SIZES = {
    "a3": (841.89, 1190.55),
    "a4": (595.28, 841.89),
    "a5": (419.53, 595.28),
    "letter": (612.0, 792.0),
    "legal": (612.0, 1008.0),
    "tabloid": (792.0, 1224.0),
}

_NAMED_COLORS = {
    "black": "#000000",
    "white": "#ffffff",
    "red": "#d32f2f",
    "green": "#2e7d32",
    "blue": "#1565c0",
    "gray": "#808080",
    "grey": "#808080",
    "lightgray": "#d3d3d3",
    "darkgray": "#404040",
    "orange": "#ef6c00",
    "yellow": "#f9a825",
    "purple": "#6a1b9a",
}

_UNIT = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*(pt|in|cm|mm|px)?\s*$", re.IGNORECASE)


def to_points(value: Any, name: str) -> float:
    """A length in points from 72, "2cm", "10mm", "1in" or "12pt"."""

    if isinstance(value, bool):
        raise ToolArgumentError(f"'{name}' must be a length, got {value!r}.")
    if isinstance(value, int | float):
        return float(value)
    match = _UNIT.match(str(value))
    if not match:
        raise ToolArgumentError(f"'{name}' must be a length like 72, '2cm' or '10mm'.")
    return float(match.group(1)) * POINTS_PER_UNIT[(match.group(2) or "pt").lower()]


def page_size(value: Any, name: str = "size") -> tuple[float, float]:
    if isinstance(value, str) and value.strip().lower() in PAGE_SIZES:
        return PAGE_SIZES[value.strip().lower()]
    if isinstance(value, list | tuple) and len(value) == 2:
        return to_points(value[0], name), to_points(value[1], name)
    if isinstance(value, str) and "x" in value.lower():
        width, height = value.lower().split("x", 1)
        return to_points(width, name), to_points(height, name)
    raise ToolArgumentError(
        f"'{name}' must be one of {sorted(PAGE_SIZES)}, [width, height] or '210mmx297mm'."
    )


def rgb(value: Any, name: str = "color") -> tuple[float, float, float]:
    """0-1 floats from "#rrggbb", "#rgb", a basic colour name or [r, g, b] in 0-255."""

    if value is None:
        return 0.0, 0.0, 0.0
    if isinstance(value, list | tuple) and len(value) == 3:
        return tuple(max(0.0, min(1.0, float(c) / 255.0)) for c in value)  # type: ignore[return-value]
    text = _NAMED_COLORS.get(str(value).strip().lower(), str(value).strip())
    text = text.lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if not re.fullmatch(r"[0-9a-fA-F]{6}", text):
        raise ToolArgumentError(f"'{name}' must be a hex colour like '#1565c0', got {value!r}.")
    return tuple(int(text[i : i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]


def parse_json_argument(value: Any, name: str) -> Any:
    """JSON-bearing arguments arrive as strings, but some models send the object itself."""

    if value is None:
        return None
    if isinstance(value, list | dict):
        return value
    if not isinstance(value, str):
        raise ToolArgumentError(f"'{name}' must be a JSON string.")
    text = value.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ToolArgumentError(f"'{name}' is not valid JSON: {exc}") from None


_STANDARD_FONTS = {
    "helvetica": "Helvetica",
    "helvetica-bold": "Helvetica-Bold",
    "helvetica-oblique": "Helvetica-Oblique",
    "times": "Times-Roman",
    "times-roman": "Times-Roman",
    "times-bold": "Times-Bold",
    "times-italic": "Times-Italic",
    "courier": "Courier",
    "courier-bold": "Courier-Bold",
}

_registered: dict[str, str] = {}


def font_for(text: str, requested: str | None, workspace_file: Path | None = None) -> str:
    """A reportlab font name able to draw ``text``.

    The standard 14 fonts only cover Latin-1; anything past it needs a TrueType
    font, so fall back to one that ships with reportlab or the system.
    """

    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    if workspace_file is not None:
        return _register_ttf(workspace_file)
    name = _STANDARD_FONTS.get((requested or "helvetica").strip().lower())
    if name is None:
        raise ToolArgumentError(
            f"Unknown font {requested!r}. Use one of {sorted(_STANDARD_FONTS)} or font_file."
        )
    try:
        text.encode("cp1252")
        return name
    except UnicodeEncodeError:
        pass
    bold = "bold" in name.lower()
    for candidate in _unicode_font_candidates(bold):
        if candidate.is_file():
            registered = _register_ttf(candidate)
            font = pdfmetrics.getFont(registered)
            if isinstance(font, TTFont) and all(
                ord(ch) in font.face.charToGlyph or ch.isspace() for ch in text
            ):
                return registered
    return name


def _unicode_font_candidates(bold: bool) -> list[Path]:
    import reportlab

    bundled = Path(reportlab.__file__).parent / "fonts"
    windows = Path("C:/Windows/Fonts")
    return [
        windows / ("arialbd.ttf" if bold else "arial.ttf"),
        windows / ("segoeuib.ttf" if bold else "segoeui.ttf"),
        Path("/usr/share/fonts/truetype/dejavu")
        / ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"),
        Path("/usr/share/fonts/dejavu") / ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"),
        bundled / ("VeraBd.ttf" if bold else "Vera.ttf"),
    ]


def _register_ttf(path: Path) -> str:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFError, TTFont

    key = str(path.resolve())
    if key not in _registered:
        name = f"F{len(_registered)}-{path.stem}"
        try:
            pdfmetrics.registerFont(TTFont(name, key))
        except TTFError as exc:
            raise ToolArgumentError(f"Cannot use font {path.name}: {exc}") from None
        _registered[key] = name
    return _registered[key]
