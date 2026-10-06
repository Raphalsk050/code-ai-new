"""LaTeX formulas as the nearest thing a terminal can draw: Unicode.

The extension typesets them; a terminal has one row of fixed cells and no way
to stack a fraction. What it can do is stop showing the markup: ``\\alpha``
becomes the letter, ``x^2`` a real superscript, ``\\frac{a}{b}`` reads ``a/b``.
"""

from __future__ import annotations

import re
import unicodedata

_GREEK = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε", "varepsilon": "ε",
    "zeta": "ζ", "eta": "η", "theta": "θ", "vartheta": "ϑ", "iota": "ι", "kappa": "κ",
    "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ", "pi": "π", "rho": "ρ", "sigma": "σ",
    "tau": "τ", "upsilon": "υ", "phi": "φ", "varphi": "φ", "chi": "χ", "psi": "ψ", "omega": "ω",
    "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ", "Xi": "Ξ", "Pi": "Π",
    "Sigma": "Σ", "Upsilon": "Υ", "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω",
}  # fmt: skip
_SYMBOLS = {
    **_GREEK,
    "times": "×", "cdot": "·", "div": "÷", "pm": "±", "mp": "∓", "ast": "∗", "star": "⋆",
    "leq": "≤", "le": "≤", "geq": "≥", "ge": "≥", "neq": "≠", "ne": "≠", "ll": "≪", "gg": "≫",
    "approx": "≈", "equiv": "≡", "sim": "∼", "simeq": "≃", "cong": "≅", "propto": "∝",
    "infty": "∞", "partial": "∂", "nabla": "∇", "sum": "∑", "prod": "∏",
    "int": "∫", "iint": "∬", "oint": "∮",
    "forall": "∀", "exists": "∃", "in": "∈", "notin": "∉", "ni": "∋",
    "subset": "⊂", "subseteq": "⊆", "supset": "⊃", "supseteq": "⊇",
    "cup": "∪", "cap": "∩", "setminus": "∖", "emptyset": "∅", "varnothing": "∅",
    "to": "→", "rightarrow": "→", "leftarrow": "←", "gets": "←", "leftrightarrow": "↔",
    "Rightarrow": "⇒", "implies": "⇒", "Leftarrow": "⇐", "Leftrightarrow": "⇔", "iff": "⇔",
    "mapsto": "↦", "uparrow": "↑", "downarrow": "↓",
    "cdots": "⋯", "ldots": "…", "dots": "…", "vdots": "⋮", "ddots": "⋱",
    "land": "∧", "wedge": "∧", "lor": "∨", "vee": "∨", "neg": "¬", "lnot": "¬",
    "oplus": "⊕", "otimes": "⊗", "circ": "∘", "bullet": "∙", "perp": "⊥", "parallel": "∥",
    "angle": "∠", "triangle": "△", "therefore": "∴", "because": "∵", "top": "⊤", "bot": "⊥",
    "langle": "⟨", "rangle": "⟩", "lfloor": "⌊", "rfloor": "⌋", "lceil": "⌈", "rceil": "⌉",
    "lbrace": "{", "rbrace": "}", "vert": "|", "mid": "|", "Vert": "‖", "|": "‖",
    "ell": "ℓ", "hbar": "ℏ", "Re": "ℜ", "Im": "ℑ", "aleph": "ℵ", "prime": "′", "degree": "°",
}  # fmt: skip
_BLACKBOARD = {"R": "ℝ", "N": "ℕ", "Z": "ℤ", "Q": "ℚ", "C": "ℂ", "P": "ℙ", "H": "ℍ"}
# Commands that only style their argument: what is left is the argument.
_WRAPPERS = frozenset(
    {
        "text", "textrm", "textbf", "textit", "texttt", "mathrm", "mathbf", "mathit", "mathsf",
        "mathtt", "mathcal", "boldsymbol", "bm", "operatorname", "mbox", "overline", "underline",
    }
)  # fmt: skip
# Sizing and layout hints with nothing to show for themselves.
_SILENT = frozenset(
    {
        "left", "right", "big", "Big", "bigg", "Bigg", "bigl", "bigr", "Bigl", "Bigr",
        "displaystyle", "textstyle", "limits", "nolimits", "!", "nonumber",
    }
)  # fmt: skip
_SPACES = frozenset({",", ";", ":", " ", "quad", "qquad", "enspace", "thinspace"})
_ACCENTS = {"hat": "̂", "bar": "̄", "vec": "⃗", "tilde": "̃", "dot": "̇"}

# Only what the fonts terminals ship with can be counted on to draw.
_SUPERSCRIPT = str.maketrans("0123456789+-=()ni", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿⁱ")
_SUBSCRIPT = str.maketrans("0123456789+-=()aeoxijkmnt", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑₒₓᵢⱼₖₘₙₜ")
_SUPERSCRIPTABLE = frozenset("0123456789+-=()ni")
_SUBSCRIPTABLE = frozenset("0123456789+-=()aeoxijkmnt")


def _command(tex: str, at: int) -> tuple[str, int]:
    """The command starting at the backslash at ``at``, and where it ends."""

    end = at + 1
    while end < len(tex) and tex[end].isalpha():
        end += 1
    if end == at + 1:
        # A single symbol (\, \{ \\), or a lone backslash at the very end.
        return tex[at + 1 : at + 2], min(at + 2, len(tex))
    return tex[at + 1 : end], end


def _argument(tex: str, at: int) -> tuple[str, int]:
    """One argument from ``at``: a braced group, a command or a character."""

    while at < len(tex) and tex[at].isspace():
        at += 1
    if at >= len(tex):
        return "", at
    if tex[at] == "{":
        depth = 0
        for end in range(at, len(tex)):
            if tex[end] == "{" and (end == 0 or tex[end - 1] != "\\"):
                depth += 1
            elif tex[end] == "}" and tex[end - 1] != "\\":
                depth -= 1
                if depth == 0:
                    return tex[at + 1 : end], end + 1
        return tex[at + 1 :], len(tex)
    if tex[at] == "\\":
        _name, end = _command(tex, at)
        return tex[at:end], end
    return tex[at], at + 1


def _atom(text: str) -> str:
    """``text`` as one operand: bracketed unless it already reads as one."""

    return text if re.fullmatch(r"[\w.]+|.", text) else f"({text})"


def _script(mark: str, text: str) -> str:
    table, known = (
        (_SUPERSCRIPT, _SUPERSCRIPTABLE) if mark == "^" else (_SUBSCRIPT, _SUBSCRIPTABLE)
    )
    if text and all(char in known for char in text):
        return text.translate(table)
    return mark + _atom(text) if text else mark


def _convert(tex: str) -> str:
    out: list[str] = []
    at = 0
    while at < len(tex):
        char = tex[at]
        if char == "\\":
            name, at = _command(tex, at)
            if name in _WRAPPERS:
                inner, at = _argument(tex, at)
                out.append(_convert(inner))
            elif name in {"frac", "dfrac", "tfrac"}:
                top, at = _argument(tex, at)
                bottom, at = _argument(tex, at)
                out.append(f"{_atom(_convert(top))}/{_atom(_convert(bottom))}")
            elif name == "binom":
                top, at = _argument(tex, at)
                bottom, at = _argument(tex, at)
                out.append(f"C({_convert(top)}, {_convert(bottom)})")
            elif name == "sqrt":
                degree = ""
                if tex[at : at + 1] == "[":
                    close = tex.find("]", at)
                    if close > 0:
                        degree, at = _script("^", _convert(tex[at + 1 : close])), close + 1
                inner, at = _argument(tex, at)
                out.append(f"{degree.removeprefix('^')}√{_atom(_convert(inner))}")
            elif name == "mathbb":
                inner, at = _argument(tex, at)
                out.append(_BLACKBOARD.get(inner.strip(), _convert(inner)))
            elif name in _ACCENTS:
                inner, at = _argument(tex, at)
                body = _convert(inner)
                out.append(body + _ACCENTS[name] if len(body) == 1 else f"{name}({body})")
            elif name in {"begin", "end"}:
                _environment, at = _argument(tex, at)
            elif name in _SYMBOLS:
                out.append(_SYMBOLS[name])
            elif name in _SPACES:
                out.append(" ")
            elif name == "\\":
                out.append("\n")
            elif name not in _SILENT:
                # An escaped character is itself; an unknown command, its name
                # (sin, log, lim): readable, and nothing is silently dropped.
                out.append(name)
        elif char in "^_":
            inner, at = _argument(tex, at + 1)
            out.append(_script(char, _convert(inner)))
        elif char == "{":
            inner, at = _argument(tex, at)
            out.append(_convert(inner))
        elif char in "}&~":
            out.append("" if char == "}" else " ")
            at += 1
        else:
            out.append(char)
            at += 1
    return "".join(out)


def latex_to_unicode(tex: str) -> str:
    """A LaTeX formula as plain text a terminal can show."""

    # Composed: one cell for an accented letter where the font has it.
    text = unicodedata.normalize("NFC", _convert(tex))
    lines = (re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n"))
    return "\n".join(line for line in lines if line)


# Left as it is: code is where a dollar sign is just a dollar sign.
_CODE = re.compile(r"(```[\s\S]*?(?:```|\Z)|~~~[\s\S]*?(?:~~~|\Z)|`[^`\n]*`)")
_TEX = r"((?:(?!\n[ \t]*\n)[\s\S])+?)"
_DISPLAY = re.compile(rf"\$\${_TEX}\$\$|\\\[{_TEX}\\\]")
# Pandoc's rule, the extension's too: "$5 and $10" stays money.
_INLINE = re.compile(rf"\\\({_TEX}\\\)|\$(?![\s$])((?:\\[\s\S]|[^\\$\n])+?)(?<![\s\\])\$(?!\d)")
_MARKDOWN_SPECIAL = re.compile(r"([\\`*_\[\]<>|~#])")


def _plain(tex: str) -> str:
    """Converted, and escaped so Markdown reads none of it as markup."""

    return _MARKDOWN_SPECIAL.sub(r"\\\1", latex_to_unicode(tex))


def _display(match: re.Match[str]) -> str:
    # Its own paragraph, one line per row of the formula.
    rows = _plain(match.group(1) or match.group(2)).split("\n")
    return "\n\n" + "  \n".join(rows) + "\n\n"


def render_math_in_markdown(body: str) -> str:
    """``body`` with every formula outside code turned into readable text."""

    if "$" not in body and "\\" not in body:
        return body
    parts = _CODE.split(body)
    for index in range(0, len(parts), 2):
        text = _DISPLAY.sub(_display, parts[index])
        parts[index] = _INLINE.sub(lambda m: _plain(m.group(1) or m.group(2)), text)
    return "".join(parts)
