"""Choosing a theme has to recolour the tool, not just the chrome around it."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pygments.token import Comment
from textual.content import Content
from textual.theme import BUILTIN_THEMES
from textual.widgets import Static

from code_ai.ui.terminal.app import create_terminal_app
from code_ai.ui.terminal.palette import (
    CODE_AI_PALETTE,
    CODE_AI_THEME_NAME,
    Palette,
    code_ai_theme,
    contrast_ratio,
    css_variables,
    derive_palette,
    markdown_theme,
    palette_for_theme,
    set_active_palette,
    spec_themes,
    syntax_theme,
)
from code_ai.ui.terminal.slash_commands import TERMINAL_THEME_SUGGESTIONS
from code_ai.ui.terminal.themes import THEME_SPECS
from code_ai.ui.terminal.widgets import markdown_to_content
from tests.unit.test_terminal_ui import FakeTerminalApplication

THEME_CSS = Path("src/code_ai/ui/terminal/theme.tcss").resolve()
# A literal colour in the stylesheet, e.g. "#ff9f1c" - as opposed to an id
# selector like "#logo", which is also spelled with a hash.
HEX_LITERAL = re.compile(r"#[0-9a-fA-F]{3,8}\b(?![-\w])")
# Slots that are not colours.
NON_COLOURS = {"pulse_stops", "syntax_theme", "syntax_colors", "classic"}
CURATED = sorted(THEME_SPECS)


def _theme(name: str):
    return next(theme for theme in spec_themes() if theme.name == name)


def test_the_stylesheet_names_colours_instead_of_hard_coding_them() -> None:
    # A literal hex in the stylesheet is a colour no theme can reach, which is
    # exactly the bug this palette exists to fix.
    css = THEME_CSS.read_text(encoding="utf-8")

    assert not HEX_LITERAL.findall(css)
    assert "$ca-accent" in css
    assert "$ca-background" in css


def test_monokai_is_the_look_code_ai_shipped_with() -> None:
    palette = palette_for_theme(code_ai_theme())

    assert palette is CODE_AI_PALETTE
    assert palette.background == "#071018"
    assert palette.accent == "#ff9f1c"
    assert palette.success == "#48d17a"
    assert palette.syntax_theme == "monokai"
    # The chip label is the page colour, so it reads on a bright fill.
    assert palette.chip_text == palette.background
    # Answers keep Rich's own markdown colours and monokai code.
    assert markdown_theme(palette) is None
    assert syntax_theme(palette) == "monokai"


def test_code_ai_theme_hands_its_colours_to_textuals_own_chrome() -> None:
    # Buttons, the footer and the selection are drawn by Textual from the
    # theme, not by our stylesheet: if the theme did not carry the palette they
    # would be the one part of the screen following different colours.
    theme = code_ai_theme()

    assert theme.name == CODE_AI_THEME_NAME
    assert theme.background == CODE_AI_PALETTE.background
    assert theme.accent == CODE_AI_PALETTE.accent
    assert theme.variables["footer-key-foreground"] == CODE_AI_PALETTE.accent


def test_the_github_themes_are_offered() -> None:
    for name in ("github-dark", "github-dark-dimmed", "github-light"):
        assert name in THEME_SPECS
        assert name in TERMINAL_THEME_SUGGESTIONS
    assert TERMINAL_THEME_SUGGESTIONS[0] == CODE_AI_THEME_NAME


@pytest.mark.parametrize("name", CURATED)
def test_a_curated_theme_is_painted_in_its_own_colours(name: str) -> None:
    spec = THEME_SPECS[name]
    palette = palette_for_theme(_theme(name))

    for field in Palette.__dataclass_fields__:
        if field not in NON_COLOURS:
            assert getattr(palette, field).startswith("#"), f"{name}.{field}"
    assert palette.background == spec.background
    assert palette.foreground == spec.foreground
    # The banner and the spinner are the theme's, not Code-AI's red-to-yellow.
    assert (palette.logo_start, palette.logo_end) == spec.logo
    assert palette.pulse_stops != CODE_AI_PALETTE.pulse_stops
    assert not palette.classic


@pytest.mark.parametrize("name", CURATED)
def test_a_curated_theme_reads_on_its_own_page(name: str) -> None:
    palette = palette_for_theme(_theme(name))
    page = palette.background

    def ratio(slot: str, against: str = page) -> float:
        return contrast_ratio(getattr(palette, slot), against)

    assert ratio("foreground") >= 4.5
    for slot in ("muted", "error_soft", "command_text"):
        assert ratio(slot) >= 4.5, slot
    for slot in ("label", "subtle", "done"):
        assert ratio(slot) >= 3.5, slot
    for slot in ("trace", "accent", "info", "highlight", "success", "amber", "error"):
        assert ratio(slot) >= 3.0, slot
    for slot in ("faint", "gutter"):
        assert ratio(slot) >= 2.3, slot
    assert ratio("border") >= 1.8
    # Chips carry the user's and the model's names.
    assert ratio("chip_text", palette.success) >= 3.0
    assert ratio("chip_text", palette.accent) >= 3.0
    assert ratio("diff_add_fg", palette.diff_add_bg) >= 4.5
    assert ratio("diff_del_fg", palette.diff_del_bg) >= 4.5


@pytest.mark.parametrize("name", CURATED)
def test_code_is_painted_in_the_theme_and_its_comments_read(name: str) -> None:
    palette = palette_for_theme(_theme(name))
    style = syntax_theme(palette)

    assert not isinstance(style, str)
    assert style.background_color == palette.surface
    comment = style.style_for_token(Comment)["color"]
    assert contrast_ratio("#" + comment, palette.surface) >= 3.0


def test_answers_use_the_theme_for_markdown_and_code() -> None:
    palette = palette_for_theme(_theme("dracula"))
    set_active_palette(palette)
    try:
        rendered = markdown_to_content("use `x`\n\n```python\nx = 1\n```", 40)
    finally:
        set_active_palette(CODE_AI_PALETTE)

    def rgb(value: str) -> str:
        return "rgb({},{},{})".format(*(int(value[i : i + 2], 16) for i in (1, 3, 5)))

    styles = " ".join(str(span.style) for span in rendered.spans)
    # Inline code in the theme's info colour, the block on its surface.
    assert rgb(palette.info) in styles
    assert rgb(palette.surface) in styles


@pytest.mark.parametrize("theme_name", sorted(BUILTIN_THEMES))
def test_every_other_theme_derives_a_complete_palette(theme_name: str) -> None:
    if theme_name == CODE_AI_THEME_NAME:
        pytest.skip("monokai declares its palette rather than deriving one")
    palette = derive_palette(BUILTIN_THEMES[theme_name])

    for field in Palette.__dataclass_fields__:
        if field not in NON_COLOURS:
            assert getattr(palette, field), f"{theme_name}.{field} is empty"
    assert len(palette.pulse_stops) == 4
    # Code colours come from the theme too, never from Pygments' monokai.
    assert palette.syntax_colors is not None
    if not BUILTIN_THEMES[theme_name].ansi:
        declared = BUILTIN_THEMES[theme_name].to_color_system().generate()
        assert palette.background == declared["background"].lower()


def test_a_derived_palette_stays_inside_its_own_theme() -> None:
    nord = derive_palette(BUILTIN_THEMES["nord"])

    # The colours that carry meaning come straight from the theme...
    assert nord.accent.lower() == "#b48ead"
    assert nord.success.lower() == "#a3be8c"
    assert nord.background.lower() == "#2e3440"
    # ...and none of Code-AI's own colours survive.
    assert nord.trace != CODE_AI_PALETTE.trace
    assert nord.diff_add_bg != CODE_AI_PALETTE.diff_add_bg
    assert nord.logo_start != CODE_AI_PALETTE.logo_start
    assert nord.chip_text == nord.background


def test_a_light_theme_gets_a_light_palette() -> None:
    # The dim ramp is "towards the background", not "darker", so a light theme
    # does not come out with black-on-black traces or a dark code preview.
    light = derive_palette(BUILTIN_THEMES["textual-light"])

    assert light.surface != CODE_AI_PALETTE.surface
    assert light.trace != light.foreground
    assert contrast_ratio(light.trace, light.background) >= 3.0
    assert syntax_theme(light).background_color == light.surface


@pytest.mark.parametrize("theme_name", ["ansi-dark", "ansi-light"])
def test_an_ansi_theme_still_yields_colours_that_can_be_drawn(theme_name: str) -> None:
    # These themes name their colours by ANSI slot and leave the page to the
    # terminal, so there is nothing to blend a dim ramp out of and nothing the
    # Rich renderables could print. Each slot has to come out as a real value.
    palette = derive_palette(BUILTIN_THEMES[theme_name])

    for field in Palette.__dataclass_fields__:
        if field in NON_COLOURS:
            continue
        value = getattr(palette, field)
        assert value.startswith("#"), f"{field} is not a drawable colour: {value}"
    assert palette.background != palette.foreground


@pytest.mark.parametrize("theme_name", sorted(BUILTIN_THEMES))
def test_the_dim_ramp_never_reaches_the_page(theme_name: str) -> None:
    # Every dim tone is the foreground faded towards the background. Faded far
    # enough it *becomes* the background, which is how a "theme" turns into
    # invisible text; the ramp has to stop short of that everywhere.
    palette = derive_palette(BUILTIN_THEMES[theme_name])

    for field in ("muted", "subtle", "trace", "done", "faint", "gutter", "label"):
        assert getattr(palette, field) != palette.background, field


def test_every_palette_slot_reaches_the_stylesheet() -> None:
    variables = css_variables(CODE_AI_PALETTE)

    assert variables["ca-accent"] == CODE_AI_PALETTE.accent
    assert variables["ca-diff-add-bg"] == CODE_AI_PALETTE.diff_add_bg
    assert variables["ca-label"] == CODE_AI_PALETTE.label
    # The non-colour slots are for the Rich side only.
    for name in ("ca-pulse-stops", "ca-syntax-theme", "ca-syntax-colors", "ca-classic"):
        assert name not in variables


def _chip_styles(rendered: Content) -> str:
    return " ".join(str(span.style) for span in rendered.spans)


async def test_switching_the_theme_repaints_the_conversation(tmp_path) -> None:
    from code_ai.ui.terminal.widgets import render_conversation_line

    fake_app = FakeTerminalApplication(tmp_path)
    fake_app.session.config.terminal_theme = CODE_AI_THEME_NAME
    terminal_app = create_terminal_app(fake_app, config_path=tmp_path / "config.json")

    async with terminal_app.run_test(size=(100, 40)) as pilot:
        terminal_app.vm.conversation.append("you> ola")
        terminal_app._sync_conversation()
        await pilot.pause()

        before = render_conversation_line("you> ola")
        assert CODE_AI_PALETTE.success in _chip_styles(before)

        terminal_app.theme = "dracula"
        await pilot.pause(0.2)

        dracula = palette_for_theme(terminal_app.current_theme)
        after = render_conversation_line("you> ola")
        assert dracula.success in _chip_styles(after)
        # The transcript was rebuilt rather than left holding the old colours.
        mounted = terminal_app.query("#conversation Static")
        assert any(
            dracula.success in _chip_styles(widget.render())
            for widget in mounted
            if isinstance(widget.render(), Content)
        )


async def test_switching_the_theme_recolours_the_stylesheet(tmp_path) -> None:
    fake_app = FakeTerminalApplication(tmp_path)
    fake_app.session.config.terminal_theme = CODE_AI_THEME_NAME
    terminal_app = create_terminal_app(fake_app, config_path=tmp_path / "config.json")

    async with terminal_app.run_test(size=(100, 40)) as pilot:
        logo = terminal_app.query_one("#logo", Static)
        assert logo.styles.color.hex.lower() == CODE_AI_PALETTE.accent

        terminal_app.theme = "github-dark"
        await pilot.pause(0.2)

        github = palette_for_theme(terminal_app.current_theme)
        assert github.accent == THEME_SPECS["github-dark"].accent
        assert terminal_app.query_one("#logo", Static).styles.color.hex.lower() == (
            github.accent.lower()
        )


async def test_ansi_colours_follow_the_theme(tmp_path) -> None:
    fake_app = FakeTerminalApplication(tmp_path)
    fake_app.session.config.terminal_theme = CODE_AI_THEME_NAME
    terminal_app = create_terminal_app(fake_app, config_path=tmp_path / "config.json")

    async with terminal_app.run_test(size=(100, 40)) as pilot:
        # Code-AI's own look keeps Textual's map.
        monokai_red = terminal_app.ansi_theme.ansi_colors[1]

        terminal_app.theme = "nord"
        await pilot.pause(0.2)

        nord = palette_for_theme(terminal_app.current_theme)
        red = terminal_app.ansi_theme.ansi_colors[1]
        assert "#{:02x}{:02x}{:02x}".format(*red) == nord.error
        assert tuple(red) != tuple(monokai_red)
