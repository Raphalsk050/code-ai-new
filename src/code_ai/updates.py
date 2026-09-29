"""Updates from GitHub releases: is there a newer build, and fetch it.

Every CI build publishes a release tagged ``vX.Y.Z`` with the binaries and the
VS Code packages named after the version (see .github/workflows and
scripts/release.py). This module finds the newest one and downloads the asset
that fits the machine; the front ends decide what installing it means.
"""

from __future__ import annotations

import asyncio
import base64
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from code_ai.version import read_version

# --- Where Code-AI looks for updates -----------------------------------------
# The GitHub API to ask. For GitHub Enterprise it is "https://<host>/api/v3",
# e.g. "https://github.sec.samsung.net/api/v3".
RELEASES_API = "https://api.github.com"
# The "owner/repository" that publishes the releases. Left as the placeholder,
# nothing is looked up and no update is ever offered.
RELEASES_REPOSITORY = "owner/repository"
# Whether builds from other branches (published as pre-releases) count as
# updates, or only releases from the default branch and tags.
INCLUDE_PRERELEASES = True
# A token for a private repository. Unset, the login this machine already has
# for the host is used (the GitHub CLI's, then git's credential manager), and
# without one the request goes anonymous, which a public repository takes.
RELEASES_TOKEN_ENV = "CODE_AI_UPDATE_TOKEN"
# -----------------------------------------------------------------------------

_UNSET_REPOSITORY = "owner/repository"
UNSET_MESSAGE = (
    "Updates are off: set RELEASES_REPOSITORY in code_ai/updates.py to the "
    "repository that publishes the releases."
)
_TAG = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
_TIMEOUT_S = 30.0
_TOOL_TIMEOUT_S = 15.0


class UpdateError(RuntimeError):
    """GitHub refused the lookup; the message says what to fix."""


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


# --- Signing in with the login this machine already has -----------------------


@dataclass(frozen=True, slots=True)
class Credential:
    secret: str
    username: str = ""
    # Set on purpose: a refusal is reported, never swapped for another login.
    explicit: bool = False

    def header(self) -> str:
        if self.username:
            pair = f"{self.username}:{self.secret}".encode()
            return "Basic " + base64.b64encode(pair).decode("ascii")
        return f"Bearer {self.secret}"


# Logins the API turned down this session, so a stale one is not tried again.
_refused: set[str] = set()


def login_host() -> str:
    """The host a login is kept under; github.com's API lives on api.github.com."""
    host = (urlsplit(RELEASES_API).hostname or "").lower()
    return "github.com" if host == "api.github.com" else host


def find_credential() -> Credential | None:
    """The configured token, else the first login of this machine's for the host."""
    explicit = os.environ.get(RELEASES_TOKEN_ENV, "").strip()
    if explicit:
        return Credential(explicit, explicit=True)
    for credential in _machine_logins(login_host()):
        if credential.secret not in _refused:
            return credential
    return None


def _machine_logins(host: str) -> Iterator[Credential]:
    """Cheapest first, and none of them may prompt: this runs on every start."""
    if not host:
        return
    # The GitHub CLI's variables: one pair for github.com, one for Enterprise.
    if host == "github.com":
        names = ("GH_TOKEN", "GITHUB_TOKEN")
    else:
        names = ("GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN")
    for name in names:
        if value := os.environ.get(name, "").strip():
            yield Credential(value)
    if token := _run_tool(["gh", "auth", "token", "--hostname", host]).strip():
        yield Credential(token)
    # Git Credential Manager keeps the login from the last clone or push.
    scheme = urlsplit(RELEASES_API).scheme or "https"
    filled = _run_tool(
        ["git", "-c", "credential.interactive=never", "-c", "core.askPass=", "credential", "fill"],
        stdin=f"protocol={scheme}\nhost={host}\n\n",
    )
    fields = dict(line.partition("=")[::2] for line in filled.splitlines())
    if secret := fields.get("password", "").strip():
        yield Credential(secret, username=fields.get("username", "").strip())


def _run_tool(argv: list[str], stdin: str = "") -> str:
    """A local tool's output, or "" when it is missing, fails or wants to ask."""
    exe = shutil.which(argv[0])
    if exe is None:
        return ""
    env = {k: v for k, v in os.environ.items() if k not in ("GIT_ASKPASS", "SSH_ASKPASS")}
    env.update(GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never", GH_PROMPT_DISABLED="1")
    try:
        done = subprocess.run(
            [exe, *argv[1:]],
            input=stdin,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_TOOL_TIMEOUT_S,
            env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout if done.returncode == 0 else ""


def _signed(credential: Credential | None, url: str) -> dict[str, str]:
    """The Authorization header, and only for the API's own host."""
    if credential is None or urlsplit(url).hostname != urlsplit(RELEASES_API).hostname:
        return {}
    return {"Authorization": credential.header()}


def _client(verify_ssl: bool):
    import httpx

    headers = {"User-Agent": "code-ai", "Accept": "application/vnd.github+json"}
    # verify follows ssl_verification: the company proxy re-signs TLS.
    return httpx.AsyncClient(
        verify=verify_ssl, follow_redirects=True, timeout=_TIMEOUT_S, headers=headers
    )


def configured() -> bool:
    return RELEASES_REPOSITORY.strip() not in ("", _UNSET_REPOSITORY)


async def newest_release(*, verify_ssl: bool) -> Release | None:
    """The newest release the repository publishes, whatever its version."""
    if not configured():
        return None
    url = f"{RELEASES_API.rstrip('/')}/repos/{RELEASES_REPOSITORY}/releases?per_page=30"
    async with _client(verify_ssl) as http:
        while True:
            credential = await asyncio.to_thread(find_credential)
            response = await http.get(url, headers=_signed(credential, url))
            if response.status_code != 401 or credential is None or credential.explicit:
                break
            # A stale login; the next one, or none, gets the same question.
            _refused.add(credential.secret)
        if response.status_code in (401, 403, 404):
            raise UpdateError(_refusal(response.status_code, credential))
        response.raise_for_status()
        listing = response.json()
    if not isinstance(listing, list):
        return None
    return pick_newest(listing, include_prereleases=INCLUDE_PRERELEASES)


def _refusal(status: int, credential: Credential | None) -> str:
    where = f"{RELEASES_REPOSITORY} on {login_host()}"
    if credential is None:
        return (
            f"GitHub answered {status} for {where}. A private repository needs a login: "
            f"sign in to {login_host()} with git or gh, or set {RELEASES_TOKEN_ENV}."
        )
    return f"GitHub answered {status} for {where}: check RELEASES_REPOSITORY and the login."


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
    credential = await asyncio.to_thread(find_credential)
    # A private repository only serves assets through the API, signed.
    signed = _signed(credential, asset.api_url) if asset.api_url else {}
    url = asset.api_url if signed else asset.download_url
    async with _client(verify_ssl) as http:
        headers = {"Accept": "application/octet-stream", **signed}
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
