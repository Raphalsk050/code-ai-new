"""Measuring text against a box, so a slide never overflows.

PowerPoint's own autofit only runs inside PowerPoint; LibreOffice and every
previewer draw the raw size. Sizes are therefore decided here, with the real
font metrics when the font file can be found and an average glyph width when
it cannot.
"""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path

LINE_SPACING = 1.18

_FILES = {
    "segoe ui": ("segoeui.ttf", "segoeuib.ttf"),
    "calibri": ("calibri.ttf", "calibrib.ttf"),
    "arial": ("arial.ttf", "arialbd.ttf"),
    "georgia": ("georgia.ttf", "georgiab.ttf"),
    "consolas": ("consola.ttf", "consolab.ttf"),
    "cambria": ("cambria.ttc", "cambriab.ttf"),
    "verdana": ("verdana.ttf", "verdanab.ttf"),
    "times new roman": ("times.ttf", "timesbd.ttf"),
}

_LINUX_FALLBACKS = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
)


@lru_cache(maxsize=64)
def _font(family: str, bold: bool, size_tenths: int):
    """(font, width factor): the exact cut when installed, else its base family scaled."""

    try:
        from PIL import ImageFont
    except ImportError:  # pragma: no cover - pillow is a dependency
        return None, 1.0
    base, heavy, factor = _base_family(family)
    attempts = [(family.strip().lower(), bold, 1.0)]
    if base != family.strip().lower():
        attempts.append((base, bold or heavy, factor))
    for name, weight, scale in attempts:
        for path in _candidates(name, weight):
            try:
                return ImageFont.truetype(str(path), max(1, size_tenths) / 10), scale
            except OSError:
                continue
    return None, factor


_WEIGHTS = ("semibold", "black", "light", "bold", "medium", "heavy")

# Heavier cuts than the file we fall back to run wider.
_WIDTH_FACTOR = {"black": 1.1, "heavy": 1.1, "semibold": 1.03}


def _base_family(family: str) -> tuple[str, bool, float]:
    words = family.strip().lower().split()
    weight = next((w for w in words if w in _WEIGHTS), "")
    base = " ".join(w for w in words if w not in _WEIGHTS)
    return base, weight in {"semibold", "black", "bold", "heavy"}, _WIDTH_FACTOR.get(weight, 1.0)


def _candidates(family: str, bold: bool) -> list[Path]:
    names = _FILES.get(family)
    if not names:
        return []
    directories = []
    if sys.platform == "win32":
        windir = os.environ.get("WINDIR", "C:/Windows")
        directories += [
            Path(windir, "Fonts"),
            Path.home() / "AppData/Local/Microsoft/Windows/Fonts",
        ]
    else:
        directories += [Path("/usr/share/fonts"), Path.home() / ".fonts", Path("/Library/Fonts")]
    wanted = names[1] if bold else names[0]
    found: list[Path] = []
    for directory in directories:
        candidate = directory / wanted
        if candidate.is_file():
            found.append(candidate)
        elif directory.is_dir() and sys.platform != "win32":
            found += list(directory.rglob(wanted))[:1]
    return found


def _fallback_font(size_tenths: int):
    try:
        from PIL import ImageFont
    except ImportError:  # pragma: no cover
        return None
    for path in _LINUX_FALLBACKS:
        if Path(path).is_file():
            return ImageFont.truetype(path, max(1, size_tenths) / 10)
    return None


def text_width(text: str, family: str, size: float, bold: bool = False) -> float:
    """Width in points."""

    font, factor = _font(family, bold, int(size * 10))
    if font is None:
        font = _fallback_font(int(size * 10))
    if font is None:
        return len(text) * size * (0.58 if bold else 0.54) * factor
    return font.getlength(text) * factor


def wrap(text: str, width: float, family: str, size: float, bold: bool = False) -> list[str]:
    lines: list[str] = []
    for paragraph in text.split("\n"):
        words = paragraph.split(" ")
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip() if current else word
            if text_width(candidate, family, size, bold) <= width or not current:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return lines


def block_height(
    paragraphs: list[tuple[str, int]],
    width: float,
    family: str,
    size: float,
    *,
    bold: bool = False,
    indent: float = 0.0,
    gap: float = 0.35,
) -> float:
    """Height in points of (text, level) paragraphs wrapped into ``width``."""

    total = 0.0
    for number, (text, level) in enumerate(paragraphs):
        level_size = size * (0.88 ** min(level, 3))
        usable = max(20.0, width - indent * (level + 1))
        # A word wider than the box breaks mid-word, which never looks intended.
        if any(text_width(word, family, level_size, bold) > usable for word in text.split()):
            return float("inf")
        lines = len(wrap(text, usable, family, level_size, bold))
        total += lines * level_size * LINE_SPACING
        if number:
            total += level_size * gap
    return total


def fit_size(
    paragraphs: list[tuple[str, int]],
    width: float,
    height: float,
    family: str,
    *,
    start: float,
    minimum: float,
    bold: bool = False,
    indent: float = 0.0,
    gap: float = 0.35,
) -> tuple[float, bool]:
    """Largest size from ``start`` to ``minimum`` that fits; False if the minimum overflows."""

    size = start
    while size >= minimum:
        if (
            block_height(paragraphs, width, family, size, bold=bold, indent=indent, gap=gap)
            <= height
        ):
            return size, True
        size -= 1 if size > 14 else 0.5
    return minimum, False
