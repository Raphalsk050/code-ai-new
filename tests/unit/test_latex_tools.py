from __future__ import annotations

import asyncio
import json
import sys

import pytest
from PIL import Image

from code_ai.config.models import AppConfig
from code_ai.core.errors import ToolArgumentError, ToolExecutionError
from code_ai.events.bus import AsyncEventBus
from code_ai.tools.base import TOOL_IMAGES_KEY, ToolCapability, ToolContext
from code_ai.tools.latex import (
    LatexArticleTool,
    LatexCompileTool,
    LatexSetupTool,
    engines,
    install,
    logparse,
    markup,
    templates,
)
from code_ai.util.paths import WorkspacePolicy


def make_context(tmp_path) -> ToolContext:
    config = AppConfig.from_mapping({"api_mode": "ollama", "workspace": str(tmp_path)})
    return ToolContext(
        config=config,
        workspace=WorkspacePolicy.from_path(tmp_path),
        event_bus=AsyncEventBus(session_id="session"),
        cancel_event=asyncio.Event(),
    )


def convert(text: str, **kwargs) -> tuple[str, markup.Converter]:
    converter = markup.Converter(copy_image=lambda src: f"figures/{src}", **kwargs)
    return converter.convert(text), converter


def test_capabilities() -> None:
    assert ToolCapability.WEB in LatexSetupTool.capabilities
    assert ToolCapability.LOCAL_WRITE in LatexArticleTool.capabilities


def test_escape_covers_every_special() -> None:
    assert markup.escape("50% & #1 $x_y ^ ~ {}") == (
        r"50\% \& \#1 \$x\_y \textasciicircum{} \textasciitilde{} \{\}"
    )


def test_math_and_raw_latex_pass_through() -> None:
    source = (
        "Energy $E = mc^2$ and $a_1*b_2$ with 10% off.\n\n"
        "$$\n\\int_0^1 f(x)\\,dx\n$$\n\n"
        "\\begin{equation}\\label{eq:m}\na_b = c\n\\end{equation}\n\n"
        "See \\cite{extra} and **bold** `x_y`."
    )
    out, _ = convert(source)
    assert "$E = mc^2$" in out and "$a_1*b_2$" in out and r"10\%" in out
    assert "\\int_0^1 f(x)\\,dx" in out
    assert "\\begin{equation}\\label{eq:m}\na_b = c\n\\end{equation}" in out
    assert "\\cite{extra}" in out and "\\textbf{bold}" in out and r"\texttt{x\_y}" in out


def test_citations_and_cross_references() -> None:
    out, converter = convert("As shown [@knuth84; @lamport94], see @fig:arch and @eq:main.")
    assert "\\cite{knuth84,lamport94}" in out
    assert "Figure~\\ref{fig:arch}" in out and "Equation~\\eqref{eq:main}" in out
    assert converter.citations == {"knuth84", "lamport94"}
    portuguese, _ = convert("Veja @tab:res.", language="brazilian")
    assert "Tabela~\\ref{tab:res}" in portuguese


def test_figures_tables_lists_and_code() -> None:
    source = (
        "![Pipeline {#fig:pipe}](pipe.png){width=60%}\n\n"
        "Table: Results {#tab:res}\n\n| A | B |\n|:--|--:|\n| a_1 | 2 |\n\n"
        "- one\n  - nested\n1. first\n\n"
        "```python\nx_y = {'a': 1}  # $not math$\n```\n"
    )
    out, _ = convert(source)
    assert "\\includegraphics[width=0.6\\linewidth]{figures/pipe.png}" in out
    assert "\\caption{Pipeline}" in out and "\\label{fig:pipe}" in out
    assert "\\begin{tabular}{lr}" in out and "\\label{tab:res}" in out and r"a\_1 & 2 \\" in out
    assert out.count("\\begin{itemize}") == 2 and "\\begin{enumerate}" in out
    assert "\\begin{verbatim}\nx_y = {'a': 1}  # $not math$\n\\end{verbatim}" in out


def test_headings_follow_the_template() -> None:
    out, _ = convert(
        "# Intro {#sec:intro}\n## Detail", heading_commands=templates.heading_commands("abnt")
    )
    assert "\\chapter{Intro}\\label{sec:intro}" in out and "\\section{Detail}" in out


def _spec(template: str) -> templates.ArticleSpec:
    return templates.ArticleSpec(
        template=template,
        title="A & B",
        authors=[templates.Author("Ana", "UFAM", "ana@ufam.br"), templates.Author("Bruno", "USP")],
        abstract="Short.",
        keywords=["x_y", "z"],
        has_bibliography=True,
        abnt={"institution": "UFAM", "city": "Manaus", "year": "2026"},
    )


@pytest.mark.parametrize(
    ("template", "marker"),
    [
        ("article", "{article}"),
        ("report", "{report}"),
        ("ieee", "{IEEEtran}"),
        ("acm", "{acmart}"),
        ("springer", "{llncs}"),
        ("elsevier", "{elsarticle}"),
        ("abnt", "{abntex2}"),
    ],
)
def test_every_template_renders(template, marker) -> None:
    tex = templates.render(_spec(template), "Body.\n", "", "references")
    assert marker in tex and "\\begin{document}" in tex and tex.rstrip().endswith("\\end{document}")
    assert r"A \& B" in tex
    assert "\\bibliography{references}" in tex


def test_abnt_workarounds() -> None:
    tex = templates.render(_spec("abnt"), "Body.\n", "", "references")
    assert "\\autor{Ana \\\\ Bruno}" in tex  # no \and outside \maketitle
    assert "\\imprimirfolhaderosto*\n\n" in tex  # the blank line is the argument abnTeX2 eats
    springer = templates.render(_spec("springer"), "", "", "references")
    assert r"\keywords{x\_y \and z}" in springer


def _wrapped(line: str) -> str:
    """Wrap like TeX does: 79 characters per physical line."""

    return "\n".join(line[i : i + 79] for i in range(0, len(line), 79))


WRAPPED_LOG = "\n".join(
    [
        "(./main.tex",
        "LaTeX Warning: Citation `knuth84' on page 1 undefined on input line 12.",
        "LaTeX Warning: Reference `fig:arch' on page 1 undefined on input line 14.",
        "Overfull \\hbox (12.5pt too wide) in paragraph at lines 20--22",
        "! LaTeX Error: File `fancything.sty' not found.",
        "",
        _wrapped(
            "c:/Users/someone/AppData/Local/Temp/pytest-of-someone/pytest-1/"
            "test_with_a_long_name0/main.tex:36: Argument of \\begin has an extra }."
        ),
        "l.36 \\begin",
        "LaTeX Warning: There were undefined references.",
    ]
)


def test_log_parser_reads_errors_warnings_and_missing_files(tmp_path) -> None:
    (tmp_path / "main.tex").write_text(
        "\n".join(f"line {n}" for n in range(1, 50)), encoding="utf-8"
    )
    report = logparse.parse(WRAPPED_LOG, source_root=tmp_path)
    messages = [error["message"] for error in report.errors]
    assert "Argument of \\begin has an extra }." in messages
    wrapped = next(e for e in report.errors if e.get("line") == 36)
    assert wrapped["near"] == "\\begin"
    assert wrapped["file"].endswith("test_with_a_long_name0/main.tex")
    assert report.missing_files == ["fancything.sty"]
    assert report.undefined_citations == ["knuth84"] and report.undefined_references == ["fig:arch"]
    assert report.overfull == 1 and report.needs_rerun


def test_log_parser_adds_source_excerpt(tmp_path) -> None:
    (tmp_path / "main.tex").write_text("a\nb\n\\badcommand\nd\n", encoding="utf-8")
    log = "./main.tex:3: Undefined control sequence.\nl.3 \\badcommand\n"
    error = logparse.parse(log, source_root=tmp_path).errors[0]
    assert error["line"] == 3 and "> 3: \\badcommand" in error["source"]
    assert "misspelled" in error["hint"]


def test_installer_profile_and_insecure_downloader(tmp_path, monkeypatch) -> None:
    profile = install.profile_text(tmp_path / "texlive", "basic")
    assert "selected_scheme scheme-basic" in profile and "tlpdbopt_install_docfiles 0" in profile
    assert "instopt_adjustpath 0" in profile  # never touches the user's PATH
    monkeypatch.setattr(
        install.shutil, "which", lambda name: "/usr/bin/curl" if name == "curl" else None
    )
    if sys.platform == "win32":
        wget = tmp_path / "tlpkg" / "installer" / "wget"
        wget.mkdir(parents=True)
        (wget / "wget.exe").write_bytes(b"")
        env = install.downloader_env(tmp_path, verify_ssl=False)
        assert "--no-check-certificate" in env["TL_DOWNLOAD_ARGS"]
    else:
        env = install.downloader_env(tmp_path, verify_ssl=False)
        assert "--insecure" in env["TL_DOWNLOAD_ARGS"]
    assert "TL_DOWNLOAD_PROGRAM" not in install.downloader_env(tmp_path, verify_ssl=True)


async def test_unknown_packages_do_not_block_the_rest(monkeypatch) -> None:
    calls: list[list[str]] = []

    async def fake_run(command, **kwargs):
        calls.append(command)
        if "nope" in command:
            return "tlmgr install: package nope not present in repository.\n"
        return "done\n"

    monkeypatch.setattr(install, "tlmgr_command", lambda: ["tlmgr"])
    monkeypatch.setattr(install, "run", fake_run)
    result = await install.install_packages(["siunitx", "nope"])
    assert result["unknown_packages"] == ["nope"]
    assert calls[-1] == ["tlmgr", "install", "siunitx"]


def test_engine_discovery_prefers_managed_then_path(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(engines, "managed_bin", lambda: None)
    fake = tmp_path / ("pdflatex.exe" if sys.platform == "win32" else "pdflatex")
    fake.write_bytes(b"")
    monkeypatch.setattr(
        engines.shutil, "which", lambda name: str(fake) if name == "pdflatex" else None
    )
    setup = engines.discover()
    assert setup.source == "PATH" and set(setup.engines) == {"pdflatex"} and not setup.managed
    monkeypatch.setattr(engines, "managed_bin", lambda: tmp_path)
    managed = engines.discover()
    assert managed.managed and "pdflatex" in managed.engines


async def test_article_project_files(tmp_path) -> None:
    Image.new("RGB", (400, 200), "navy").save(tmp_path / "arch.png")
    context = make_context(tmp_path)
    references = json.dumps(
        [
            {
                "key": "knuth84",
                "type": "book",
                "author": ["Donald Knuth"],
                "title": "The TeXbook",
                "year": 1984,
            }
        ]
    )
    result = await LatexArticleTool().execute(
        {
            "output_dir": "paper",
            "title": "Study",
            "authors": json.dumps([{"name": "Ana", "affiliation": "UFAM"}]),
            "content": "# Intro\n\nAs [@knuth84; @missing].\n\n![Arch {#fig:arch}](../arch.png)",
            "references": references,
            "template": "ieee",
        },
        context,
    )
    project = tmp_path / "paper"
    assert (project / "main.tex").exists() and (project / "figures" / "arch.png").exists()
    assert "@book{knuth84," in (project / "references.bib").read_text(encoding="utf-8")
    assert "author = {Donald Knuth}" in (project / "references.bib").read_text(encoding="utf-8")
    assert result["citations_without_bib_entry"] == ["missing"]
    with pytest.raises(ToolExecutionError, match="overwrite"):
        await LatexArticleTool().execute({"output_dir": "paper", "title": "Again"}, context)
    with pytest.raises(ToolArgumentError, match="template"):
        await LatexArticleTool().execute(
            {"output_dir": "other", "title": "X", "template": "nature"}, context
        )


tex_available = engines.discover().usable
needs_tex = pytest.mark.skipif(not tex_available, reason="no TeX distribution installed")


@needs_tex
async def test_compile_builds_a_pdf_with_bibliography(tmp_path) -> None:
    context = make_context(tmp_path)
    result = await LatexArticleTool().execute(
        {
            "output_dir": "paper",
            "title": "Compiled",
            "content": "# Intro {#sec:intro}\n\nCited [@knuth84]. See @sec:intro.",
            "bibtex": (
                "@book{knuth84, author={Donald Knuth}, title={The TeXbook}, "
                "year={1984}, publisher={AW}}"
            ),
            "compile": True,
            "render_pages": "1",
        },
        context,
    )
    compiled = result["compile"]
    assert compiled["success"], compiled
    assert compiled["bibliography"] == "bibtex"
    assert "undefined_citations" not in compiled and "undefined_references" not in compiled
    assert (tmp_path / "paper" / "main.pdf").exists()
    assert len(result[TOOL_IMAGES_KEY]) == 1


@needs_tex
async def test_compile_reports_errors_with_source(tmp_path) -> None:
    (tmp_path / "bad.tex").write_text(
        "\\documentclass{article}\n\\begin{document}\nHello\n\\undefinedmacro\n\\end{document}\n",
        encoding="utf-8",
    )
    result = await LatexCompileTool().execute(
        {"path": "bad.tex", "install_missing": False}, make_context(tmp_path)
    )
    assert not result["success"]
    error = result["errors"][0]
    assert error["line"] == 4 and "Undefined control sequence" in error["message"]
    assert "> 4: \\undefinedmacro" in error["source"]


async def test_setup_status_reports_the_environment(tmp_path) -> None:
    status = await LatexSetupTool().execute({"action": "status"}, make_context(tmp_path))
    assert "engines" in status and "source" in status
    with pytest.raises(ToolArgumentError):
        await LatexSetupTool().execute({"action": "explode"}, make_context(tmp_path))
