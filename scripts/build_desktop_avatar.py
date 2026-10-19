#!/usr/bin/env python3
"""Build the desktop avatar's host, code-ai-avatar, for Linux and Windows on Linux.

    python3 scripts/build_desktop_avatar.py --out out/desktop-avatar [--cache DIR]

cargo cannot be told to skip TLS verification, and the company proxy re-signs
it. So cargo never goes online: every crate desktop-avatar/Cargo.lock names is
fetched here, over unverified TLS, held to the SHA-256 the lock pins for it,
and unpacked as a vendored source cargo builds from with --offline. The
Windows standard library comes the same way, held to a hash pinned below.
macOS needs neither: its host is Python (src/code_ai/desktop_avatar/cocoa.py).

Linux needs WebKitGTK 4.1, GTK 3 and D-Bus headers, and Windows the MinGW-w64
cross compiler. What is missing is installed first with the distribution's
package manager (apt, dnf, pacman or zypper, through sudo when not root);
--no-install only names it. With no Rust, or one older than CI's, the pinned
toolchain is fetched into the cache the same way as the crates. The Windows
build ships with WebView2Loader.dll beside it.
Standard library only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import ssl
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path
from platform import machine

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10, as on Ubuntu 22.04
    tomllib = None

ROOT = Path(__file__).resolve().parents[1]
CRATE = ROOT / "desktop-avatar"

# The toolchain CI's image carries, and the hash of its Windows standard library.
RUST = "1.90.0"
WINDOWS = "x86_64-pc-windows-gnu"
WINDOWS_STD_SHA256 = "d82b3240908390cef1ddb88d2d922fa6d5e8abb6e6ef1d47948ec503ba694bf5"
# The same toolchain for a Linux machine without one: archive, the component in
# it, its hash.
LINUX = "x86_64-unknown-linux-gnu"
TOOLCHAIN = {
    f"rustc-{RUST}-{LINUX}": (
        "rustc",
        "48c2a42de9e92fcae8c24568f5fe40d5734696a6f80e83cc6d46eef1a78f13c9",
    ),
    f"cargo-{RUST}-{LINUX}": (
        "cargo",
        "9853db03d68578a30972e2755c89c66aec035fec641cf8f3a7117c81eec2578d",
    ),
    f"rust-std-{RUST}-{LINUX}": (
        f"rust-std-{LINUX}",
        "663f4ab7945b392d5e5294dec1b050a66820a20e86f084ec37eeb0f2f7ff5569",
    ),
}

# What the Linux build links, by pkg-config name, and the C compilers the
# targets link with (build scripts always run on the host, so cc either way).
MODULES = ("webkit2gtk-4.1", "gtk+-3.0", "dbus-1")
MINGW = "x86_64-w64-mingw32-gcc"
# Which package gives each of those, per package manager. dnf and zypper take
# the pkg-config name itself.
PACKAGES = {
    "apt-get": {
        "cc": "build-essential",
        "pkg-config": "pkg-config",
        "webkit2gtk-4.1": "libwebkit2gtk-4.1-dev",
        "gtk+-3.0": "libgtk-3-dev",
        "dbus-1": "libdbus-1-dev",
        MINGW: "gcc-mingw-w64-x86-64",
    },
    "dnf": {
        "cc": "gcc",
        "pkg-config": "pkgconf-pkg-config",
        **{module: f"pkgconfig({module})" for module in MODULES},
        MINGW: "mingw64-gcc",
    },
    "pacman": {
        "cc": "gcc",
        "pkg-config": "pkgconf",
        "webkit2gtk-4.1": "webkit2gtk-4.1",
        "gtk+-3.0": "gtk3",
        "dbus-1": "dbus",
        MINGW: "mingw-w64-gcc",
    },
    "zypper": {
        "cc": "gcc",
        "pkg-config": "pkg-config",
        **{module: f"pkgconfig({module})" for module in MODULES},
        MINGW: "mingw64-cross-gcc",
    },
}
INSTALL = {
    "apt-get": ["env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "install", "-y",
                "--no-install-recommends"],
    "dnf": ["dnf", "install", "-y"],
    "pacman": ["pacman", "-S", "--needed", "--noconfirm"],
    "zypper": ["zypper", "--non-interactive", "install"],
}
# CODE_AI_CRATES_MIRROR and CODE_AI_RUST_DIST_MIRROR swap the hosts for internal
# copies; the hashes still hold.
CRATES = "https://static.crates.io/crates"
RUST_DIST = "https://static.rust-lang.org/dist"

TARGETS = {"linux": "x86_64-unknown-linux-gnu", "windows": WINDOWS}
NAMES = {"linux": "code-ai-avatar", "windows": "code-ai-avatar.exe"}
# Linked statically only by MSVC: a MinGW build loads it from beside the exe.
# Microsoft's redistributable, inside webview2-com-sys and so under its hash.
WEBVIEW2_LOADER = "WebView2Loader.dll"


def _host(variable: str, default: str) -> str:
    # The workflow passes the repository variable through even when it is unset.
    return (os.environ.get(variable) or default).rstrip("/")


def crate_url(name: str, version: str) -> str:
    return f"{_host('CODE_AI_CRATES_MIRROR', CRATES)}/{name}/{name}-{version}.crate"


def rust_dist_url(name: str) -> str:
    return f"{_host('CODE_AI_RUST_DIST_MIRROR', RUST_DIST)}/{name}.tar.xz"


def _extract(archive: Path, into: Path | str) -> None:
    with tarfile.open(archive) as tar:
        # The safe filter where this Python has it (3.11.4 on); bookworm's is 3.11.2.
        safe = {"filter": "data"} if hasattr(tarfile, "data_filter") else {}
        tar.extractall(into, **safe)  # noqa: S202 - archives held to a pinned hash


def _download(url: str, sha256: str, to: Path) -> None:
    if to.is_file() and hashlib.sha256(to.read_bytes()).hexdigest() == sha256:
        return
    unverified = ssl._create_unverified_context()  # noqa: S323 - the hash is the check
    with urllib.request.urlopen(url, context=unverified, timeout=120) as response:  # noqa: S310
        data = response.read()
    got = hashlib.sha256(data).hexdigest()
    if got != sha256:
        raise SystemExit(f"{url}: SHA-256 {got}, expected {sha256}")
    to.parent.mkdir(parents=True, exist_ok=True)
    to.write_bytes(data)


def missing(platforms: list[str]) -> list[str]:
    """What the build needs from the system and does not find: commands, then modules."""
    needs = ["cc"] + (["pkg-config"] if "linux" in platforms else [])
    needs += [MINGW] if "windows" in platforms else []
    gone = [need for need in needs if not shutil.which(need)]
    if "linux" in platforms:
        if shutil.which("pkg-config"):
            gone += [m for m in MODULES if subprocess.run(["pkg-config", "--exists", m]).returncode]
        else:
            gone += MODULES
    return gone


def prerequisites(platforms: list[str], install: bool = True) -> None:
    """Install, with the system's package manager, what the build is missing."""
    gone = missing(platforms)
    if not gone:
        return
    manager = next((name for name in PACKAGES if shutil.which(name)), None)
    if manager is None:
        raise SystemExit(
            f"missing {', '.join(gone)}, and no apt-get, dnf, pacman or zypper to install it"
        )
    packages = sorted({PACKAGES[manager][need] for need in gone})
    sudo = [] if os.geteuid() == 0 else ["sudo"]
    command = [*sudo, *INSTALL[manager], *packages]
    if not install or (sudo and not shutil.which("sudo")):
        raise SystemExit(f"missing {', '.join(gone)}. Install it with:\n    {shlex.join(command)}")
    print(f"missing {', '.join(gone)}: installing {' '.join(packages)}", flush=True)
    # apt's lists may predate the package, or never have been fetched (containers).
    if manager == "apt-get" and subprocess.run([*sudo, "apt-get", "update"]).returncode:
        raise SystemExit(f"apt-get update failed. Install them by hand:\n    {shlex.join(command)}")
    if subprocess.run(command).returncode:
        raise SystemExit(f"the install failed. Try it by hand:\n    {shlex.join(command)}")
    still = missing(platforms)
    if still:
        raise SystemExit(f"still missing after the install: {', '.join(still)}")


def _version(rustc: str) -> tuple[int, ...]:
    out = subprocess.run([rustc, "--version"], capture_output=True, text=True).stdout
    found = re.search(r"(\d+)\.(\d+)\.(\d+)", out)
    return tuple(int(part) for part in found.groups()) if found else (0,)


def _sysroot(rustc: str) -> Path:
    return Path(subprocess.check_output([rustc, "--print", "sysroot"], text=True).strip())


def toolchain(platforms: list[str], cache: Path) -> dict[str, str]:
    """The environment cargo runs in: this machine's Rust when it will do, else CI's."""
    path = os.environ.get("PATH", "")
    # Where rustup puts it, on PATH only from the next login shell.
    cargo_bin = Path(os.environ.get("CARGO_HOME") or Path.home() / ".cargo") / "bin"
    search = os.pathsep.join([path, str(cargo_bin)])
    rustc = shutil.which("rustc", path=search)
    if rustc and shutil.which("cargo", path=search):
        version = _version(rustc)
        pinned = tuple(int(part) for part in RUST.split("."))
        # The Windows library is fetched for RUST alone; rustup would add it
        # online, and through the proxy it cannot.
        windows_ok = (
            "windows" not in platforms
            or version == pinned
            or (_sysroot(rustc) / "lib" / "rustlib" / WINDOWS).is_dir()
        )
        if version >= pinned and windows_ok:
            return {**os.environ, "PATH": search}
    if sys.platform != "linux" or machine() != "x86_64":
        raise SystemExit(f"Rust {RUST} or newer is needed: https://rustup.rs")
    prefix = cache / f"rust-{RUST}"
    if not (prefix / "bin" / "cargo").is_file():
        fetch_toolchain(prefix)
    return {**os.environ, "PATH": os.pathsep.join([str(prefix / "bin"), path])}


def fetch_toolchain(prefix: Path) -> None:
    """Rust RUST for Linux, unpacked into prefix: rustc, cargo and the standard library."""
    print(f"no Rust {RUST} here: fetching it into {prefix} (115 MB, once)", flush=True)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=prefix.parent) as scratch:
        staged = Path(scratch) / "rust"
        for name, (component, sha256) in TOOLCHAIN.items():
            archive = Path(scratch) / f"{name}.tar.xz"
            _download(rust_dist_url(name), sha256, archive)
            _extract(archive, scratch)
            # What its install.sh would copy, less the bookkeeping.
            shutil.copytree(Path(scratch) / name / component, staged, dirs_exist_ok=True)
            archive.unlink()
        (staged / "manifest.in").unlink(missing_ok=True)
        shutil.rmtree(prefix, ignore_errors=True)
        staged.rename(prefix)


def lock_packages(lock: Path) -> list[dict[str, str]]:
    text = lock.read_text(encoding="utf-8")
    if tomllib is not None:
        return tomllib.loads(text)["package"]
    # Without tomllib: a lock's packages are flat key = "value" lines.
    return [
        dict(re.findall(r'^(\w+) = "([^"]*)"$', block, re.MULTILINE))
        for block in text.split("[[package]]")[1:]
    ]


def vendor(lock: Path, into: Path, cache: Path) -> None:
    """Every registry crate in the lock, unpacked where cargo looks for a vendored source."""
    packages = lock_packages(lock)
    into.mkdir(parents=True, exist_ok=True)
    fetched = 0
    for package in packages:
        if "checksum" not in package:
            continue  # the crate itself, not from the registry
        name, version, sha256 = package["name"], package["version"], package["checksum"]
        folder = into / f"{name}-{version}"
        if (folder / ".cargo-checksum.json").is_file():
            continue
        archive = cache / f"{name}-{version}.crate"
        _download(crate_url(name, version), sha256, archive)
        _extract(archive, into)
        # No per-file sums: the archive's own was checked against the lock.
        (folder / ".cargo-checksum.json").write_text(
            json.dumps({"files": {}, "package": sha256}), encoding="utf-8"
        )
        fetched += 1
    print(f"vendored {fetched} new crate(s) into {into}")


def windows_std(env: dict[str, str]) -> None:
    """The Windows standard library in the toolchain, if it is not there already."""
    rustc = shutil.which("rustc", path=env["PATH"]) or "rustc"
    sysroot = _sysroot(rustc)
    if (sysroot / "lib" / "rustlib" / WINDOWS).is_dir():
        return
    version = subprocess.check_output([rustc, "--version"], text=True).split()[1]
    if version != RUST:
        raise SystemExit(f"rustc {version}: the Windows library's hash is pinned for {RUST}")
    name = f"rust-std-{RUST}-{WINDOWS}"
    with tempfile.TemporaryDirectory() as scratch:
        archive = Path(scratch) / f"{name}.tar.xz"
        _download(rust_dist_url(name), WINDOWS_STD_SHA256, archive)
        _extract(archive, scratch)
        library = Path(scratch) / name / f"rust-std-{WINDOWS}" / "lib" / "rustlib" / WINDOWS
        shutil.copytree(library, sysroot / "lib" / "rustlib" / WINDOWS)
    print(f"installed the {WINDOWS} standard library into {sysroot}")


def build(platform: str, sources: Path, target_dir: Path, out: Path, env: dict[str, str]) -> None:
    triple = TARGETS[platform]
    if platform == "windows":
        windows_std(env)
    subprocess.run(
        [
            shutil.which("cargo", path=env["PATH"]) or "cargo",
            "build", "--release", "--offline", "--locked", "--target", triple,
            "--config", 'source.crates-io.replace-with="vendored"',
            "--config", f'source.vendored.directory="{sources.as_posix()}"',
        ],
        cwd=CRATE,
        env={**env, "CARGO_TARGET_DIR": str(target_dir)},
        check=True,
    )
    out.mkdir(parents=True, exist_ok=True)
    built = target_dir / triple / "release" / NAMES[platform]
    shutil.copy2(built, out / NAMES[platform])
    print(f"{out / NAMES[platform]} ({built.stat().st_size // 1024} KB)")
    if platform == "windows":
        (loader,) = sources.glob(f"webview2-com-sys-*/x64/{WEBVIEW2_LOADER}")
        shutil.copy2(loader, out / WEBVIEW2_LOADER)
        print(f"{out / WEBVIEW2_LOADER} (from {loader.parent.parent.name})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--cache", type=Path, default=ROOT / "desktop-avatar" / "target" / "ci")
    parser.add_argument("--targets", default="linux,windows")
    parser.add_argument(
        "--no-install", action="store_true",
        help="name the missing system packages instead of installing them",
    )
    args = parser.parse_args(argv)
    platforms = [name.strip() for name in args.targets.split(",") if name.strip()]
    cache = args.cache.resolve()
    # First, so a sudo password is asked for before minutes of downloads.
    if sys.platform == "linux":
        prerequisites(platforms, install=not args.no_install)
    env = toolchain(platforms, cache)
    sources = cache / "vendor"
    vendor(CRATE / "Cargo.lock", sources, cache / "crates")
    for platform in platforms:
        build(platform, sources, cache / "target", args.out.resolve(), env)
    return 0


if __name__ == "__main__":
    sys.exit(main())
