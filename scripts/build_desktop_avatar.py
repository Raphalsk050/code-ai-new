#!/usr/bin/env python3
"""Build the desktop avatar's host, code-ai-avatar, for Linux and Windows on Linux.

    python3 scripts/build_desktop_avatar.py --out out/desktop-avatar [--cache DIR]

cargo cannot be told to skip TLS verification, and the company proxy re-signs
it. So cargo never goes online: every crate desktop-avatar/Cargo.lock names is
fetched here, over unverified TLS, held to the SHA-256 the lock pins for it,
and unpacked as a vendored source cargo builds from with --offline. The
Windows standard library comes the same way, held to a hash pinned below.
macOS needs neither: its host is Python (src/code_ai/desktop_avatar/cocoa.py).

Linux needs libwebkit2gtk-4.1-dev and Windows the MinGW-w64 cross compiler,
which CI's desktop-avatar job installs in a rust:1.90-bookworm container. The
Windows build ships with WebView2Loader.dll beside it.
Standard library only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import ssl
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CRATE = ROOT / "desktop-avatar"

# The toolchain CI's image carries, and the hash of its Windows standard library.
RUST = "1.90.0"
WINDOWS = "x86_64-pc-windows-gnu"
WINDOWS_STD_SHA256 = "d82b3240908390cef1ddb88d2d922fa6d5e8abb6e6ef1d47948ec503ba694bf5"
# CODE_AI_CRATES_MIRROR and CODE_AI_RUST_DIST_MIRROR swap the hosts for internal
# copies; the hashes still hold.
CRATES = os.environ.get("CODE_AI_CRATES_MIRROR", "https://static.crates.io/crates").rstrip("/")
RUST_DIST = os.environ.get("CODE_AI_RUST_DIST_MIRROR", "https://static.rust-lang.org/dist").rstrip("/")

TARGETS = {"linux": "x86_64-unknown-linux-gnu", "windows": WINDOWS}
NAMES = {"linux": "code-ai-avatar", "windows": "code-ai-avatar.exe"}
# Linked statically only by MSVC: a MinGW build loads it from beside the exe.
# Microsoft's redistributable, inside webview2-com-sys and so under its hash.
WEBVIEW2_LOADER = "WebView2Loader.dll"


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


def vendor(lock: Path, into: Path, cache: Path) -> None:
    """Every registry crate in the lock, unpacked where cargo looks for a vendored source."""
    packages = tomllib.loads(lock.read_text(encoding="utf-8"))["package"]
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
        _download(f"{CRATES}/{name}/{name}-{version}.crate", sha256, archive)
        _extract(archive, into)
        # No per-file sums: the archive's own was checked against the lock.
        (folder / ".cargo-checksum.json").write_text(
            json.dumps({"files": {}, "package": sha256}), encoding="utf-8"
        )
        fetched += 1
    print(f"vendored {fetched} new crate(s) into {into}")


def windows_std() -> None:
    """The Windows standard library in the toolchain, if it is not there already."""
    sysroot = Path(subprocess.check_output(["rustc", "--print", "sysroot"], text=True).strip())
    if (sysroot / "lib" / "rustlib" / WINDOWS).is_dir():
        return
    version = subprocess.check_output(["rustc", "--version"], text=True).split()[1]
    if version != RUST:
        raise SystemExit(f"rustc {version}: the Windows library's hash is pinned for {RUST}")
    name = f"rust-std-{RUST}-{WINDOWS}"
    with tempfile.TemporaryDirectory() as scratch:
        archive = Path(scratch) / f"{name}.tar.xz"
        _download(f"{RUST_DIST}/{name}.tar.xz", WINDOWS_STD_SHA256, archive)
        _extract(archive, scratch)
        library = Path(scratch) / name / f"rust-std-{WINDOWS}" / "lib" / "rustlib" / WINDOWS
        shutil.copytree(library, sysroot / "lib" / "rustlib" / WINDOWS)
    print(f"installed the {WINDOWS} standard library into {sysroot}")


def build(platform: str, sources: Path, target_dir: Path, out: Path) -> None:
    triple = TARGETS[platform]
    if platform == "windows":
        windows_std()
    subprocess.run(
        [
            "cargo", "build", "--release", "--offline", "--locked", "--target", triple,
            "--config", 'source.crates-io.replace-with="vendored"',
            "--config", f'source.vendored.directory="{sources.as_posix()}"',
        ],
        cwd=CRATE,
        env={**os.environ, "CARGO_TARGET_DIR": str(target_dir)},
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
    args = parser.parse_args(argv)
    cache = args.cache.resolve()
    sources = cache / "vendor"
    vendor(CRATE / "Cargo.lock", sources, cache / "crates")
    for platform in args.targets.split(","):
        build(platform.strip(), sources, cache / "target", args.out.resolve())
    return 0


if __name__ == "__main__":
    sys.exit(main())
