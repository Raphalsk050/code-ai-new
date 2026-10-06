from __future__ import annotations

import pytest

from code_ai.prompts import MATH_RULE, build_system_prompt, build_toolless_system_prompt
from code_ai.ui.terminal.math_text import latex_to_unicode, render_math_in_markdown
from code_ai.ui.terminal.widgets import markdown_to_content


@pytest.mark.parametrize(
    ("tex", "shown"),
    [
        (r"E = mc^2", "E = mc²"),
        (r"x = \frac{-b \pm \sqrt{b^2 - 4ac}}{2a}", "x = (-b ± √(b² - 4ac))/2a"),
        (r"\sum_{i=1}^{n} i = \frac{n(n+1)}{2}", "∑ᵢ₌₁ⁿ i = (n(n+1))/2"),
        (r"\int_0^\infty e^{-x^2}\,dx", "∫₀^∞ e^(-x²) dx"),
        (r"\alpha \leq \beta \Rightarrow \forall x \in \mathbb{R}", "α ≤ β ⇒ ∀ x ∈ ℝ"),
        (r"\lim_{x \to 0} \frac{\sin x}{x} = 1", "lim_(x → 0) (sin x)/x = 1"),
        (r"\hat{y} = \vec{w} \cdot \vec{x} + b", "ŷ = w⃗ · x⃗ + b"),
        (r"\left( \frac{1}{2} \right)^{10}", "( 1/2 )¹⁰"),
        (r"\text{cost} = 50\%", "cost = 50%"),
        (r"\begin{aligned} a &= b + c \\ d &= e \end{aligned}", "a = b + c\nd = e"),
    ],
)
def test_a_formula_reads_as_one(tex: str, shown: str) -> None:
    assert latex_to_unicode(tex) == shown


@pytest.mark.parametrize("tex", ["x^{", "\\", r"\frac{a", r"\sqrt[3", "{{{", "}}}", "^", ""])
def test_half_a_formula_is_converted_without_failing(tex: str) -> None:
    assert isinstance(latex_to_unicode(tex), str)


def test_only_what_is_a_formula_is_touched() -> None:
    body = (
        "Custa $5 ou $10, e R$5 e R$10. Set $HOME and $PATH.\n\n"
        "Mas $a^2+b^2=c^2$ vale, e `echo $a$b` fica.\n\n"
        "```sh\necho $x$y \\(z\\)\n```\n"
    )
    out = render_math_in_markdown(body)
    assert "Custa $5 ou $10, e R$5 e R$10. Set $HOME and $PATH." in out
    assert "Mas a²+b²=c² vale" in out
    assert "`echo $a$b`" in out
    assert "echo $x$y \\(z\\)" in out


def test_a_display_formula_gets_its_own_paragraph() -> None:
    out = render_math_in_markdown("Logo:\n$$\nx = \\frac{a}{b}\n$$\ne segue.")
    assert out.split() == ["Logo:", "x", "=", "a/b", "e", "segue."]
    assert "\n\nx = a/b\n\n" in out
    assert "x = a/b" in render_math_in_markdown("\\[ x = \\frac{a}{b} \\]")


def test_what_a_formula_becomes_is_not_read_as_markdown() -> None:
    """a*b*c would lose its stars to emphasis, and y_1 ... z_2 its underscores."""

    shown = markdown_to_content("Produto \\(a*b*c\\) e $x_{y} + z_{w}$ aqui.", 80).plain
    assert "a*b*c" in shown
    assert "x_y + z_w" in shown


def test_the_answer_shows_the_formula_not_its_markup() -> None:
    shown = markdown_to_content("A energia é $E = mc^2$.\n\n$$\n\\alpha \\leq \\beta\n$$", 80).plain
    assert "E = mc²" in shown
    assert "α ≤ β" in shown
    assert "$" not in shown and "\\" not in shown


def test_the_model_is_told_how_to_write_a_formula(tmp_path) -> None:
    assert MATH_RULE in build_system_prompt(workspace=tmp_path, language="en")
    assert MATH_RULE in build_toolless_system_prompt(workspace=tmp_path, language="en")
