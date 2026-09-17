"""Colour math: parsing, sRGB/OKLab/OKLCH, WCAG and APCA contrast, colour-vision simulation."""

from __future__ import annotations

import colorsys
import math
import re
from dataclasses import dataclass

from code_ai.core.errors import ToolArgumentError

NAMED = {
    "black": "#000000",
    "white": "#ffffff",
    "red": "#ff0000",
    "green": "#008000",
    "blue": "#0000ff",
    "yellow": "#ffff00",
    "orange": "#ffa500",
    "purple": "#800080",
    "pink": "#ffc0cb",
    "gray": "#808080",
    "grey": "#808080",
    "navy": "#000080",
    "teal": "#008080",
    "cyan": "#00ffff",
    "magenta": "#ff00ff",
    "lime": "#00ff00",
    "maroon": "#800000",
    "olive": "#808000",
    "silver": "#c0c0c0",
    "indigo": "#4b0082",
    "violet": "#ee82ee",
    "brown": "#a52a2a",
    "coral": "#ff7f50",
    "gold": "#ffd700",
    "crimson": "#dc143c",
    "tomato": "#ff6347",
    "slategray": "#708090",
    "rebeccapurple": "#663399",
    "transparent": "#00000000",
}


@dataclass(frozen=True)
class RGB:
    """sRGB channels in 0..1."""

    r: float
    g: float
    b: float

    @property
    def hex(self) -> str:
        return "#" + "".join(
            f"{round(max(0.0, min(1.0, c)) * 255):02x}" for c in (self.r, self.g, self.b)
        )


def parse(value: str) -> RGB:
    """#rgb, #rrggbb, rgb(), hsl(), oklch() or a CSS colour name."""

    text = str(value).strip().lower()
    text = NAMED.get(text, text)
    match = re.fullmatch(r"#?([0-9a-f]{3}|[0-9a-f]{6}|[0-9a-f]{8})", text)
    if match:
        digits = match.group(1)
        if len(digits) == 3:
            digits = "".join(ch * 2 for ch in digits)
        return RGB(*(int(digits[i : i + 2], 16) / 255 for i in (0, 2, 4)))
    numbers = [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", text)]
    if text.startswith("rgb") and len(numbers) >= 3:
        scale = 100 if "%" in text else 255
        return RGB(*(max(0.0, min(1.0, n / scale)) for n in numbers[:3]))
    if text.startswith("hsl") and len(numbers) >= 3:
        r, g, b = colorsys.hls_to_rgb(numbers[0] % 360 / 360, numbers[2] / 100, numbers[1] / 100)
        return RGB(r, g, b)
    if text.startswith("oklch") and len(numbers) >= 3:
        lightness = numbers[0] / 100 if "%" in text.split()[0] or numbers[0] > 1 else numbers[0]
        return oklch_to_rgb(lightness, numbers[1], numbers[2])
    raise ToolArgumentError(
        f"Unreadable colour {value!r}: use #hex, rgb(), hsl(), oklch() or a name."
    )


# -- transfer functions --------------------------------------------------------------------


def to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def to_gamma(c: float) -> float:
    return 12.92 * c if c <= 0.0031308 else 1.055 * (c ** (1 / 2.4)) - 0.055


# -- OKLab / OKLCH (Björn Ottosson) --------------------------------------------------------


def rgb_to_oklab(color: RGB) -> tuple[float, float, float]:
    r, g, b = (to_linear(c) for c in (color.r, color.g, color.b))
    l_ = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m_ = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s_ = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l_, m_, s_ = (math.copysign(abs(v) ** (1 / 3), v) for v in (l_, m_, s_))
    return (
        0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_,
        1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_,
        0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_,
    )


def oklab_to_linear(lightness: float, a: float, b: float) -> tuple[float, float, float]:
    l_ = lightness + 0.3963377774 * a + 0.2158037573 * b
    m_ = lightness - 0.1055613458 * a - 0.0638541728 * b
    s_ = lightness - 0.0894841775 * a - 1.2914855480 * b
    l_, m_, s_ = l_**3, m_**3, s_**3
    return (
        4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_,
        -1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_,
        -0.0041960863 * l_ - 0.7034186147 * m_ + 1.7076147010 * s_,
    )


def rgb_to_oklch(color: RGB) -> tuple[float, float, float]:
    lightness, a, b = rgb_to_oklab(color)
    chroma = math.hypot(a, b)
    hue = math.degrees(math.atan2(b, a)) % 360 if chroma > 1e-5 else 0.0
    return lightness, chroma, hue


def in_gamut(linear: tuple[float, float, float], tolerance: float = 1e-4) -> bool:
    return all(-tolerance <= c <= 1 + tolerance for c in linear)


def oklch_to_rgb(lightness: float, chroma: float, hue: float) -> RGB:
    """Converted to sRGB, reducing chroma until the colour exists (CSS Color 4 gamut mapping)."""

    lightness = max(0.0, min(1.0, lightness))

    def linear(c: float):
        radians = math.radians(hue)
        return oklab_to_linear(lightness, c * math.cos(radians), c * math.sin(radians))

    if not in_gamut(linear(chroma)):
        low, high = 0.0, chroma
        for _ in range(24):
            middle = (low + high) / 2
            if in_gamut(linear(middle)):
                low = middle
            else:
                high = middle
        chroma = low
    r, g, b = (to_gamma(max(0.0, min(1.0, c))) for c in linear(chroma))
    return RGB(r, g, b)


def delta_e_ok(a: RGB, b: RGB) -> float:
    la, aa, ba = rgb_to_oklab(a)
    lb, ab, bb = rgb_to_oklab(b)
    return math.sqrt((la - lb) ** 2 + (aa - ab) ** 2 + (ba - bb) ** 2)


# -- contrast ------------------------------------------------------------------------------


def relative_luminance(color: RGB) -> float:
    r, g, b = (to_linear(c) for c in (color.r, color.g, color.b))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def wcag_ratio(foreground: RGB, background: RGB) -> float:
    lighter, darker = sorted(
        (relative_luminance(foreground), relative_luminance(background)), reverse=True
    )
    return (lighter + 0.05) / (darker + 0.05)


def apca(text: RGB, background: RGB) -> float:
    """APCA-W3 0.0.98G lightness contrast Lc; positive for dark text on light backgrounds."""

    def y(color: RGB) -> float:
        value = 0.2126729 * color.r**2.4 + 0.7151522 * color.g**2.4 + 0.0721750 * color.b**2.4
        return value + (0.022 - value) ** 1.414 if value < 0.022 else value

    text_y, background_y = y(text), y(background)
    if abs(background_y - text_y) < 0.0005:
        return 0.0
    if background_y > text_y:
        sapc = (background_y**0.56 - text_y**0.57) * 1.14
        output = 0.0 if sapc < 0.1 else sapc - 0.027
    else:
        sapc = (background_y**0.65 - text_y**0.62) * 1.14
        output = 0.0 if sapc > -0.1 else sapc + 0.027
    return round(output * 100, 1)


def grade(ratio: float) -> dict[str, bool]:
    return {
        "aa_normal": ratio >= 4.5,
        "aa_large": ratio >= 3.0,
        "aaa_normal": ratio >= 7.0,
        "aaa_large": ratio >= 4.5,
        "ui_components": ratio >= 3.0,
    }


def fix_contrast(foreground: RGB, background: RGB, target: float = 4.5) -> RGB:
    """The foreground's hue and chroma at the nearest lightness that reaches ``target``."""

    if wcag_ratio(foreground, background) >= target:
        return foreground
    lightness, chroma, hue = rgb_to_oklch(foreground)
    best: RGB | None = None
    best_distance = 2.0
    for step in range(1, 101):
        for direction in (-1, 1):
            candidate_l = lightness + direction * step / 100
            if not 0 <= candidate_l <= 1:
                continue
            candidate = oklch_to_rgb(candidate_l, chroma, hue)
            if wcag_ratio(candidate, background) >= target and step / 100 < best_distance:
                best, best_distance = candidate, step / 100
        if best is not None:
            return best
    black, white = RGB(0, 0, 0), RGB(1, 1, 1)
    return black if wcag_ratio(black, background) >= wcag_ratio(white, background) else white


# -- colour vision deficiency (Machado, Oliveira & Fernandes 2009, severity 1.0) -----------

_CVD = {
    "protanopia": (
        (0.152286, 1.052583, -0.204868),
        (0.114503, 0.786281, 0.099216),
        (-0.003882, -0.048116, 1.051998),
    ),
    "deuteranopia": (
        (0.367322, 0.860646, -0.227968),
        (0.280085, 0.672501, 0.047413),
        (-0.011820, 0.042940, 0.968881),
    ),
    "tritanopia": (
        (1.255528, -0.076749, -0.178779),
        (-0.078411, 0.930809, 0.147602),
        (0.004733, 0.691367, 0.303900),
    ),
}
CVD_TYPES = (*_CVD, "achromatopsia")


def simulate(color: RGB, kind: str) -> RGB:
    linear = [to_linear(c) for c in (color.r, color.g, color.b)]
    if kind == "achromatopsia":
        y = relative_luminance(color)
        value = to_gamma(y)
        return RGB(value, value, value)
    matrix = _CVD[kind]
    mixed = [sum(matrix[row][col] * linear[col] for col in range(3)) for row in range(3)]
    return RGB(*(to_gamma(max(0.0, min(1.0, c))) for c in mixed))
