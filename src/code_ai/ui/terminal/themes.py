"""The terminal themes, each described by the colours it is known by.

A theme used to be Textual's handful of colours stretched over forty slots by
blending, and the blending was the same for every theme: the banner went red to
yellow, the spinner pulsed through fire, code was painted by Pygments'
``monokai`` and every accent was mixed towards the text colour. Every theme
came out as a faded copy of Code-AI's own look.

Each spec here names the colours its theme is recognised by - its dim tones,
its signature hues, its banner pair and its code colours - and
:mod:`code_ai.ui.terminal.palette` builds the full palette from them.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SyntaxColors:
    """Token colours for themes that Pygments ships no style for."""

    comment: str
    keyword: str
    string: str
    number: str
    function: str
    type: str
    builtin: str
    variable: str
    operator: str


@dataclass(frozen=True, slots=True)
class ThemeSpec:
    """The colours a theme is known by; the rest of its palette is built from them."""

    name: str
    dark: bool
    background: str
    # Code blocks, dialogs and the live code window.
    surface: str
    # Raised rows: hovered and focused cards, the header bar.
    panel: str
    foreground: str
    # Secondary text, then the dim work trace.
    muted: str
    trace: str
    border: str
    border_dim: str
    # Frames, titles and the model's chip.
    accent: str
    # Tool names and links.
    info: str
    # Hover frames and diff hunk headers.
    highlight: str
    success: str
    warning: str
    error: str
    # The banner's two rows, bottom first. The spinner pulses between them.
    logo: tuple[str, str]
    # A Pygments style name, or token colours where Pygments has none.
    syntax: str | SyntaxColors
    # Textual's primary (buttons, tabs, scrollbars). Defaults to the accent.
    primary: str = ""


_GITHUB_DARK = ThemeSpec(
    name="github-dark",
    dark=True,
    background="#0d1117",
    surface="#161b22",
    panel="#21262d",
    foreground="#e6edf3",
    muted="#9198a1",
    trace="#6e7681",
    border="#3d444d",
    border_dim="#262c36",
    accent="#4493f8",
    info="#d2a8ff",
    highlight="#79c0ff",
    success="#3fb950",
    warning="#d29922",
    error="#f85149",
    logo=("#a371f7", "#4493f8"),
    syntax="github-dark",
)

_GITHUB_DARK_DIMMED = ThemeSpec(
    name="github-dark-dimmed",
    dark=True,
    background="#22272e",
    surface="#2d333b",
    panel="#373e47",
    foreground="#adbac7",
    muted="#909dab",
    trace="#768390",
    border="#545d68",
    border_dim="#373e47",
    accent="#539bf5",
    info="#dcbdfb",
    highlight="#6cb6ff",
    success="#57ab5a",
    warning="#c69026",
    error="#e5534b",
    logo=("#986ee2", "#539bf5"),
    syntax=SyntaxColors(
        comment="#768390",
        keyword="#f47067",
        string="#96d0ff",
        number="#6cb6ff",
        function="#dcbdfb",
        type="#f69d50",
        builtin="#6cb6ff",
        variable="#f69d50",
        operator="#f47067",
    ),
)

_GITHUB_LIGHT = ThemeSpec(
    name="github-light",
    dark=False,
    background="#ffffff",
    surface="#f6f8fa",
    panel="#eaeef2",
    foreground="#1f2328",
    muted="#59636e",
    trace="#6e7781",
    border="#afb8c1",
    border_dim="#d0d7de",
    accent="#0969da",
    info="#8250df",
    highlight="#0550ae",
    success="#1a7f37",
    warning="#9a6700",
    error="#d1242f",
    logo=("#8250df", "#0969da"),
    syntax=SyntaxColors(
        comment="#6e7781",
        keyword="#cf222e",
        string="#0a3069",
        number="#0550ae",
        function="#8250df",
        type="#953800",
        builtin="#0550ae",
        variable="#953800",
        operator="#cf222e",
    ),
)

_TEXTUAL_DARK = ThemeSpec(
    name="textual-dark",
    dark=True,
    background="#121212",
    surface="#1e1e1e",
    panel="#242f38",
    foreground="#e0e0e0",
    muted="#a8a8a8",
    trace="#7a7a7a",
    border="#4a4a4a",
    border_dim="#2e2e2e",
    accent="#0178d4",
    info="#4fb3ff",
    highlight="#ffa62b",
    success="#4ebf71",
    warning="#ffa62b",
    error="#e0526f",
    logo=("#0178d4", "#ffa62b"),
    # VS Code's Dark+, the look Textual's own editor widget ships with.
    syntax=SyntaxColors(
        comment="#6a9955",
        keyword="#569cd6",
        string="#ce9178",
        number="#b5cea8",
        function="#dcdcaa",
        type="#4ec9b0",
        builtin="#4ec9b0",
        variable="#9cdcfe",
        operator="#d4d4d4",
    ),
)

_TEXTUAL_LIGHT = ThemeSpec(
    name="textual-light",
    dark=False,
    background="#e0e0e0",
    surface="#ececec",
    panel="#d0d0d0",
    foreground="#1f1f1f",
    muted="#4f4f4f",
    trace="#6b6b6b",
    border="#9e9e9e",
    border_dim="#c4c4c4",
    accent="#004578",
    info="#0167b4",
    highlight="#b35900",
    success="#1e7d3c",
    warning="#9c5d00",
    error="#ba3c5b",
    logo=("#004578", "#0178d4"),
    # VS Code's Light+.
    syntax=SyntaxColors(
        comment="#008000",
        keyword="#0000ff",
        string="#a31515",
        number="#098658",
        function="#795e26",
        type="#267f99",
        builtin="#267f99",
        variable="#001080",
        operator="#1f1f1f",
    ),
)

_TOKYO_NIGHT = ThemeSpec(
    name="tokyo-night",
    dark=True,
    background="#1a1b26",
    surface="#1f2335",
    panel="#292e42",
    foreground="#c0caf5",
    muted="#a9b1d6",
    trace="#737aa2",
    border="#545c7e",
    border_dim="#3b4261",
    accent="#7aa2f7",
    info="#7dcfff",
    highlight="#bb9af7",
    success="#9ece6a",
    warning="#e0af68",
    error="#f7768e",
    logo=("#bb9af7", "#7aa2f7"),
    syntax=SyntaxColors(
        comment="#565f89",
        keyword="#bb9af7",
        string="#9ece6a",
        number="#ff9e64",
        function="#7aa2f7",
        type="#2ac3de",
        builtin="#2ac3de",
        variable="#e0af68",
        operator="#89ddff",
    ),
)

_DRACULA = ThemeSpec(
    name="dracula",
    dark=True,
    background="#282a36",
    surface="#21222c",
    panel="#44475a",
    foreground="#f8f8f2",
    muted="#c3c6d9",
    trace="#7c88b8",
    border="#6272a4",
    border_dim="#44475a",
    accent="#bd93f9",
    info="#8be9fd",
    highlight="#ff79c6",
    success="#50fa7b",
    warning="#ffb86c",
    error="#ff5555",
    logo=("#bd93f9", "#ff79c6"),
    syntax="dracula",
)

_NORD = ThemeSpec(
    name="nord",
    dark=True,
    background="#2e3440",
    surface="#3b4252",
    panel="#434c5e",
    foreground="#d8dee9",
    muted="#b4bccb",
    trace="#8a94a8",
    border="#616e88",
    border_dim="#4c566a",
    accent="#88c0d0",
    info="#81a1c1",
    highlight="#b48ead",
    success="#a3be8c",
    warning="#ebcb8b",
    error="#bf616a",
    logo=("#5e81ac", "#88c0d0"),
    syntax="nord",
)

_GRUVBOX = ThemeSpec(
    name="gruvbox",
    dark=True,
    background="#282828",
    surface="#32302f",
    panel="#504945",
    foreground="#ebdbb2",
    muted="#bdae93",
    trace="#928374",
    border="#7c6f64",
    border_dim="#504945",
    accent="#fabd2f",
    info="#8ec07c",
    highlight="#d3869b",
    success="#b8bb26",
    warning="#fe8019",
    error="#fb4934",
    logo=("#fe8019", "#fabd2f"),
    syntax="gruvbox-dark",
)

_CATPPUCCIN_MOCHA = ThemeSpec(
    name="catppuccin-mocha",
    dark=True,
    background="#1e1e2e",
    surface="#181825",
    panel="#313244",
    foreground="#cdd6f4",
    muted="#a6adc8",
    trace="#7f849c",
    border="#585b70",
    border_dim="#45475a",
    accent="#cba6f7",
    info="#89b4fa",
    highlight="#f5c2e7",
    success="#a6e3a1",
    warning="#f9e2af",
    error="#f38ba8",
    logo=("#cba6f7", "#f5c2e7"),
    syntax=SyntaxColors(
        comment="#9399b2",
        keyword="#cba6f7",
        string="#a6e3a1",
        number="#fab387",
        function="#89b4fa",
        type="#f9e2af",
        builtin="#f38ba8",
        variable="#eba0ac",
        operator="#89dceb",
    ),
)

_CATPPUCCIN_MACCHIATO = ThemeSpec(
    name="catppuccin-macchiato",
    dark=True,
    background="#24273a",
    surface="#1e2030",
    panel="#363a4f",
    foreground="#cad3f5",
    muted="#a5adcb",
    trace="#8087a2",
    border="#5b6078",
    border_dim="#494d64",
    accent="#c6a0f6",
    info="#8aadf4",
    highlight="#f5bde6",
    success="#a6da95",
    warning="#eed49f",
    error="#ed8796",
    logo=("#c6a0f6", "#f5bde6"),
    syntax=SyntaxColors(
        comment="#939ab7",
        keyword="#c6a0f6",
        string="#a6da95",
        number="#f5a97f",
        function="#8aadf4",
        type="#eed49f",
        builtin="#ed8796",
        variable="#ee99a0",
        operator="#91d7e3",
    ),
)

_CATPPUCCIN_FRAPPE = ThemeSpec(
    name="catppuccin-frappe",
    dark=True,
    background="#303446",
    surface="#292c3c",
    panel="#414559",
    foreground="#c6d0f5",
    muted="#a5adce",
    trace="#838ba7",
    border="#626880",
    border_dim="#51576d",
    accent="#ca9ee6",
    info="#8caaee",
    highlight="#f4b8e4",
    success="#a6d189",
    warning="#e5c890",
    error="#e78284",
    logo=("#ca9ee6", "#f4b8e4"),
    syntax=SyntaxColors(
        comment="#949cbb",
        keyword="#ca9ee6",
        string="#a6d189",
        number="#ef9f76",
        function="#8caaee",
        type="#e5c890",
        builtin="#e78284",
        variable="#ea999c",
        operator="#99d1db",
    ),
)

_CATPPUCCIN_LATTE = ThemeSpec(
    name="catppuccin-latte",
    dark=False,
    background="#eff1f5",
    surface="#e6e9ef",
    panel="#ccd0da",
    foreground="#4c4f69",
    muted="#5c5f77",
    trace="#7c7f93",
    border="#9ca0b0",
    border_dim="#bcc0cc",
    accent="#8839ef",
    info="#1e66f5",
    highlight="#ea76cb",
    success="#40a02b",
    warning="#df8e1d",
    error="#d20f39",
    logo=("#8839ef", "#ea76cb"),
    syntax=SyntaxColors(
        comment="#7c7f93",
        keyword="#8839ef",
        string="#40a02b",
        number="#fe640b",
        function="#1e66f5",
        type="#df8e1d",
        builtin="#d20f39",
        variable="#e64553",
        operator="#04a5e5",
    ),
)

_SOLARIZED_DARK = ThemeSpec(
    name="solarized-dark",
    dark=True,
    background="#002b36",
    surface="#073642",
    panel="#0b4050",
    foreground="#93a1a1",
    muted="#839496",
    trace="#657b83",
    border="#586e75",
    border_dim="#0b4050",
    accent="#268bd2",
    info="#2aa198",
    highlight="#6c71c4",
    success="#859900",
    warning="#b58900",
    error="#dc322f",
    logo=("#268bd2", "#2aa198"),
    syntax="solarized-dark",
)

_SOLARIZED_LIGHT = ThemeSpec(
    name="solarized-light",
    dark=False,
    background="#fdf6e3",
    surface="#eee8d5",
    panel="#e4ddc8",
    foreground="#586e75",
    muted="#657b83",
    trace="#839496",
    border="#93a1a1",
    border_dim="#e4ddc8",
    accent="#268bd2",
    info="#2aa198",
    highlight="#6c71c4",
    success="#859900",
    warning="#b58900",
    error="#dc322f",
    logo=("#268bd2", "#2aa198"),
    syntax="solarized-light",
)

_ATOM_ONE_DARK = ThemeSpec(
    name="atom-one-dark",
    dark=True,
    background="#282c34",
    surface="#21252b",
    panel="#3e4451",
    foreground="#abb2bf",
    muted="#9da5b4",
    trace="#7f848e",
    border="#5c6370",
    border_dim="#3e4451",
    accent="#61afef",
    info="#56b6c2",
    highlight="#c678dd",
    success="#98c379",
    warning="#e5c07b",
    error="#e06c75",
    logo=("#c678dd", "#61afef"),
    syntax="one-dark",
)

_ATOM_ONE_LIGHT = ThemeSpec(
    name="atom-one-light",
    dark=False,
    background="#fafafa",
    surface="#f0f0f0",
    panel="#e5e5e6",
    foreground="#383a42",
    muted="#4f525e",
    trace="#696c77",
    border="#a0a1a7",
    border_dim="#dbdbdc",
    accent="#4078f2",
    info="#0184bc",
    highlight="#a626a4",
    success="#50a14f",
    warning="#c18401",
    error="#e45649",
    logo=("#a626a4", "#4078f2"),
    syntax=SyntaxColors(
        comment="#a0a1a7",
        keyword="#a626a4",
        string="#50a14f",
        number="#986801",
        function="#4078f2",
        type="#c18401",
        builtin="#0184bc",
        variable="#e45649",
        operator="#0184bc",
    ),
)

_ROSE_PINE = ThemeSpec(
    name="rose-pine",
    dark=True,
    background="#191724",
    surface="#1f1d2e",
    panel="#26233a",
    foreground="#e0def4",
    muted="#908caa",
    trace="#6e6a86",
    border="#524f67",
    border_dim="#403d52",
    accent="#ebbcba",
    info="#c4a7e7",
    highlight="#31748f",
    success="#9ccfd8",
    warning="#f6c177",
    error="#eb6f92",
    logo=("#c4a7e7", "#ebbcba"),
    syntax=SyntaxColors(
        comment="#6e6a86",
        keyword="#31748f",
        string="#f6c177",
        number="#f6c177",
        function="#ebbcba",
        type="#9ccfd8",
        builtin="#eb6f92",
        variable="#c4a7e7",
        operator="#908caa",
    ),
)

_ROSE_PINE_MOON = ThemeSpec(
    name="rose-pine-moon",
    dark=True,
    background="#232136",
    surface="#2a273f",
    panel="#393552",
    foreground="#e0def4",
    muted="#908caa",
    trace="#6e6a86",
    border="#56526e",
    border_dim="#44415a",
    accent="#ea9a97",
    info="#c4a7e7",
    highlight="#3e8fb0",
    success="#9ccfd8",
    warning="#f6c177",
    error="#eb6f92",
    logo=("#c4a7e7", "#ea9a97"),
    syntax=SyntaxColors(
        comment="#6e6a86",
        keyword="#3e8fb0",
        string="#f6c177",
        number="#f6c177",
        function="#ea9a97",
        type="#9ccfd8",
        builtin="#eb6f92",
        variable="#c4a7e7",
        operator="#908caa",
    ),
)

_ROSE_PINE_DAWN = ThemeSpec(
    name="rose-pine-dawn",
    dark=False,
    background="#faf4ed",
    surface="#fffaf3",
    panel="#f2e9e1",
    foreground="#575279",
    muted="#797593",
    trace="#9893a5",
    border="#9893a5",
    border_dim="#dfdad9",
    accent="#d7827e",
    info="#907aa9",
    highlight="#286983",
    success="#56949f",
    warning="#ea9d34",
    error="#b4637a",
    logo=("#907aa9", "#d7827e"),
    syntax=SyntaxColors(
        comment="#9893a5",
        keyword="#286983",
        string="#ea9d34",
        number="#ea9d34",
        function="#d7827e",
        type="#56949f",
        builtin="#b4637a",
        variable="#907aa9",
        operator="#797593",
    ),
)

_FLEXOKI = ThemeSpec(
    name="flexoki",
    dark=True,
    background="#100f0f",
    surface="#1c1b1a",
    panel="#282726",
    foreground="#cecdc3",
    muted="#b7b5ac",
    trace="#878580",
    border="#575653",
    border_dim="#343331",
    accent="#3aa99f",
    info="#4385be",
    highlight="#8b7ec8",
    success="#879a39",
    warning="#d0a215",
    error="#d14d41",
    logo=("#8b7ec8", "#3aa99f"),
    syntax=SyntaxColors(
        comment="#878580",
        keyword="#879a39",
        string="#3aa99f",
        number="#8b7ec8",
        function="#da702c",
        type="#d0a215",
        builtin="#ce5d97",
        variable="#4385be",
        operator="#b7b5ac",
    ),
)

# Picker order: GitHub first (the theme people ask for by name), then dark
# themes, then light ones. Code-AI's own "monokai" is declared in palette.py.
THEME_SPECS: dict[str, ThemeSpec] = {
    spec.name: spec
    for spec in (
        _GITHUB_DARK,
        _GITHUB_DARK_DIMMED,
        _GITHUB_LIGHT,
        _TEXTUAL_DARK,
        _TOKYO_NIGHT,
        _DRACULA,
        _NORD,
        _GRUVBOX,
        _CATPPUCCIN_MOCHA,
        _CATPPUCCIN_MACCHIATO,
        _CATPPUCCIN_FRAPPE,
        _SOLARIZED_DARK,
        _ATOM_ONE_DARK,
        _ROSE_PINE,
        _ROSE_PINE_MOON,
        _FLEXOKI,
        _TEXTUAL_LIGHT,
        _CATPPUCCIN_LATTE,
        _SOLARIZED_LIGHT,
        _ATOM_ONE_LIGHT,
        _ROSE_PINE_DAWN,
    )
}
