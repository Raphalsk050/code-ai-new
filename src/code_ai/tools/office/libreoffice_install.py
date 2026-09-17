"""Per-user LibreOffice, no admin rights and no package manager.

Windows: administrative install of the official MSI (``msiexec /a``), which
only unpacks. Linux: the official .deb tarball unpacked in Python, so no dpkg
or root.
"""

from __future__ import annotations

import asyncio
import io
import re
import shutil
import sys
import tarfile
import tempfile
from pathlib import Path

from code_ai.core.errors import ToolExecutionError
from code_ai.tools.office.common import managed_tools_dir
from code_ai.tools.office.converters import _run, find_libreoffice
from code_ai.tools.office.download import download, fetch_text

STABLE_INDEX = "https://download.documentfoundation.org/libreoffice/stable/"
INSTALL_TIMEOUT_S = 1800.0

# One install at a time per process: two tools asking at once would download twice.
_lock = asyncio.Lock()


async def install_libreoffice(*, verify_ssl: bool = False) -> Path:
    async with _lock:
        existing = find_libreoffice()
        if existing is not None:
            return existing
        if sys.platform == "win32":
            await _install_windows(verify_ssl)
        elif sys.platform.startswith("linux"):
            await _install_linux(verify_ssl)
        else:
            raise ToolExecutionError(
                "Installing LibreOffice automatically is supported on Windows and Linux. "
                "Install it from https://www.libreoffice.org/download/ and try again."
            )
        installed = find_libreoffice()
        if installed is None:
            raise ToolExecutionError(
                "LibreOffice was unpacked but soffice could not be found in "
                f"{managed_tools_dir() / 'libreoffice'}."
            )
        return installed


async def latest_version(verify_ssl: bool) -> str:
    listing = await fetch_text(STABLE_INDEX, verify_ssl=verify_ssl)
    versions = re.findall(r'href="(\d+\.\d+\.\d+)/"', listing)
    if not versions:
        raise ToolExecutionError(f"No LibreOffice version listed at {STABLE_INDEX}.")
    return max(versions, key=lambda v: tuple(int(part) for part in v.split(".")))


async def _install_windows(verify_ssl: bool) -> None:
    version = await latest_version(verify_ssl)
    name = f"LibreOffice_{version}_Win_x86-64.msi"
    url = f"{STABLE_INDEX}{version}/win/x86_64/{name}"
    target = managed_tools_dir() / "libreoffice"
    with tempfile.TemporaryDirectory(prefix="code-ai-lo-") as scratch:
        msi = await download(url, Path(scratch, name), verify_ssl=verify_ssl)
        log = Path(scratch, "msiexec.log")
        target.mkdir(parents=True, exist_ok=True)
        command = ["msiexec", "/a", str(msi), "/qn", f"TARGETDIR={target}", "/L*", str(log)]
        try:
            await _run(command, INSTALL_TIMEOUT_S, "msiexec")
        except ToolExecutionError as exc:
            tail = ""
            if log.exists():
                lines = log.read_text("utf-16", errors="replace").splitlines()
                tail = " ".join(line for line in lines[-6:] if line.strip())
            raise ToolExecutionError(f"{exc} {tail}".strip()) from exc


async def _install_linux(verify_ssl: bool) -> None:
    version = await latest_version(verify_ssl)
    name = f"LibreOffice_{version}_Linux_x86-64_deb.tar.gz"
    url = f"{STABLE_INDEX}{version}/deb/x86_64/{name}"
    target = managed_tools_dir() / "libreoffice"
    with tempfile.TemporaryDirectory(prefix="code-ai-lo-") as scratch:
        archive = await download(url, Path(scratch, name), verify_ssl=verify_ssl)
        await asyncio.to_thread(_unpack_deb_tarball, archive, target)


def _unpack_deb_tarball(archive: Path, target: Path) -> None:
    staging = target.with_name(target.name + ".partial")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    with tarfile.open(archive, "r:gz") as outer:
        for member in outer.getmembers():
            if not member.isfile() or not member.name.endswith(".deb"):
                continue
            # Desktop menu integration writes outside the program tree.
            if "debian-menus" in member.name:
                continue
            handle = outer.extractfile(member)
            if handle is not None:
                _unpack_deb(handle.read(), staging)
    shutil.rmtree(target, ignore_errors=True)
    staging.rename(target)


def _unpack_deb(data: bytes, destination: Path) -> None:
    """Extract a .deb's data member: an ar archive whose data.tar.* holds the files."""

    if not data.startswith(b"!<arch>\n"):
        raise ToolExecutionError("A LibreOffice package is not a valid .deb archive.")
    offset = 8
    while offset + 60 <= len(data):
        header = data[offset : offset + 60]
        name = header[:16].decode("ascii", "replace").strip().rstrip("/")
        size = int(header[48:58].decode("ascii").strip())
        body = data[offset + 60 : offset + 60 + size]
        offset += 60 + size + (size % 2)
        if not name.startswith("data.tar"):
            continue
        stream = io.BytesIO(body)
        if name.endswith(".zst"):
            try:
                from compression import zstd  # Python 3.14+
            except ImportError as exc:
                raise ToolExecutionError(
                    "This LibreOffice package is zstd-compressed, which this build cannot unpack."
                ) from exc
            stream = io.BytesIO(zstd.decompress(body))
            mode = "r:"
        else:
            mode = "r:*"
        with tarfile.open(fileobj=stream, mode=mode) as inner:
            inner.extractall(destination, filter="tar")
        return
