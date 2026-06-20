from __future__ import annotations

from importlib import resources

from rich.text import Text

try:
    from art import text2art
except ImportError:  # pragma: no cover - dependency fallback for incomplete installs.
    text2art = None

BANNER_RESOURCE = "banner.txt"
CODE_AI_LOGO_FONT = "tarty2"
CODE_AI_BANNER_FONT_OPTIONS = (
    "tarty1",
    "tarty2",
    "tarty3",
    "tarty4",
    "tarty5",
    "tarty6",
    "tarty7",
    "tarty8",
    "tarty9",
    "future_1",
    "future_2",
    "future_3",
    "future_4",
    "future_5",
    "future_6",
    "future_7",
    "future_8",
    "block",
    "block2",
    "big",
    "small",
    "smallcaps",
    "standard",
    "slant",
    "doom",
    "epic",
    "mini",
    "cybermedium",
    "cyberlarge",
    "cybersmall",
    "digital",
    "thin",
    "thin2",
    "thin3",
    "lineblocks",
    "monospace",
    "xsansi",
)
CODE_AI_LOGO_STYLES = (
    "bold rgb(255,80,100)",
    "bold rgb(255,230,90)",
)

FALLBACK_CODE_AI_LOGO_TEXT = "code.ai"
FALLBACK_CODE_AI_LOGO_ART = "       \n█▀▀ █▀█ █▀▄ █▀▀ ░ ▄▀█ █ \n█▄▄ █▄█ █▄▀ ██▄ ▄ █▀█ █ \n       "


def normalize_banner_font(font: str) -> str:
    value = font.strip()
    if value in CODE_AI_BANNER_FONT_OPTIONS:
        return value
    return CODE_AI_LOGO_FONT


def load_banner_source() -> str:
    try:
        logo = resources.files(__package__).joinpath(BANNER_RESOURCE).read_text(
            encoding="utf-8"
        )
    except (FileNotFoundError, ModuleNotFoundError, OSError):
        return FALLBACK_CODE_AI_LOGO_TEXT
    if not logo.strip():
        return FALLBACK_CODE_AI_LOGO_TEXT
    return logo.strip()


def render_banner_art(source: str, *, font: str = CODE_AI_LOGO_FONT) -> str:
    font = normalize_banner_font(font)
    if text2art is None:
        return FALLBACK_CODE_AI_LOGO_ART
    try:
        return text2art(source, font=font)
    except Exception:
        return FALLBACK_CODE_AI_LOGO_ART


def style_banner_art(
    source: str,
    styles: tuple[str, ...] = CODE_AI_LOGO_STYLES,
) -> Text:
    lines = source.splitlines()
    styled = Text()
    visible_line_count = sum(1 for line in lines if line.strip())
    visible_line_index = 0

    for index, line in enumerate(lines):
        if line.strip() and styles:
            style_index = visible_line_count - visible_line_index - 1
            styled.append(line, style=styles[style_index % len(styles)])
            visible_line_index += 1
        else:
            styled.append(line)
        if index < len(lines) - 1:
            styled.append("\n")
    return styled


def load_code_ai_logo(font: str = CODE_AI_LOGO_FONT) -> Text:
    return style_banner_art(render_banner_art(load_banner_source(), font=font))


CODE_AI_LOGO = load_code_ai_logo()
