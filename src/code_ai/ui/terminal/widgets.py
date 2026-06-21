from __future__ import annotations

from dataclasses import dataclass
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
THINKING_LINE_STYLE = "#6b7280"

# --- Working indicator ("the agent is busy" animation) ----------------------
# AgentState values that mean the agent is actively doing something; the
# indicator animates for these and stays static otherwise.
WORKING_STATES = frozenset(
    {"CALLING_MODEL", "EXECUTING_TOOL", "COMPRESSING_CONTEXT", "CANCELLING"}
)
_WORKING_LABELS = {
    "CALLING_MODEL": "calling model",
    "EXECUTING_TOOL": "running tools",
    "COMPRESSING_CONTEXT": "compacting context",
    "CANCELLING": "cancelling",
}

# Animated glyph color base, the dim label next to it, and the static idle tint.
WORKING_BASE_COLOR = "#ff9f1c"
WORKING_LABEL_STYLE = "#6b7280"
WORKING_IDLE_STYLE = "#3b4654"
# Seconds for one full color-pulse cycle, and the red->orange->yellow ramp it
# walks through (matches the CODE.AI banner palette).
WORKING_PULSE_PERIOD = 2.2
WORKING_PULSE_STOPS = ((255, 80, 100), (255, 138, 60), (255, 210, 80), (255, 138, 60))


@dataclass(frozen=True, slots=True)
class SpinnerStyle:
    """One selectable working-indicator animation.

    ``frames`` are cycled every ``interval`` seconds. ``pulse`` makes the glyph
    walk the color ramp continuously (independent of the frame rate), which is
    what gives single-frame styles like the plain asterisk their life.
    """

    key: str
    label: str
    frames: tuple[str, ...]
    interval: float
    pulse: bool = False


# Order here drives the command palette and `/config spinner` listing. The
# first entry is the default; the entries that read most like Claude Code's
# indicator are grouped right after it.
_SPINNER_LIST = (
    SpinnerStyle("ascii", "ASCII line (retro)", ("|", "/", "—", "\\"), 0.11),
    SpinnerStyle(
        "star-spin", "star spinning", ("✶", "✷", "✸", "✹", "✺", "✹", "✸", "✷"), 0.11, True
    ),
    SpinnerStyle("asterisk-pulse", "asterisk pulse", ("✳",), 0.6, True),
    SpinnerStyle("asterisk-color", "asterisk color only", ("✻",), 0.6, True),
    SpinnerStyle(
        "braille-full",
        "braille full (smooth)",
        ("⣾", "⣽", "⣻", "⢿", "⡿", "⣟", "⣯", "⣷"),
        0.07,
        True,
    ),
    SpinnerStyle("sparkle", "sparkle", ("·", "✢", "✦", "✶", "✦", "✢"), 0.13),
    SpinnerStyle("asterisk-spin", "asterisk spinning", ("✲", "✳", "✴", "✳"), 0.12, True),
    SpinnerStyle(
        "starburst", "starburst", ("·", "∗", "✳", "✺", "✹", "✺", "✳", "∗"), 0.11, True
    ),
    SpinnerStyle(
        "braille-classic",
        "braille classic",
        ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"),
        0.08,
    ),
    SpinnerStyle(
        "braille-wave",
        "braille wave (fill)",
        ("⡀", "⣀", "⣄", "⣆", "⣇", "⣧", "⣷", "⣿", "⣷", "⣧", "⣇", "⣆", "⣄", "⣀"),
        0.06,
    ),
    SpinnerStyle("orbit", "orbit dot", ("⠁", "⠂", "⠄", "⡀", "⢀", "⠠", "⠐", "⠈"), 0.095),
    SpinnerStyle("braille-orbit", "braille orbit", ("⠁", "⠈", "⠐", "⠠", "⢀", "⡀", "⠄", "⠂"), 0.075),
    SpinnerStyle(
        "equalizer",
        "equalizer / breathing",
        ("▁", "▂", "▃", "▄", "▅", "▆", "▇", "█", "▇", "▆", "▅", "▄", "▃", "▂"),
        0.085,
    ),
    SpinnerStyle("shade", "shaded block", ("░", "▒", "▓", "█", "▓", "▒"), 0.095),
    SpinnerStyle("dotted-circle", "dotted circle", ("◌", "◍", "◎", "●", "◉", "●", "◎", "◍"), 0.13),
    SpinnerStyle("corners", "corners", ("▖", "▘", "▝", "▗"), 0.12),
    SpinnerStyle("quad-spin", "quadrants spinning", ("▘", "▝", "▗", "▖"), 0.11),
    SpinnerStyle("arc", "arc", ("◜", "◠", "◝", "◞", "◡", "◟"), 0.11),
    SpinnerStyle("moon", "moon", ("◐", "◓", "◑", "◒"), 0.15),
    SpinnerStyle("clock", "clock", ("◴", "◷", "◶", "◵"), 0.14),
    SpinnerStyle("triangle", "triangle", ("◢", "◣", "◤", "◥"), 0.13),
    SpinnerStyle("flower", "flower", ("❉", "❊", "❋", "❊"), 0.16, True),
)
WORKING_SPINNERS = {style.key: style for style in _SPINNER_LIST}
CODE_AI_SPINNER_OPTIONS = tuple(style.key for style in _SPINNER_LIST)
DEFAULT_SPINNER = "ascii"


def working_label(status: str) -> str:
    return _WORKING_LABELS.get(status, "working")


def normalize_spinner(spinner: str) -> str:
    key = spinner.strip()
    if key in WORKING_SPINNERS:
        return key
    return DEFAULT_SPINNER


def resolve_spinner(spinner: str) -> SpinnerStyle:
    return WORKING_SPINNERS[normalize_spinner(spinner)]


def spinner_color(progress: float) -> str:
    """Hex color for a point along the pulse ramp; ``progress`` wraps at 1.0."""
    stops = WORKING_PULSE_STOPS
    count = len(stops)
    position = (progress % 1.0) * count
    index = int(position)
    fraction = position - index
    start = stops[index % count]
    end = stops[(index + 1) % count]
    red = round(start[0] + (end[0] - start[0]) * fraction)
    green = round(start[1] + (end[1] - start[1]) * fraction)
    blue = round(start[2] + (end[2] - start[2]) * fraction)
    return f"#{red:02x}{green:02x}{blue:02x}"


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


def render_conversation_line(line: str) -> str | Text:
    if line.startswith("thinking> ") or line.startswith("model> thinking"):
        return Text(line, style=THINKING_LINE_STYLE)
    return line


CODE_AI_LOGO = load_code_ai_logo()
