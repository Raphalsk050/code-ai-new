"""One palette for the whole terminal UI, built from the active theme.

Everything the UI paints comes from one :class:`Palette`, and a palette comes
from a theme:

* ``monokai`` is Code-AI's own look: the literal colours the tool shipped with.
* the themes in :mod:`code_ai.ui.terminal.themes` are built from the colours
  each one is known by, so ``dracula`` has Dracula's purple banner and Dracula's
  code colours instead of a faded copy of Code-AI's.
* any other theme (``ansi-*``, one a user registers) is derived from the
  handful of colours Textual gives it, through the same builder.

The palette is exposed twice, because the UI paints in two ways: as CSS
variables (``$ca-*``) for the stylesheet, and as a process-wide "active
palette" the Rich renderables read at draw time.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from functools import cache, lru_cache
from typing import TypeVar

from rich.terminal_theme import TerminalTheme
from rich.theme import Theme as RichTheme
from textual.app import App
from textual.color import Color
from textual.theme import Theme

from code_ai.ui.terminal.themes import THEME_SPECS, SyntaxColors, ThemeSpec

# The theme name that carries Code-AI's original colours. Registering a theme
# under a built-in name overrides it, which is deliberate here: this *is* the
# tool's default look, and it needs a slot in the theme picker.
CODE_AI_THEME_NAME = "monokai"

# Mirrors textual.app.App's own type parameter: what the app returns on exit.
ReturnType = TypeVar("ReturnType")


@dataclass(frozen=True, slots=True)
class Palette:
    """Every colour the terminal UI is allowed to use, named by its job.

    Slots are semantic rather than literal ("trace", not "gray"), so a theme
    only has to answer "what is the dim colour here?" once and every surface
    that needs a dim colour follows.
    """

    # --- Structure: the surfaces things sit on ---------------------------
    background: str
    input_background: str
    surface: str
    surface_highlight: str
    hover_background: str
    terminal_focus_background: str
    border: str
    border_dim: str
    card_border: str
    # Text that sits on a frame: the context meter, panel headers.
    label: str

    # --- Text: one ramp from the brightest body text down to the faintest -
    foreground: str
    muted: str
    subtle: str
    trace: str
    done: str
    faint: str
    gutter: str
    idle: str

    # --- Accents: the colours that carry meaning -------------------------
    accent: str
    amber: str
    success: str
    success_bright: str
    success_border: str
    success_subtitle: str
    command_text: str
    error: str
    error_soft: str
    info: str
    highlight: str
    chip_text: str

    # --- Diffs: foreground plus the tinted row behind it -----------------
    diff_add_fg: str
    diff_add_bg: str
    diff_del_fg: str
    diff_del_bg: str

    # --- Brand: the banner gradient and the spinner's colour pulse -------
    logo_start: str
    logo_end: str
    pulse_stops: tuple[tuple[int, int, int], ...]

    # --- Code: a Pygments style name, or the theme's own token colours ---
    syntax_theme: str
    syntax_colors: SyntaxColors | None = None

    # Code-AI's original look paints answers with Rich's stock markdown colours
    # and Textual's ANSI map; every other theme brings its own.
    classic: bool = False


# The colours Code-AI has always drawn with, kept verbatim so the ``monokai``
# theme reproduces today's screen exactly.
CODE_AI_PALETTE = Palette(
    background="#071018",
    input_background="#0b141d",
    surface="#111820",
    surface_highlight="#17212c",
    hover_background="#1b2530",
    terminal_focus_background="#0d1512",
    border="#8892a0",
    border_dim="#2b3440",
    card_border="#2b3a4a",
    label="#8892a0",
    foreground="#d7dee8",
    muted="#9fb3c8",
    subtle="#9aa4b2",
    trace="#6b7280",
    done="#7b8493",
    faint="#56606e",
    gutter="#5c6773",
    idle="#3b4654",
    accent="#ff9f1c",
    amber="#f5c84c",
    success="#48d17a",
    success_bright="#7ee787",
    success_border="#2f6b45",
    success_subtitle="#3f7d56",
    command_text="#9fb3a6",
    error="#e05252",
    error_soft="#e0a0a0",
    info="#4fc3dc",
    highlight="#7aa2f7",
    chip_text="#071018",
    diff_add_fg="#a6e3a1",
    diff_add_bg="#1d3322",
    diff_del_fg="#f38ba8",
    diff_del_bg="#3a1620",
    logo_start="#ff5064",
    logo_end="#ffe65a",
    pulse_stops=((255, 80, 100), (255, 138, 60), (255, 210, 80), (255, 138, 60)),
    syntax_theme="monokai",
    classic=True,
)


# --- Contrast -----------------------------------------------------------


def _luminance(color: Color) -> float:
    """WCAG relative luminance."""

    def channel(value: int) -> float:
        c = value / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * channel(color.r) + 0.7152 * channel(color.g) + 0.0722 * channel(color.b)


def contrast_ratio(first: str | Color, second: str | Color) -> float:
    """WCAG contrast between two colours, 1.0 (none) to 21.0."""
    a = Color.parse(first) if isinstance(first, str) else first
    b = Color.parse(second) if isinstance(second, str) else second
    high, low = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def _shift(color: Color, amount: float) -> Color:
    """Lighter (positive) or darker (negative) with the hue and saturation kept.

    Mixing towards the text colour is what used to wash accents out; moving
    only the lightness keeps them as vivid as the theme drew them.
    """
    hue, saturation, lightness = color.hsl
    return Color.from_hsl(hue, saturation, min(1.0, max(0.0, lightness + amount)))


def _legible(color: Color, page: Color, ratio: float, *, dark: bool) -> Color:
    """``color`` moved away from ``page`` just far enough to read at ``ratio``."""
    step = 0.02 if dark else -0.02
    candidate = color
    for _ in range(50):
        if contrast_ratio(candidate, page) >= ratio:
            return candidate
        candidate = _shift(candidate, step)
    return candidate


def _chip_text(background: Color, foreground: Color, *fills: Color) -> Color:
    """The page or the ink, whichever reads better on every chip fill."""
    return max(
        (background, foreground),
        key=lambda ink: min(contrast_ratio(ink, fill) for fill in fills),
    )


# --- Building palettes --------------------------------------------------


def _rgb(color: Color) -> tuple[int, int, int]:
    return (color.r, color.g, color.b)


def build_palette(spec: ThemeSpec) -> Palette:
    """The full palette for a theme spec.

    The spec's colours are used as the theme drew them. The builder only fills
    in the slots in between (the dim ramp, tinted rows, borders) and nudges a
    colour's lightness when it would not read on the page. Minimum contrasts:
    4.5 for secondary text and diff rows, 3 for accents and the work trace.
    """

    dark = spec.dark
    bg = Color.parse(spec.background)
    fg = Color.parse(spec.foreground)
    surface = Color.parse(spec.surface)
    panel = Color.parse(spec.panel)

    def legible(value: str | Color, ratio: float, page: Color = bg) -> Color:
        color = Color.parse(value) if isinstance(value, str) else value
        return _legible(color, page, ratio, dark=dark)

    muted = legible(spec.muted, 4.5)
    trace = legible(spec.trace, 3.0)
    border = legible(spec.border, 1.8)
    border_dim = Color.parse(spec.border_dim)
    accent = legible(spec.accent, 3.0)
    info = legible(spec.info, 3.0)
    highlight = legible(spec.highlight, 3.0)
    success = legible(spec.success, 3.0)
    warning = legible(spec.warning, 3.0)
    error = legible(spec.error, 3.0)
    success_bright = legible(_shift(success, 0.08 if dark else -0.06), 3.0)
    add_bg = bg.blend(success, 0.16 if dark else 0.12)
    del_bg = bg.blend(error, 0.16 if dark else 0.12)
    logo_start, logo_end = (Color.parse(value) for value in spec.logo)
    middle = logo_start.blend(logo_end, 0.5)

    colours = dict(
        background=bg,
        input_background=bg.blend(surface, 0.6),
        surface=surface,
        surface_highlight=panel,
        hover_background=surface.blend(panel, 0.6),
        terminal_focus_background=bg.blend(success, 0.08),
        border=border,
        border_dim=border_dim,
        card_border=border_dim.blend(border, 0.35),
        # Frames can be as quiet as the theme likes; words on them cannot.
        label=legible(muted.blend(trace, 0.5), 3.5),
        foreground=fg,
        muted=muted,
        subtle=legible(muted.blend(trace, 0.3), 4.0),
        trace=trace,
        done=legible(muted.blend(trace, 0.65), 3.5),
        faint=legible(trace.blend(bg, 0.25), 2.3),
        gutter=legible(trace.blend(bg, 0.15), 2.5),
        idle=trace.blend(bg, 0.55),
        accent=accent,
        amber=warning,
        success=success,
        success_bright=success_bright,
        success_border=success.blend(bg, 0.5),
        success_subtitle=success.blend(bg, 0.3),
        command_text=legible(fg.blend(success, 0.3), 4.5),
        error=error,
        error_soft=legible(error.blend(fg, 0.3), 4.5),
        info=info,
        highlight=highlight,
        # Chips are the user's and the model's names, on the success and the
        # accent fills.
        chip_text=_chip_text(bg, fg, success, accent),
        diff_add_fg=legible(success_bright, 4.5, add_bg),
        diff_add_bg=add_bg,
        diff_del_fg=legible(_shift(error, 0.06 if dark else -0.04), 4.5, del_bg),
        diff_del_bg=del_bg,
        logo_start=logo_start,
        logo_end=logo_end,
    )
    return Palette(
        **{slot: color.hex.lower() for slot, color in colours.items()},
        # The spinner walks the banner's own gradient and back.
        pulse_stops=(_rgb(logo_start), _rgb(middle), _rgb(logo_end), _rgb(middle)),
        syntax_theme=spec.syntax if isinstance(spec.syntax, str) else "",
        syntax_colors=None if isinstance(spec.syntax, str) else spec.syntax,
    )


def _solid(color: Color, fallback: Color) -> Color:
    """A colour with real channels behind it, blendable and printable.

    The ``ansi-*`` themes name their colours by ANSI slot rather than by value:
    ``ansi_default`` has no colour at all (it means "whatever the terminal
    uses") and the surfaces come through transparent. Each one is resolved to
    the concrete colour it stands for, and anything with nothing behind it
    falls back to the page.
    """
    if color.ansi is not None:
        # A negative slot is "terminal default"; a real slot carries the
        # standard RGB for that ANSI colour.
        return fallback if color.ansi < 0 else Color(color.r, color.g, color.b)
    if not color.a:
        return fallback
    return color


def derive_palette(theme: Theme) -> Palette:
    """A palette for a theme with no spec, from the colours Textual gives it.

    Goes through the same builder as the curated themes: the banner, the
    spinner and the code colours come from the theme's own primary, accent and
    status colours rather than from Code-AI's.
    """

    generated = theme.to_color_system().generate()
    # What the page and the ink fall back to when the theme declines to say
    # (the ansi-* themes leave both to the terminal).
    page = Color.parse("#000000" if theme.dark else "#ffffff")
    ink = Color.parse("#ffffff" if theme.dark else "#000000")

    def base(name: str, fallback: Color) -> Color:
        return _solid(Color.parse(generated[name]), fallback)

    bg = base("background", page)
    fg = base("foreground", ink)
    surface = base("surface", bg.blend(ink, 0.06))
    panel = base("panel", surface.blend(fg, 0.08))
    primary = base("primary", fg)
    secondary = base("secondary", primary)
    accent = base("accent", primary)
    success = base("success", fg)
    warning = base("warning", accent)
    error = base("error", fg)
    trace = fg.blend(bg, 0.5)

    return build_palette(
        ThemeSpec(
            name=theme.name,
            dark=theme.dark,
            background=bg.hex,
            surface=surface.hex,
            panel=panel.hex,
            foreground=fg.hex,
            muted=fg.blend(bg, 0.25).hex,
            trace=trace.hex,
            border=fg.blend(bg, 0.6).hex,
            border_dim=fg.blend(bg, 0.8).hex,
            accent=accent.hex,
            info=primary.hex,
            highlight=secondary.hex,
            success=success.hex,
            warning=warning.hex,
            error=error.hex,
            logo=(primary.hex, accent.hex),
            syntax=SyntaxColors(
                comment=trace.hex,
                keyword=secondary.hex,
                string=success.hex,
                number=warning.hex,
                function=primary.hex,
                type=accent.hex,
                builtin=error.hex,
                variable=fg.hex,
                operator=fg.blend(bg, 0.25).hex,
            ),
        )
    )


@cache
def _spec_palette(name: str) -> Palette:
    return build_palette(THEME_SPECS[name])


def palette_for_theme(theme: Theme) -> Palette:
    """The palette a theme should paint with."""
    if theme.name == CODE_AI_THEME_NAME:
        return CODE_AI_PALETTE
    if theme.name in THEME_SPECS:
        return _spec_palette(theme.name)
    return derive_palette(theme)


# --- Textual themes -----------------------------------------------------


def _textual_theme(name: str, palette: Palette, *, dark: bool, primary: str) -> Theme:
    """A Textual theme carrying the palette, so the chrome Textual draws for us
    (header, footer, buttons, scrollbars, selection) matches the rest."""
    return Theme(
        name=name,
        primary=primary,
        secondary=palette.highlight,
        accent=palette.accent,
        warning=palette.amber,
        error=palette.error,
        success=palette.success,
        foreground=palette.foreground,
        background=palette.background,
        surface=palette.surface,
        panel=palette.surface_highlight,
        dark=dark,
        variables={
            "border": palette.accent,
            "border-blurred": palette.border,
            "block-cursor-background": palette.accent,
            "block-cursor-foreground": palette.chip_text,
            "block-cursor-text-style": "none",
            "button-color-foreground": palette.chip_text,
            "footer-key-foreground": palette.accent,
            "input-selection-background": palette.info + " 35%",
        },
    )


def code_ai_theme() -> Theme:
    """Code-AI's own look, as a Textual theme registered under ``monokai``."""
    palette = CODE_AI_PALETTE
    return _textual_theme(CODE_AI_THEME_NAME, palette, dark=True, primary=palette.info)


def spec_themes() -> list[Theme]:
    """Every curated theme as a Textual theme, in picker order."""
    themes = []
    for spec in THEME_SPECS.values():
        palette = _spec_palette(spec.name)
        themes.append(
            _textual_theme(
                spec.name, palette, dark=spec.dark, primary=spec.primary or palette.accent
            )
        )
    return themes


# --- Rich: code, answers and ANSI colours --------------------------------


# A foreground colour inside a Pygments style string ("italic #616e88"), as
# opposed to a background ("bg:#...") or a border ("border:#...").
_STYLE_COLOR = re.compile(r"(?<![:\w])#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b")


def _legible_tokens(styles: dict, background: str) -> dict:
    """Token styles with every colour lifted to read on ``background``.

    Several shipped styles draw comments too faint for a card that is lighter
    than the page they were designed for (nord's comments on nord1, say).
    """
    page = Color.parse(background)
    dark = _luminance(page) < 0.5

    def lift(match: re.Match[str]) -> str:
        return _legible(Color.parse(match.group(0)), page, 3.0, dark=dark).hex

    return {token: _STYLE_COLOR.sub(lift, value or "") for token, value in styles.items()}


@lru_cache(maxsize=64)
def _pygments_style(
    base: str, colors: SyntaxColors | None, background: str, foreground: str
) -> type:
    from pygments.style import Style
    from pygments.styles import get_style_by_name
    from pygments.token import (
        Comment,
        Error,
        Generic,
        Keyword,
        Name,
        Number,
        Operator,
        Punctuation,
        String,
        Text,
        Token,
    )

    if colors is None:
        # Pygments ships this theme's style: keep its tokens, paint it on the
        # palette's surface so a code block sits in the page like a card.
        parent = get_style_by_name(base)
        return type(
            f"CodeAI_{base}",
            (parent,),
            {
                "background_color": background,
                "styles": _legible_tokens(parent.styles, background),
            },
        )
    styles = {
        Token: foreground,
        Text: foreground,
        Error: foreground,
        Comment: f"italic {colors.comment}",
        Comment.Preproc: colors.keyword,
        Keyword: colors.keyword,
        Keyword.Constant: colors.number,
        Keyword.Type: colors.type,
        Operator: colors.operator,
        Operator.Word: colors.keyword,
        Punctuation: foreground,
        Name: foreground,
        Name.Attribute: colors.function,
        Name.Builtin: colors.builtin,
        Name.Builtin.Pseudo: f"italic {colors.builtin}",
        Name.Class: colors.type,
        Name.Constant: colors.number,
        Name.Decorator: colors.function,
        Name.Exception: colors.type,
        Name.Function: colors.function,
        Name.Function.Magic: colors.builtin,
        Name.Namespace: colors.type,
        Name.Tag: colors.keyword,
        Name.Variable: colors.variable,
        Number: colors.number,
        String: colors.string,
        String.Doc: f"italic {colors.comment}",
        String.Escape: colors.operator,
        String.Interpol: colors.operator,
        String.Regex: colors.operator,
        Generic.Deleted: colors.builtin,
        Generic.Inserted: colors.string,
        Generic.Heading: f"bold {colors.function}",
        Generic.Subheading: f"bold {colors.type}",
        Generic.Emph: "italic",
        Generic.Strong: "bold",
    }
    return type(
        "CodeAISyntax",
        (Style,),
        {"background_color": background, "styles": _legible_tokens(styles, background)},
    )


def syntax_theme(palette: Palette) -> str | type:
    """What Rich paints code with under this palette: a style name or class."""
    if palette.classic:
        return palette.syntax_theme
    return _pygments_style(
        palette.syntax_theme, palette.syntax_colors, palette.surface, palette.foreground
    )


def markdown_theme(palette: Palette) -> RichTheme | None:
    """Colours for the Markdown in answers; None keeps Rich's own."""
    if palette.classic:
        return None
    return RichTheme(
        {
            "markdown.code": f"bold {palette.info} on {palette.surface_highlight}",
            "markdown.code_block": f"{palette.foreground} on {palette.surface}",
            "markdown.block_quote": f"italic {palette.muted}",
            "markdown.list": palette.accent,
            "markdown.item.bullet": f"bold {palette.accent}",
            "markdown.item.number": f"bold {palette.accent}",
            "markdown.hr": palette.border_dim,
            "markdown.h1": f"bold {palette.accent}",
            "markdown.h1.border": palette.accent,
            "markdown.h2": f"bold underline {palette.accent}",
            "markdown.h3": f"bold {palette.highlight}",
            "markdown.h4": f"italic {palette.highlight}",
            "markdown.h5": f"italic {palette.muted}",
            "markdown.h6": f"italic {palette.trace}",
            "markdown.link": f"underline {palette.info}",
            "markdown.link_url": f"underline {palette.info}",
            "markdown.table.border": palette.border,
            "markdown.table.header": f"bold {palette.accent}",
        }
    )


def terminal_theme(palette: Palette, *, dark: bool) -> TerminalTheme:
    """Where ANSI-named colours in Rich output land under this palette."""

    def rgb(value: str) -> tuple[int, int, int]:
        return _rgb(Color.parse(value))

    black, white = (
        (palette.surface_highlight, palette.muted) if dark else (palette.foreground, palette.trace)
    )
    normal = [
        black,
        palette.error,
        palette.success,
        palette.amber,
        palette.accent,
        palette.highlight,
        palette.info,
        white,
    ]
    bright = (
        [palette.trace]
        + [_shift(Color.parse(value), 0.06 if dark else -0.06).hex for value in normal[1:-1]]
        + [palette.foreground]
    )
    return TerminalTheme(
        rgb(palette.background),
        rgb(palette.foreground),
        [rgb(value) for value in normal],
        [rgb(value) for value in bright],
    )


# CSS variable names are the field names with a ``ca-`` prefix, so the
# stylesheet and the dataclass can never drift apart: adding a slot here makes
# ``$ca-<slot>`` available in theme.tcss automatically.
_CSS_EXCLUDED = frozenset({"pulse_stops", "syntax_theme", "syntax_colors", "classic"})


def css_variables(palette: Palette) -> dict[str, str]:
    """The palette as ``$ca-*`` variables for the Textual stylesheet."""
    return {
        "ca-" + name.replace("_", "-"): getattr(palette, name)
        for name in Palette.__dataclass_fields__
        if name not in _CSS_EXCLUDED
    }


# The palette the Rich renderables draw with right now. Rich styles are built
# per render call, not per stylesheet parse, so they cannot read CSS variables;
# they read this instead, and the app repoints it whenever the theme changes.
_active: Palette = CODE_AI_PALETTE


def active_palette() -> Palette:
    """The palette in force for the current theme."""
    return _active


def set_active_palette(palette: Palette) -> None:
    """Point the Rich renderables at a new palette."""
    global _active
    _active = palette


def with_overrides(palette: Palette, **overrides: str) -> Palette:
    """A copy of ``palette`` with individual slots replaced (for tests)."""
    return replace(palette, **overrides)


class PaletteApp(App[ReturnType]):
    """An app whose stylesheet and renderables share one palette.

    Any app that loads ``theme.tcss`` has to mix this in: the stylesheet
    references ``$ca-*`` variables that Textual knows nothing about, so without
    it the CSS simply fails to parse. Registering Code-AI's themes here too
    means ``monokai`` and the curated themes resolve to their palettes wherever
    the stylesheet is used, rather than only inside the main app.
    """

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.register_theme(code_ai_theme())
        for theme in spec_themes():
            self.register_theme(theme)

    @property
    def ansi_theme(self) -> TerminalTheme:
        """ANSI colours in the theme's own hues rather than Textual's monokai map."""
        palette = palette_for_theme(self.current_theme)
        if palette.classic:
            return super().ansi_theme
        return terminal_theme(palette, dark=self.current_theme.dark)

    def get_css_variables(self) -> dict[str, str]:
        """Hand the stylesheet the whole palette, not just Textual's slice.

        Textual builds ``$primary``, ``$surface`` and friends from the theme;
        theme.tcss also needs the application-level colours (traces, diff rows,
        chips, panel frames). Deriving them here means every theme switch
        rebuilds them, and pointing the Rich renderables at the same palette in
        the same place keeps the stylesheet and the drawn text from disagreeing.
        """
        palette = palette_for_theme(self.current_theme)
        set_active_palette(palette)
        variables = super().get_css_variables()
        variables.update(css_variables(palette))
        self.theme_variables = variables
        return variables
