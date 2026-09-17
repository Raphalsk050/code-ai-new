"""A per-user TeX Live that installs through the company proxy.

TeX Live over Tectonic: Tectonic fetches its bundle through a TLS stack that
cannot be told to skip verification, while TeX Live's downloader is a plain
program we pick. Every package is checked against the repository's SHA-512
sums, so integrity does not rest on TLS.

TeX Live only adds --no-check-certificate when ``wget --version`` says "+ssl",
and its bundled Windows wget says "+https", so the downloader is always given
explicitly through TL_DOWNLOAD_PROGRAM / TL_DOWNLOAD_ARGS.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

from code_ai.core.errors import ToolExecutionError
from code_ai.tools.office.common import managed_tools_dir
from code_ai.tools.office.download import download

REPOSITORY = "https://mirror.ctan.org/systems/texlive/tlnet"
INSTALL_TIMEOUT_S = 3600.0
PACKAGE_TIMEOUT_S = 1200.0

# What the templates need on top of scheme-basic. Anything else a document
# asks for is installed on demand when latex_compile meets the missing file.
BASE_PACKAGES = (
    "latexmk",
    "xetex",
    "biblatex",
    "biber",
    "collection-fontsrecommended",
    "collection-latexrecommended",
    "babel-portuges",
    "babel-english",
    "hyphen-portuguese",
    "microtype",
    "booktabs",
    "caption",
    "float",
    "enumitem",
    "csquotes",
    "xcolor",
    "listings",
    "lastpage",
    "titlesec",
    "cleveref",
    "siunitx",
    "multirow",
    "doi",
    "url",
)
TEMPLATE_PACKAGES = {
    "ieee": ("ieeetran", "cite"),
    "acm": ("acmart",),
    "springer": ("llncs",),
    "elsevier": ("elsarticle", "lineno"),
    "abnt": ("abntex2", "memoir", "textcase", "lastpage", "microtype", "babel-portuges"),
}

_lock = asyncio.Lock()


def install_dir() -> Path:
    return managed_tools_dir() / "texlive"


def platform_dir() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "universal-darwin"
    machine = os.uname().machine.lower()
    return "aarch64-linux" if machine in {"aarch64", "arm64"} else "x86_64-linux"


def managed_bin() -> Path | None:
    candidate = install_dir() / "bin" / platform_dir()
    return candidate if candidate.is_dir() else None


def downloader_env(root: Path, *, verify_ssl: bool) -> dict[str, str]:
    """Environment that makes install-tl and tlmgr download through a program we chose."""

    env = dict(os.environ)
    if verify_ssl:
        return env
    wget_windows = root / "tlpkg" / "installer" / "wget" / "wget.exe"
    if sys.platform == "win32" and wget_windows.is_file():
        env["TL_DOWNLOAD_PROGRAM"] = str(wget_windows)
        env["TL_DOWNLOAD_ARGS"] = (
            "--no-check-certificate --user-agent=texlive/wget --tries=4 --timeout=90 -q -O"
        )
    elif shutil.which("curl"):
        env["TL_DOWNLOAD_PROGRAM"] = shutil.which("curl") or "curl"
        env["TL_DOWNLOAD_ARGS"] = (
            "--insecure --location --fail --silent --retry 4 --connect-timeout 90 --output"
        )
    elif shutil.which("wget"):
        env["TL_DOWNLOAD_PROGRAM"] = shutil.which("wget") or "wget"
        env["TL_DOWNLOAD_ARGS"] = "--no-check-certificate --tries=4 --timeout=90 -q -O"
    # LWP inside TeX Live's Perl reads this; harmless when LWP is not used.
    env["PERL_LWP_SSL_VERIFY_HOSTNAME"] = "0"
    env["TEXLIVE_INSTALL_NO_WELCOME"] = "1"
    return env


def profile_text(target: Path, scheme: str) -> str:
    texdir = target.as_posix()
    return "\n".join(
        [
            f"selected_scheme scheme-{scheme}",
            f"TEXDIR {texdir}",
            f"TEXMFLOCAL {texdir}/texmf-local",
            f"TEXMFSYSVAR {texdir}/texmf-var",
            f"TEXMFSYSCONFIG {texdir}/texmf-config",
            f"TEXMFVAR {texdir}/texmf-var",
            f"TEXMFCONFIG {texdir}/texmf-config",
            f"TEXMFHOME {texdir}/texmf-home",
            "instopt_adjustpath 0",
            "instopt_adjustrepo 1",
            "instopt_letter 0",
            "instopt_portable 1",
            "instopt_write18_restricted 1",
            "tlpdbopt_autobackup 0",
            "tlpdbopt_desktop_integration 0",
            "tlpdbopt_file_assocs 0",
            "tlpdbopt_install_docfiles 0",
            "tlpdbopt_install_srcfiles 0",
            "tlpdbopt_w32_multi_user 0",
            "",
        ]
    )


async def install_texlive(
    *,
    verify_ssl: bool = False,
    scheme: str = "basic",
    repository: str = REPOSITORY,
    extra_packages: tuple[str, ...] = BASE_PACKAGES,
) -> dict:
    """Install TeX Live into the managed directory; returns what happened."""

    async with _lock:
        target = install_dir()
        if managed_bin() is not None:
            return {"status": "already installed", "path": str(target)}
        if sys.platform != "win32" and shutil.which("perl") is None:
            raise ToolExecutionError(
                "Installing TeX Live on this system needs perl, which is not installed. "
                "Install perl (e.g. apt install perl) or a TeX distribution, then try again."
            )
        with tempfile.TemporaryDirectory(prefix="code-ai-texlive-") as scratch:
            scratch_path = Path(scratch)
            archive_name = "install-tl.zip" if sys.platform == "win32" else "install-tl-unx.tar.gz"
            archive = await download(
                f"{repository.rstrip('/')}/{archive_name}",
                scratch_path / archive_name,
                verify_ssl=verify_ssl,
            )
            await asyncio.to_thread(_extract, archive, scratch_path)
            installer_root = next(
                p for p in scratch_path.iterdir() if p.is_dir() and p.name.startswith("install-tl")
            )
            profile = scratch_path / "code-ai.profile"
            profile.write_text(profile_text(target, scheme), encoding="utf-8")
            target.parent.mkdir(parents=True, exist_ok=True)
            if sys.platform == "win32":
                command = [
                    "cmd",
                    "/c",
                    str(installer_root / "install-tl-windows.bat"),
                    "-no-gui",
                    "-profile",
                    str(profile),
                    "-repository",
                    repository,
                ]
            else:
                command = [
                    "perl",
                    str(installer_root / "install-tl"),
                    "-profile",
                    str(profile),
                    "-repository",
                    repository,
                ]
            env = downloader_env(installer_root, verify_ssl=verify_ssl)
            output = await run(command, env=env, timeout_s=INSTALL_TIMEOUT_S, cwd=installer_root)
            if managed_bin() is None:
                raise ToolExecutionError(
                    "TeX Live's installer finished without a bin directory:\n" + tail(output)
                )
        installed = await install_packages(
            list(extra_packages), verify_ssl=verify_ssl, repository=repository
        )
        return {"status": "installed", "path": str(target), "packages": installed}


def _extract(archive: Path, destination: Path) -> None:
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(destination)
    else:
        with tarfile.open(archive) as bundle:
            bundle.extractall(destination, filter="tar")


def tlmgr_command() -> list[str] | None:
    bin_dir = managed_bin()
    if bin_dir is None:
        return None
    if sys.platform == "win32":
        return ["cmd", "/c", str(bin_dir / "tlmgr.bat")]
    return [str(bin_dir / "tlmgr")]


async def install_packages(
    packages: list[str], *, verify_ssl: bool = False, repository: str | None = None
) -> dict:
    command = tlmgr_command()
    if command is None:
        raise ToolExecutionError(
            "No Code-AI managed TeX Live: run latex_setup with action install first."
        )
    if not packages:
        return {"installed": []}
    env = downloader_env(install_dir(), verify_ssl=verify_ssl)
    args = [*command]
    if repository:
        args += ["--repository", repository]
    output = await run(
        [*args, "install", *packages], env=env, timeout_s=PACKAGE_TIMEOUT_S, check=False
    )
    # tlmgr refuses the whole list over one unknown name; drop those and install the rest.
    unknown = sorted(set(re.findall(r"package (\S+) not present in repository", output)))
    if unknown:
        rest = [name for name in packages if name not in unknown]
        if rest:
            output = await run(
                [*args, "install", *rest], env=env, timeout_s=PACKAGE_TIMEOUT_S, check=False
            )
    failed = [
        line for line in output.splitlines() if "error" in line.lower() and "tlmgr" in line.lower()
    ]
    return {
        "requested": packages,
        "unknown_packages": unknown,
        "problems": failed[:10],
        "log_tail": tail(output, 6),
    }


async def package_for_file(filename: str, *, verify_ssl: bool = False) -> str | None:
    """The TeX Live package that ships ``filename`` (e.g. 'enumitem.sty'), via tlmgr search."""

    command = tlmgr_command()
    if command is None:
        return None
    env = downloader_env(install_dir(), verify_ssl=verify_ssl)
    output = await run(
        [*command, "search", "--global", "--file", f"/{filename}"],
        env=env,
        timeout_s=300,
        check=False,
    )
    for line in output.splitlines():
        # Package lines end with a colon; file lines below them are indented.
        if line and not line.startswith((" ", "\t")) and line.rstrip().endswith(":"):
            name = line.rstrip().rstrip(":").strip()
            if name and " " not in name and not name.startswith("tlmgr"):
                return name
    return None


async def run(
    command: list[str],
    *,
    env: dict[str, str] | None = None,
    timeout_s: float,
    cwd: Path | None = None,
    check: bool = True,
) -> str:
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            env=env,
            cwd=str(cwd) if cwd else None,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except OSError as exc:
        raise ToolExecutionError(f"Could not start {command[0]}: {exc}") from exc
    try:
        raw, _ = await asyncio.wait_for(process.communicate(), timeout_s)
    except TimeoutError:
        await _kill(process)
        raise ToolExecutionError(
            f"{Path(command[0]).name} did not finish within {timeout_s:g}s."
        ) from None
    except BaseException:
        await _kill(process)
        raise
    text = raw.decode("utf-8", "replace")
    if check and process.returncode != 0:
        raise ToolExecutionError(
            f"{Path(command[-1]).name} failed (exit {process.returncode}):\n{tail(text)}"
        )
    return text


async def _kill(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        process.kill()
    except ProcessLookupError:
        return
    await process.wait()


def tail(text: str, lines: int = 15) -> str:
    kept = [line for line in text.strip().splitlines() if line.strip()]
    return "\n".join(kept[-lines:])
