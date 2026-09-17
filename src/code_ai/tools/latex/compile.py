"""Compiling a .tex: engine runs, bibliography, reruns, and installing what the log lacks."""

from __future__ import annotations

import asyncio
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from code_ai.core.errors import ToolArgumentError, ToolExecutionError
from code_ai.tools.latex import install, logparse
from code_ai.tools.latex.engines import TexSetup

RUN_TIMEOUT_S = 240.0
_MAGIC = re.compile(r"^%\s*!TEX\s+(?:TS-)?program\s*=\s*(\w+)", re.IGNORECASE | re.MULTILINE)
_UNICODE_ENGINE_HINTS = re.compile(
    r"\\usepackage(\[[^\]]*\])?\{(fontspec|unicode-math|polyglossia)\}"
)
_BIB_WARNING = re.compile(r'Warning--I didn\'t find a database entry for "([^"]+)"')


@dataclass
class CompileResult:
    pdf: Path | None
    engine: str
    runs: int = 0
    bibliography: str | None = None
    report: logparse.LogReport = field(default_factory=logparse.LogReport)
    installed: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def choose_engine(source: str, requested: str | None, setup: TexSetup) -> str:
    if requested and requested not in {"auto", ""}:
        engine = requested.lower()
        if engine not in {"pdflatex", "xelatex", "lualatex", "tectonic"}:
            raise ToolArgumentError("engine is auto, pdflatex, xelatex, lualatex or tectonic.")
    else:
        magic = _MAGIC.search(source[:2000])
        engine = (
            magic.group(1).lower()
            if magic
            else ("xelatex" if _UNICODE_ENGINE_HINTS.search(source) else "pdflatex")
        )
    if engine == "tectonic":
        if not setup.tectonic:
            raise ToolExecutionError("tectonic is not installed.")
        return engine
    if engine not in setup.engines:
        if setup.engines:
            fallback = "pdflatex" if "pdflatex" in setup.engines else sorted(setup.engines)[0]
            return fallback
        if setup.tectonic:
            return "tectonic"
        raise ToolExecutionError(
            "No TeX engine is installed. Call latex_setup with action 'install' to install "
            "TeX Live for this user (works behind the company proxy), or install MiKTeX or "
            "TeX Live yourself."
        )
    return engine


async def compile_tex(
    tex: Path,
    setup: TexSetup,
    *,
    engine: str | None,
    output: Path | None,
    max_runs: int,
    install_missing: bool,
    verify_ssl: bool,
    cancel_event: asyncio.Event | None,
) -> CompileResult:
    source = tex.read_text(encoding="utf-8", errors="replace")
    chosen = choose_engine(source, engine, setup)
    build = tex.parent / "build"
    build.mkdir(exist_ok=True)
    result = CompileResult(pdf=None, engine=chosen)
    if chosen == "tectonic":
        return await _tectonic(tex, setup, build, output, result)

    env = _env(setup, tex.parent)
    # acmart alone pulls in a dozen packages, found one missing file at a time.
    installs_left = 15
    while True:
        _check_cancel(cancel_event)
        await _engine_run(setup.engines[chosen], tex, build, env)
        result.runs += 1
        report = _read_log(build / f"{tex.stem}.log", tex.parent)
        wanted = [name for name in report.missing_files if "." in name]
        if report.errors and wanted and install_missing and setup.managed and installs_left > 0:
            packages = await _packages_for(wanted, verify_ssl)
            fresh = [p for p in packages if p not in result.installed]
            if fresh:
                await install.install_packages(fresh, verify_ssl=verify_ssl)
                result.installed += fresh
                installs_left -= 1
                continue
        break
    result.report = report
    if report.errors:
        if report.missing_files and not setup.managed:
            result.notes.append(
                "Missing files come from packages this TeX installation lacks. Install them "
                "with its package manager (MiKTeX Console, tlmgr), or use latex_setup install "
                "for a TeX Live that Code-AI keeps complete automatically."
            )
        return result

    aux = build / f"{tex.stem}.aux"
    aux_text = aux.read_text(encoding="utf-8", errors="replace") if aux.exists() else ""
    if (build / f"{tex.stem}.bcf").exists():
        result.bibliography = await _biber(setup, tex, build, env, result)
    elif "\\bibdata" in aux_text:
        result.bibliography = await _bibtex(
            setup, tex, build, env, result, verify_ssl, install_missing
        )

    reruns = 2 if result.bibliography else 0
    # Runs spent discovering missing packages do not count against the rerun budget.
    passes = 1
    while passes < max_runs and (reruns > 0 or report.needs_rerun):
        passes += 1
        _check_cancel(cancel_event)
        await _engine_run(setup.engines[chosen], tex, build, env)
        result.runs += 1
        reruns -= 1
        report = _read_log(build / f"{tex.stem}.log", tex.parent)
        if report.errors:
            break
    result.report = report
    produced = build / f"{tex.stem}.pdf"
    if produced.exists() and not report.errors:
        target = output or tex.with_suffix(".pdf")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(produced, target)
        result.pdf = target
    return result


def _env(setup: TexSetup, root: Path) -> dict[str, str]:
    env = dict(os.environ)
    if setup.bin_dir is not None:
        env["PATH"] = str(setup.bin_dir) + os.pathsep + env.get("PATH", "")
    # Trailing separator keeps the default search path after the document's own directory.
    for name in ("TEXINPUTS", "BIBINPUTS", "BSTINPUTS"):
        env[name] = str(root) + os.pathsep + env.get(name, "")
    return env


async def _engine_run(executable: str, tex: Path, build: Path, env: dict[str, str]) -> None:
    command = [
        executable,
        "-interaction=nonstopmode",
        "-halt-on-error",
        "-file-line-error",
        "-synctex=1",
        f"-output-directory={build}",
        tex.name,
    ]
    # A failed run is reported through its log, not its exit code.
    await install.run(command, env=env, timeout_s=RUN_TIMEOUT_S, cwd=tex.parent, check=False)


def _read_log(log: Path, root: Path) -> logparse.LogReport:
    if not log.exists():
        report = logparse.LogReport()
        report.errors.append(
            {"message": "The engine produced no log: it did not start or crashed."}
        )
        return report
    return logparse.parse(log.read_text(encoding="utf-8", errors="replace"), source_root=root)


async def _packages_for(files: list[str], verify_ssl: bool) -> list[str]:
    packages = []
    for name in files[:6]:
        package = await install.package_for_file(
            Path(name).name if "." in Path(name).name else f"{name}.sty", verify_ssl=verify_ssl
        )
        if package and package not in packages:
            packages.append(package)
    return packages


async def _bibtex(setup, tex, build, env, result, verify_ssl, install_missing) -> str:
    executable = setup.bibtex or "bibtex"
    output = await install.run(
        [executable, tex.stem], env=env, timeout_s=RUN_TIMEOUT_S, cwd=build, check=False
    )
    blg = build / f"{tex.stem}.blg"
    text = blg.read_text(encoding="utf-8", errors="replace") if blg.exists() else output
    missing_style = re.search(r"I couldn't open style file (\S+)", text)
    if missing_style and install_missing and setup.managed:
        package = await install.package_for_file(missing_style.group(1), verify_ssl=verify_ssl)
        if package:
            await install.install_packages([package], verify_ssl=verify_ssl)
            result.installed.append(package)
            await install.run(
                [executable, tex.stem], env=env, timeout_s=RUN_TIMEOUT_S, cwd=build, check=False
            )
            text = blg.read_text(encoding="utf-8", errors="replace") if blg.exists() else ""
    unknown = sorted(set(_BIB_WARNING.findall(text)))
    if unknown:
        result.notes.append(f"BibTeX has no entries for: {', '.join(unknown[:20])}")
    errors = re.findall(r"^(.*---line \d+ of file .*)$", text, re.MULTILINE)
    if errors:
        result.notes.append("BibTeX errors: " + " | ".join(errors[:5]))
    return "bibtex"


async def _biber(setup, tex, build, env, result) -> str:
    if not setup.biber:
        result.notes.append(
            "The document uses biblatex with biber, which is not installed; "
            "references stay unresolved."
        )
        return "biber (missing)"
    output = await install.run(
        [setup.biber, "--input-directory", str(build), "--output-directory", str(build), tex.stem],
        env=env,
        timeout_s=RUN_TIMEOUT_S,
        cwd=tex.parent,
        check=False,
    )
    problems = [line for line in output.splitlines() if line.startswith(("ERROR", "WARN"))]
    if problems:
        result.notes.append("biber: " + " | ".join(problems[:5]))
    return "biber"


async def _tectonic(tex, setup, build, output, result) -> CompileResult:
    await install.run(
        [
            setup.tectonic,
            "-X",
            "compile",
            tex.name,
            "--outdir",
            str(build),
            "--keep-logs",
            "--keep-intermediates",
        ],
        timeout_s=RUN_TIMEOUT_S * 2,
        cwd=tex.parent,
        check=False,
    )
    result.runs = 1
    result.report = _read_log(build / f"{tex.stem}.log", tex.parent)
    produced = build / f"{tex.stem}.pdf"
    if produced.exists() and not result.report.errors:
        target = output or tex.with_suffix(".pdf")
        shutil.copyfile(produced, target)
        result.pdf = target
    return result


def _check_cancel(event: asyncio.Event | None) -> None:
    if event is not None and event.is_set():
        raise asyncio.CancelledError


def clean_build(tex: Path) -> int:
    build = tex.parent / "build"
    if not build.is_dir():
        return 0
    removed = 0
    for path in build.iterdir():
        if path.suffix != ".pdf":
            path.unlink(missing_ok=True)
            removed += 1
    return removed
