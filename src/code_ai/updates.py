"""Updates from GitHub releases: is there a newer build, and fetch it.

Every CI build publishes a release tagged ``vX.Y.Z`` with the binaries and the
VS Code packages named after the version (see .github/workflows and
scripts/release.py). This module finds the newest one and downloads the asset
that fits the machine; the front ends decide what installing it means.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from code_ai.version import read_version

# --- Where Code-AI looks for updates -----------------------------------------
# The GitHub API to ask. For GitHub Enterprise it is "https://<host>/api/v3".
RELEASES_API = "https://api.github.com"
# The "owner/repository" that publishes the releases.
RELEASES_REPOSITORY = "Raphalsk050/code-ai-new"
# Whether builds from other branches (published as pre-releases) count as
# updates, or only releases from the default branch and tags.
INCLUDE_PRERELEASES = True
# An environment variable holding a token, for a private repository. Unset
# means anonymous, which is all a public repository needs.
RELEASES_TOKEN_ENV = "CODE_AI_UPDATE_TOKEN"
# -----------------------------------------------------------------------------

_TAG = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
_TIMEOUT_S = 30.0


@dataclass(frozen=True, slots=True)
class Asset:
    name: str
    download_url: str
    api_url: str
    size: int


@dataclass(frozen=True, slots=True)
class Release:
    version: str
    tag: str
    name: str
    notes: str
    url: str
    prerelease: bool
    assets: tuple[Asset, ...]

    def describe(self, current: str) -> dict[str, Any]:
        """The release as the front ends show it."""
        return {
            "version": self.version,
            "current": current,
            "name": self.name,
            "notes": self.notes,
            "url": self.url,
            "prerelease": self.prerelease,
        }


def parse_version(tag: str) -> tuple[int, int, int] | None:
    match = _TAG.match(tag.strip())
    return (int(match[1]), int(match[2]), int(match[3])) if match else None


def current_version() -> str:
    return read_version().release


def platform_key(platform: str = sys.platform) -> str:
    """``win`` or ``linux``: the platforms CI builds for; "" for the rest."""
    if platform.startswith("win"):
        return "win"
    if platform.startswith("linux"):
        return "linux"
    return ""


def asset_name(kind: str, version: str, target: str = "") -> str:
    """The file name CI gives an asset (see .github/workflows/build-binaries.yml).

    ``kind`` is "binary" (``target`` is "win" or "linux") or "vsix"
    (``target`` is VS Code's platform, e.g. "win32-x64").
    """
    if kind == "vsix":
        return f"code-ai-vscode-v{version}-{target}.vsix"
    if target == "win":
        return f"code-ai-win-v{version}.exe"
    return f"code-ai-{target}-v{version}"


def asset_for(release: Release, kind: str, target: str) -> Asset | None:
    wanted = asset_name(kind, release.version, target)
    return next((asset for asset in release.assets if asset.name == wanted), None)


def pick_newest(releases: list[dict[str, Any]], *, include_prereleases: bool) -> Release | None:
    """The highest-versioned published release in a GitHub releases listing."""
    best: tuple[tuple[int, int, int], dict[str, Any]] | None = None
    for item in releases:
        if item.get("draft") or (item.get("prerelease") and not include_prereleases):
            continue
        version = parse_version(str(item.get("tag_name") or ""))
        if version and (best is None or version > best[0]):
            best = (version, item)
    if best is None:
        return None
    item = best[1]
    return Release(
        version=".".join(map(str, best[0])),
        tag=str(item.get("tag_name") or ""),
        name=str(item.get("name") or ""),
        notes=str(item.get("body") or ""),
        url=str(item.get("html_url") or ""),
        prerelease=bool(item.get("prerelease")),
        assets=tuple(
            Asset(
                name=str(asset.get("name") or ""),
                download_url=str(asset.get("browser_download_url") or ""),
                api_url=str(asset.get("url") or ""),
                size=int(asset.get("size") or 0),
            )
            for asset in item.get("assets") or []
        ),
    )


def is_newer(release: Release | None, current: str) -> bool:
    if release is None:
        return False
    ours = parse_version(current)
    theirs = parse_version(release.version)
    return bool(ours and theirs and theirs > ours)


def _token() -> str:
    return os.environ.get(RELEASES_TOKEN_ENV, "").strip()


def _client(verify_ssl: bool):
    import httpx

    headers = {"User-Agent": "code-ai", "Accept": "application/vnd.github+json"}
    if _token():
        headers["Authorization"] = f"Bearer {_token()}"
    # verify follows ssl_verification: the company proxy re-signs TLS.
    return httpx.AsyncClient(
        verify=verify_ssl, follow_redirects=True, timeout=_TIMEOUT_S, headers=headers
    )


async def newest_release(*, verify_ssl: bool) -> Release | None:
    """The newest release the repository publishes, whatever its version."""
    url = f"{RELEASES_API.rstrip('/')}/repos/{RELEASES_REPOSITORY}/releases?per_page=30"
    async with _client(verify_ssl) as http:
        response = await http.get(url)
        response.raise_for_status()
        listing = response.json()
    if not isinstance(listing, list):
        return None
    return pick_newest(listing, include_prereleases=INCLUDE_PRERELEASES)


async def check(*, verify_ssl: bool) -> Release | None:
    """A release newer than this build, or None."""
    release = await newest_release(verify_ssl=verify_ssl)
    return release if is_newer(release, current_version()) else None


async def download(
    asset: Asset,
    directory: Path | None = None,
    *,
    verify_ssl: bool,
    progress: Callable[[int, int], None] | None = None,
) -> Path:
    """Fetch ``asset`` into ``directory``; only a complete file gets its name."""
    target_dir = directory or Path(tempfile.gettempdir()) / "code-ai-updates"
    target_dir.mkdir(parents=True, exist_ok=True)
    destination = target_dir / asset.name
    partial = destination.with_name(destination.name + ".part")
    # A private repository only serves assets through the API, with the token.
    url = asset.api_url if _token() and asset.api_url else asset.download_url
    async with _client(verify_ssl) as http:
        headers = {"Accept": "application/octet-stream"}
        async with http.stream("GET", url, headers=headers) as response:
            response.raise_for_status()
            total = int(response.headers.get("content-length") or asset.size or 0)
            done = 0
            with open(partial, "wb") as handle:
                async for chunk in response.aiter_bytes(1 << 16):
                    handle.write(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total)
    os.replace(partial, destination)
    return destination


# --- Replacing the running binary ---------------------------------------------


def running_binary() -> Path | None:
    """The executable to replace, when this is a frozen build users installed."""
    if not getattr(sys, "frozen", False):
        return None
    return Path(sys.executable).resolve()


def bundled_with_extension(path: Path) -> bool:
    """Whether ``path`` is the backend inside a VS Code extension, which the
    extension's own update replaces."""
    parts = [part.lower() for part in path.parts]
    return any(part in {".vscode", ".vscode-server", ".vscode-insiders"} for part in parts) and (
        "extensions" in parts
    )


def replace_binary(new: Path, current: Path) -> None:
    """Put ``new`` where ``current`` runs from.

    A running executable cannot be overwritten on Windows but can be renamed, so
    the old one steps aside as ``.old`` and is removed on the next start.
    """
    old = current.with_name(current.name + ".old")
    if old.exists():
        old.unlink()
    os.replace(current, old)
    try:
        shutil.copy2(new, current)
    except OSError:
        os.replace(old, current)
        raise
    if not sys.platform.startswith("win"):
        current.chmod(current.stat().st_mode | 0o111)


def remove_replaced(current: Path | None = None) -> None:
    """Clean up the binary a previous update set aside. Best-effort."""
    binary = current or running_binary()
    if binary is None:
        return
    try:
        binary.with_name(binary.name + ".old").unlink(missing_ok=True)
    except OSError:
        pass
