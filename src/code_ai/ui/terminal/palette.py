"""One palette for the whole terminal UI, derived from the active theme.

The screen used to hard-code every colour it drew: the stylesheet carried
hexes, and so did the plan panel, the diff, the question cards and the speaker
chips. Switching the Textual theme therefore repainted only the chrome Textual
owns (header, footer, buttons) and left the actual application looking exactly
the same - a "theme" that changed almost nothing.

Everything the UI paints now comes from one :class:`Palette`, and a palette
comes from a theme:

* ``monokai`` is Code-AI's own look. Its palette is the literal set of colours
  the tool shipped with, so choosing it restores today's screen exactly.
* every other theme derives its palette from that theme's own colours, so
  picking ``nord`` or ``solarized-light`` recolours the whole application -
  borders, traces, diffs, chips and the banner included - instead of only the
  parts Textual draws for us.

The palette is exposed twice, because the UI paints in two ways: as CSS
variables (``$ca-*``) for the stylesheet, and as a process-wide "active
palette" the Rich renderables read at draw time.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TypeVar

from textual.app import App
from textual.color import Color
from textual.theme import Theme

# The theme name that carries Code-AI's original colours. Registering a theme
# under a built-in name overrides it, which is deliberate here: this *is* the
# tool's default look, and it needs a slot in the theme picker.
CODE_AI_THEME_NAME = "monokai"

# Mirrors textual.app.App's own type parameter: what the app returns on exit.
ReturnType = TypeVar("ReturnType")


@dataclass(frozen=True, slots=True)
class Palette:
    """Every colour the terminal UI is allowed to use, named by its job.

    Slots are semantic rather than literal ("trace", not "gray"), so a derived
    theme only has to answer "what is the dim colour here?" once and every
    surface that needs a dim colour follows.
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

    # Pygments style for source previews. Not a colour, but it is the one
    # remaining thing that has to change with the theme's light/dark polarity.
    syntax_theme: str


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
)


def _hex(color: Color) -> str:
    """A plain ``#rrggbb`` string, which is what both CSS and Rich accept."""
    return color.hex


def _solid(color: Color, fallback: Color) -> Color:
    """A colour with real channels behind it, blendable and printable.

    The ``ansi-*`` themes name their colours by ANSI slot rather than by value:
    ``ansi_default`` has no colour at all (it means "whatever the terminal
    uses") and the surfaces come through transparent. Those cannot be blended
    into a ramp, and the two renderers that read this palette do not even spell
    ANSI names the same way, so each one is resolved to the concrete colour it
    stands for and anything with nothing behind it falls back to the page.
    """
    if color.ansi is not None:
        # A negative slot is "terminal default"; a real slot carries the
        # standard RGB for that ANSI colour.
        return fallback if color.ansi < 0 else Color(color.r, color.g, color.b)
    if not color.a:
        return fallback
    return color


def derive_palette(theme: Theme) -> Palette:
    """Build a full palette out of the handful of colours a theme declares.

    A Textual theme names about ten colours. The UI needs forty. The gap is
    closed by blending rather than by inventing: every dim tone is the theme's
    own foreground faded towards its own background, and every tinted surface
    is the background nudged towards the accent that belongs there. That keeps
    a derived palette inside the theme's world and - because the ramp is
    expressed as "towards the background" rather than "darker" - makes light
    themes come out right without a second code path.
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
    primary = base("primary", fg)
    secondary = base("secondary", primary)
    accent = base("accent", primary)
    success = base("success", fg)
    warning = base("warning", accent)
    error = base("error", fg)

    # The text ramp: 0.0 is full foreground, 1.0 disappears into the
    # background. Every dim tone in the UI is a stop on this one line.
    def fade(amount: float) -> str:
        return _hex(fg.blend(bg, amount))

    # A surface tinted towards a colour - how the diff rows, the focused
    # terminal frame and the hover states get their background.
    def tint(color: Color, amount: float) -> str:
        return _hex(bg.blend(color, amount))

    return Palette(
        background=_hex(bg),
        input_background=tint(fg, 0.05),
        surface=_hex(surface),
        surface_highlight=_hex(surface.blend(fg, 0.10)),
        hover_background=_hex(surface.blend(fg, 0.07)),
        terminal_focus_background=tint(success, 0.10),
        border=fade(0.45),
        border_dim=fade(0.80),
        card_border=fade(0.76),
        foreground=_hex(fg),
        muted=fade(0.30),
        subtle=fade(0.38),
        trace=fade(0.55),
        done=fade(0.48),
        faint=fade(0.66),
        gutter=fade(0.62),
        idle=fade(0.82),
        accent=_hex(accent),
        amber=_hex(warning.blend(fg, 0.15)),
        success=_hex(success),
        success_bright=_hex(success.blend(fg, 0.30)),
        success_border=_hex(success.blend(bg, 0.55)),
        success_subtitle=_hex(success.blend(bg, 0.40)),
        command_text=_hex(fg.blend(success, 0.30).blend(bg, 0.20)),
        error=_hex(error),
        error_soft=_hex(error.blend(fg, 0.45)),
        info=_hex(primary),
        highlight=_hex(secondary.blend(fg, 0.20)),
        # Chip labels sit on a saturated fill, so they take the page colour
        # back: dark text on a bright chip, light text on a dark one.
        chip_text=_hex(bg),
        diff_add_fg=_hex(success.blend(fg, 0.40)),
        diff_add_bg=tint(success, 0.22),
        diff_del_fg=_hex(error.blend(fg, 0.40)),
        diff_del_bg=tint(error, 0.22),
        logo_start=_hex(error.blend(fg, 0.20)),
        logo_end=_hex(warning.blend(fg, 0.20)),
        # The spinner walks error -> accent -> warning -> accent, which is the
        # same red/orange/yellow shape the original ramp had, in the theme's
        # own colours.
        pulse_stops=(
            (error.r, error.g, error.b),
            (accent.r, accent.g, accent.b),
            (warning.r, warning.g, warning.b),
            (accent.r, accent.g, accent.b),
        ),
        # Pygments has no notion of our themes; picking by polarity is the one
        # decision that actually matters, since a dark style on a light page is
        # unreadable either way round.
        syntax_theme="monokai" if theme.dark else "friendly",
    )


def palette_for_theme(theme: Theme) -> Palette:
    """The palette a theme should paint with."""
    if theme.name == CODE_AI_THEME_NAME:
        return CODE_AI_PALETTE
    return derive_palette(theme)


def code_ai_theme() -> Theme:
    """Code-AI's own look, as a Textual theme.

    Registered under ``monokai`` so the chrome Textual draws for us (header,
    footer, buttons, scrollbars, selection) matches the colours the stylesheet
    and the Rich renderables use, instead of being the one part of the screen
    that follows a different palette.
    """
    palette = CODE_AI_PALETTE
    return Theme(
        name=CODE_AI_THEME_NAME,
        primary=palette.info,
        secondary=palette.highlight,
        accent=palette.accent,
        warning=palette.amber,
        error=palette.error,
        success=palette.success,
        foreground=palette.foreground,
        background=palette.background,
        surface=palette.surface,
        panel=palette.surface_highlight,
        dark=True,
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


# CSS variable names are the field names with a ``ca-`` prefix, so the
# stylesheet and the dataclass can never drift apart: adding a slot here makes
# ``$ca-<slot>`` available in theme.tcss automatically.
_CSS_EXCLUDED = frozenset({"pulse_stops", "syntax_theme"})


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
    it the CSS simply fails to parse. Registering Code-AI's own theme here too
    means ``monokai`` resolves to the tool's palette wherever the stylesheet
    is used, rather than only inside the main app.
    """

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.register_theme(code_ai_theme())

    def get_css_variables(self) -> dict[str, str]:
        """Hand the stylesheet the whole palette, not just Textual's slice.

        Textual builds ``$primary``, ``$surface`` and friends from the theme;
        theme.tcss also needs the application-level colours (traces, diff rows,
        chips, panel frames) that used to be hard-coded hexes. Deriving them
        here means every theme switch rebuilds them, and pointing the Rich
        renderables at the same palette in the same place keeps the stylesheet
        and the drawn text from ever disagreeing.
        """
        palette = palette_for_theme(self.current_theme)
        set_active_palette(palette)
        variables = super().get_css_variables()
        variables.update(css_variables(palette))
        self.theme_variables = variables
        return variables
