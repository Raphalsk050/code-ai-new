"""Deck themes: colours, fonts and the few proportions every layout shares."""

from __future__ import annotations

from dataclasses import dataclass, replace

from pptx.dml.color import RGBColor

from code_ai.core.errors import ToolArgumentError


@dataclass(frozen=True)
class Theme:
    name: str
    background: str
    surface: str
    text: str
    muted: str
    accent: str
    accent_text: str
    palette: tuple[str, ...]
    title_font: str
    body_font: str
    code_font: str = "Consolas"
    title_background: str | None = None
    title_text: str | None = None
    code_background: str = "1E293B"
    code_text: str = "E2E8F0"
    border: str = "E2E8F0"
    title_bold: bool = True
    accent_bar: bool = True
    title_size: float = 32
    body_size: float = 20

    @property
    def dark(self) -> bool:
        return luminance(self.background) < 0.4


THEMES: dict[str, Theme] = {
    "corporate": Theme(
        name="corporate",
        background="FFFFFF",
        surface="F3F6FB",
        text="1B2638",
        muted="5B677A",
        accent="0B3D91",
        accent_text="FFFFFF",
        palette=("0B3D91", "1C7ED6", "12B886", "F59F00", "E8590C", "7048E8"),
        title_font="Segoe UI Semibold",
        body_font="Segoe UI",
        title_background="0B3D91",
        title_text="FFFFFF",
        border="D6DEEB",
    ),
    "minimal": Theme(
        name="minimal",
        background="FFFFFF",
        surface="F5F5F4",
        text="1C1917",
        muted="78716C",
        accent="1C1917",
        accent_text="FFFFFF",
        palette=("1C1917", "57534E", "A8A29E", "D97706", "0284C7", "65A30D"),
        title_font="Segoe UI Light",
        body_font="Segoe UI",
        title_bold=False,
        accent_bar=False,
        border="E7E5E4",
        title_size=34,
    ),
    "dark": Theme(
        name="dark",
        background="0F172A",
        surface="1E293B",
        text="F1F5F9",
        muted="94A3B8",
        accent="38BDF8",
        accent_text="0F172A",
        palette=("38BDF8", "A78BFA", "34D399", "FBBF24", "F87171", "F472B6"),
        title_font="Segoe UI Semibold",
        body_font="Segoe UI",
        title_background="020617",
        title_text="F8FAFC",
        code_background="020617",
        border="334155",
    ),
    "vibrant": Theme(
        name="vibrant",
        background="FFFFFF",
        surface="F5F3FF",
        text="1E1B4B",
        muted="6B7280",
        accent="7C3AED",
        accent_text="FFFFFF",
        palette=("7C3AED", "EC4899", "F97316", "06B6D4", "10B981", "EAB308"),
        title_font="Segoe UI Black",
        body_font="Segoe UI",
        title_background="7C3AED",
        title_text="FFFFFF",
        border="E9D5FF",
    ),
    "academic": Theme(
        name="academic",
        background="FFFDF8",
        surface="F4EFE6",
        text="2B2118",
        muted="6F6254",
        accent="7F1D1D",
        accent_text="FFFFFF",
        palette=("7F1D1D", "1E3A5F", "4D7C0F", "B45309", "6B21A8", "0F766E"),
        title_font="Georgia",
        body_font="Calibri",
        title_background="7F1D1D",
        title_text="FFFDF8",
        border="E2D8C8",
        body_size=19,
    ),
    "ocean": Theme(
        name="ocean",
        background="FFFFFF",
        surface="ECFEFF",
        text="0C2233",
        muted="4B6475",
        accent="0E7490",
        accent_text="FFFFFF",
        palette=("0E7490", "0369A1", "14B8A6", "F59E0B", "EF4444", "6366F1"),
        title_font="Segoe UI Semibold",
        body_font="Segoe UI",
        title_background="083344",
        title_text="ECFEFF",
        border="CFFAFE",
    ),
}


def get_theme(name: str | None, *, accent: str | None = None, font: str | None = None) -> Theme:
    key = (name or "corporate").strip().lower()
    if key not in THEMES:
        raise ToolArgumentError(f"Unknown theme {name!r}. Choose from {sorted(THEMES)}.")
    theme = THEMES[key]
    changes: dict = {}
    if accent:
        color = clean_hex(accent)
        changes.update(accent=color, palette=(color, *theme.palette[1:]))
        if theme.title_background and theme.name != "dark":
            changes["title_background"] = color
    if font:
        changes.update(title_font=font, body_font=font)
    return replace(theme, **changes) if changes else theme


def clean_hex(value: str) -> str:
    text = str(value).strip().lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) != 6 or any(ch not in "0123456789abcdefABCDEF" for ch in text):
        raise ToolArgumentError(f"Not a hex colour: {value!r}.")
    return text.upper()


def rgb(value: str) -> RGBColor:
    return RGBColor.from_string(clean_hex(value))


def luminance(hex_color: str) -> float:
    """WCAG relative luminance."""

    channels = []
    for i in (0, 2, 4):
        c = int(clean_hex(hex_color)[i : i + 2], 16) / 255
        channels.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    r, g, b = channels
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def text_safe(color: str, background: str, minimum: float = 3.0) -> str:
    """``color`` darkened (or lightened on dark backgrounds) until it reads as large text."""

    color = clean_hex(color)
    target = "000000" if luminance(background) > 0.4 else "FFFFFF"
    step = 0.0
    candidate = color
    while contrast(candidate, background) < minimum and step < 1.0:
        step += 0.08
        channels = []
        for i in (0, 2, 4):
            start, end = int(color[i : i + 2], 16), int(target[i : i + 2], 16)
            channels.append(f"{round(start + (end - start) * step):02X}")
        candidate = "".join(channels)
    return candidate


def readable_on(background: str, preferred: str, alternative: str = "FFFFFF") -> str:
    """``preferred`` if it reads on ``background`` at 4.5:1, else whichever option reads best."""

    if contrast(background, preferred) >= 4.5:
        return preferred
    options = [preferred, alternative, "000000", "FFFFFF"]
    return max(options, key=lambda c: contrast(background, c))
