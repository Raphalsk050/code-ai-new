from __future__ import annotations

from importlib import resources

from rich.text import Text

try:
    from art import text2art
except ImportError:  # pragma: no cover - dependency fallback for incomplete installs.
    text2art = None

BANNER_RESOURCE = "banner.txt"
CODE_AI_LOGO_FONT = "tarty2"
CODE_AI_LOGO_STYLES = (
    "bold rgb(255,100,150)",
    "color(155) on color(235)",
)

FALLBACK_CODE_AI_LOGO_TEXT = "code.ai"
FALLBACK_CODE_AI_LOGO_ART = "       \n█▀▀ █▀█ █▀▄ █▀▀ ░ ▄▀█ █ \n█▄▄ █▄█ █▄▀ ██▄ ▄ █▀█ █ \n       "


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


def load_code_ai_logo() -> Text:
    return style_banner_art(render_banner_art(load_banner_source()))


CODE_AI_LOGO = load_code_ai_logo()
