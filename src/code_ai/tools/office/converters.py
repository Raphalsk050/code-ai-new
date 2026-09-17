"""Office format conversion: LibreOffice first, then Word or PowerPoint over COM.

python-docx and python-pptx cannot lay pages out, so PDFs and previews need a
real office suite. COM goes through PowerShell so no pywin32 has to ship.
"""

from __future__ import annotations

import asyncio
import glob
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from code_ai.core.errors import ToolExecutionError
from code_ai.tools.office.common import managed_tools_dir

CONVERT_TIMEOUT_S = 240.0

_WORD_SUFFIXES = {".doc", ".docx", ".odt", ".rtf", ".txt", ".html", ".htm"}
_POWERPOINT_SUFFIXES = {".ppt", ".pptx", ".odp"}

# Word's WdSaveFormat and PowerPoint's PpSaveAsFileType values.
_WORD_FORMATS = {"pdf": 17, "docx": 16, "doc": 0, "odt": 23, "rtf": 6, "txt": 7, "html": 10}
_POWERPOINT_FORMATS = {"pdf": 32, "pptx": 24, "ppt": 1, "odp": 35}


@dataclass(frozen=True, slots=True)
class Conversion:
    path: Path
    backend: str


def find_libreoffice() -> Path | None:
    """soffice from PATH, the usual install locations, or Code-AI's own install."""

    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return Path(found)
    candidates: list[str] = []
    managed = managed_tools_dir() / "libreoffice"
    if sys.platform == "win32":
        for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
            if base:
                candidates.append(os.path.join(base, "LibreOffice", "program", "soffice.exe"))
        candidates += glob.glob(str(managed / "**" / "program" / "soffice.exe"), recursive=True)
    elif sys.platform == "darwin":
        candidates.append("/Applications/LibreOffice.app/Contents/MacOS/soffice")
    else:
        candidates += ["/usr/bin/soffice", "/usr/lib/libreoffice/program/soffice"]
        candidates += sorted(glob.glob("/opt/libreoffice*/program/soffice"), reverse=True)
        managed_soffice = managed / "opt" / "libreoffice*" / "program" / "soffice"
        candidates += sorted(glob.glob(str(managed_soffice)))
        candidates.append("/snap/bin/libreoffice")
    for candidate in candidates:
        if os.path.isfile(candidate):
            return Path(candidate)
    return None


def ms_office_available(application: str) -> bool:
    """Whether ``Word.Application`` or ``PowerPoint.Application`` is registered."""

    if sys.platform != "win32":
        return False
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, f"{application}\\CLSID"):
            return True
    except OSError:
        return False


def backends() -> dict[str, str | bool]:
    """What this machine can convert with, for tools that report their environment."""

    office = find_libreoffice()
    return {
        "libreoffice": str(office) if office else False,
        "word": ms_office_available("Word.Application"),
        "powerpoint": ms_office_available("PowerPoint.Application"),
    }


async def convert(
    source: Path,
    target_format: str,
    output: Path,
    *,
    verify_ssl: bool = False,
    install: bool = False,
    timeout_s: float = CONVERT_TIMEOUT_S,
) -> Conversion:
    """Convert ``source`` to ``target_format`` (an extension, no dot) at ``output``."""

    target_format = target_format.lower().lstrip(".")
    suffix = source.suffix.lower()
    failures: list[str] = []

    office = find_libreoffice()
    if office is not None:
        try:
            await _libreoffice(office, source, target_format, output, timeout_s)
            return Conversion(output, "libreoffice")
        except ToolExecutionError as exc:
            failures.append(str(exc))

    com = None
    if suffix in _WORD_SUFFIXES and target_format in _WORD_FORMATS:
        com = ("Word.Application", _WORD_FORMATS[target_format])
    elif suffix in _POWERPOINT_SUFFIXES and target_format in _POWERPOINT_FORMATS:
        com = ("PowerPoint.Application", _POWERPOINT_FORMATS[target_format])
    if com is not None and ms_office_available(com[0]):
        try:
            await _ms_office(com[0], com[1], source, output, timeout_s)
            return Conversion(output, com[0].split(".")[0].lower())
        except ToolExecutionError as exc:
            failures.append(str(exc))

    if office is None and install:
        from code_ai.tools.office.libreoffice_install import install_libreoffice

        office = await install_libreoffice(verify_ssl=verify_ssl)
        await _libreoffice(office, source, target_format, output, timeout_s)
        return Conversion(output, "libreoffice")

    if failures:
        raise ToolExecutionError("Conversion failed: " + " | ".join(failures))
    raise ToolExecutionError(
        f"Converting {source.name} to {target_format} needs LibreOffice"
        + (" or Microsoft Office" if sys.platform == "win32" else "")
        + ", and neither is installed. Call again with install_converter true to install "
        "LibreOffice for this user (a download of about 350 MB, no admin rights needed)."
    )


async def _libreoffice(
    soffice: Path, source: Path, target_format: str, output: Path, timeout_s: float
) -> None:
    # A private profile per run: two conversions sharing the default one make
    # the second exit at once, having handed its work to the first.
    with tempfile.TemporaryDirectory(prefix="code-ai-soffice-") as scratch:
        profile = Path(scratch, "profile").as_uri()
        out_dir = Path(scratch, "out")
        out_dir.mkdir()
        command = [
            str(soffice),
            f"-env:UserInstallation={profile}",
            "--headless",
            "--invisible",
            "--norestore",
            "--nolockcheck",
            "--nodefault",
            "--nofirststartwizard",
            "--convert-to",
            target_format,
            "--outdir",
            str(out_dir),
            str(source),
        ]
        output_text = await _run(command, timeout_s, "LibreOffice")
        produced = sorted(out_dir.glob(f"{source.stem}.*")) or sorted(out_dir.iterdir())
        if not produced:
            detail = output_text.strip().splitlines()[-3:] if output_text.strip() else []
            raise ToolExecutionError(
                "LibreOffice produced no output" + (f": {' '.join(detail)}" if detail else ".")
            )
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(produced[0]), output)


_COM_SCRIPTS = {
    "Word.Application": r"""
$ErrorActionPreference = 'Stop'
$app = New-Object -ComObject Word.Application
$app.Visible = $false
$app.DisplayAlerts = 0
try {
  $doc = $app.Documents.Open($env:CODE_AI_SRC, $false, $true)
  try { $doc.SaveAs2($env:CODE_AI_OUT, [int]$env:CODE_AI_FMT) } finally { $doc.Close(0) }
} finally { $app.Quit() }
""",
    "PowerPoint.Application": r"""
$ErrorActionPreference = 'Stop'
$app = New-Object -ComObject PowerPoint.Application
try {
  $pres = $app.Presentations.Open($env:CODE_AI_SRC, -1, 0, 0)
  try { $pres.SaveAs($env:CODE_AI_OUT, [int]$env:CODE_AI_FMT) } finally { $pres.Close() }
} finally { $app.Quit() }
""",
}


async def _ms_office(
    application: str, file_format: int, source: Path, output: Path, timeout_s: float
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    # Paths travel in the environment: quoting them into a script is where
    # an apostrophe in a folder name breaks the conversion.
    env = {
        **os.environ,
        "CODE_AI_SRC": str(source.resolve()),
        "CODE_AI_OUT": str(output.resolve()),
        "CODE_AI_FMT": str(file_format),
    }
    shell = shutil.which("pwsh") or shutil.which("powershell") or "powershell"
    command = [shell, "-NoProfile", "-NonInteractive", "-Command", _COM_SCRIPTS[application]]
    name = application.split(".")[0]
    await _run(command, timeout_s, name, env=env)
    if not output.exists():
        raise ToolExecutionError(f"{name} finished without writing {output.name}.")


async def _run(
    command: list[str], timeout_s: float, name: str, *, env: dict[str, str] | None = None
) -> str:
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            env=env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except OSError as exc:
        raise ToolExecutionError(f"Could not start {name}: {exc}") from exc
    try:
        raw, _ = await asyncio.wait_for(process.communicate(), timeout_s)
    except TimeoutError:
        await _kill(process)
        raise ToolExecutionError(f"{name} did not finish within {timeout_s:g}s.") from None
    except BaseException:
        # Cancelled with the turn: nobody is left to wait for the conversion.
        await _kill(process)
        raise
    text = raw.decode("utf-8", "replace")
    if process.returncode != 0:
        tail = " ".join(text.strip().splitlines()[-4:])
        raise ToolExecutionError(f"{name} failed (exit {process.returncode}): {tail}")
    return text


async def _kill(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        process.kill()
    except ProcessLookupError:
        return
    await process.wait()
