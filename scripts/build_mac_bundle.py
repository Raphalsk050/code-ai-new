#!/usr/bin/env python3
"""Assemble Code-AI for macOS on any machine: no Mac, no compiler.

PyInstaller cannot cross-compile and macOS has no Wine, so the Mac build is not
frozen, and it does not need to be. python-build-standalone publishes a
relocatable CPython for macOS and PyPI has macOS wheels of every dependency,
each built and signed on a Mac by its own project. This unpacks the first,
installs the second into it and adds a launcher:

    code-ai-mac-arm64/
      code-ai               the command, a shell script
      runtime.tar.gz        CPython 3.12, Code-AI in its site-packages; the
                            launcher unpacks it on the first start
      code-ai-bundle.json   what must stay executable, for the VSIX

packed as code-ai-mac-<arch>-v<version>.tar.gz; vscode-extension/scripts/
package.mjs turns the same folder into the darwin VSIXes.

Nothing here can run the result. A Mac registered as a runner does, in the
workflow's macos-check job (scripts/setup_mac_runner.sh sets one up).

Standard library only, as CI runs it in a stock python:3.12 container.
Downloads skip TLS verification (the company proxy re-signs it) and are held
to a pinned SHA-256 instead.
"""

from __future__ import annotations

import argparse
import compileall
import hashlib
import json
import os
import py_compile
import shutil
import ssl
import struct
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = ROOT / "src" / "code_ai" / "version.json"

PYTHON = "3.12"
PBS_PYTHON = "3.12.14"
PBS_RELEASE = "20260924"
# CODE_AI_PBS_MIRROR swaps the host for an internal mirror; the hash still holds.
PBS_BASE = "https://github.com/astral-sh/python-build-standalone/releases/download"

CPU_X86_64 = 0x01000007
CPU_ARM64 = 0x0100000C
_CPU_NAMES = {CPU_X86_64: "x86_64", CPU_ARM64: "arm64"}


@dataclass(frozen=True)
class Arch:
    name: str
    """In file names and VSIX targets: arm64, x64."""
    triple: str
    sha256: str
    wheel_platform: str
    """The oldest macOS whose wheels pip may pick, and so the oldest it runs on."""
    cpu: int
    machine: str
    """platform.machine() on that Mac, for environment markers."""


ARCHES = {
    "arm64": Arch(
        name="arm64",
        triple="aarch64-apple-darwin",
        sha256="9763f43db2481a6af36af82ec40302aab7a73632f880129d07a6e81aec846277",
        wheel_platform="macosx_11_0_arm64",
        cpu=CPU_ARM64,
        machine="arm64",
    ),
    "x64": Arch(
        name="x64",
        triple="x86_64-apple-darwin",
        sha256="0d6a4a299908123f00bc844df737603f047ff9eba14fda6cad83f3cf3cb3a2af",
        wheel_platform="macosx_11_0_x86_64",
        cpu=CPU_X86_64,
        machine="x86_64",
    ),
}

# Parts of the runtime Code-AI never touches: the test suite, Tk and IDLE, and
# what building C extensions needs.
_PRUNE = (
    "share",
    "include",
    "lib/pkgconfig",
    f"lib/python{PYTHON}/test",
    f"lib/python{PYTHON}/idlelib",
    f"lib/python{PYTHON}/tkinter",
    f"lib/python{PYTHON}/turtledemo",
    f"lib/python{PYTHON}/config-{PYTHON}-darwin",
)
_PRUNE_GLOBS = ("lib/tcl*", "lib/tk*", "lib/itcl*", "lib/thread*", "lib/libtcl*", "lib/libtk*")

_SITE = f"runtime/lib/python{PYTHON}/site-packages"
# What a working bundle cannot be without; a glob where the name carries a tag.
_REQUIRED = (
    "code-ai",
    f"runtime/bin/python{PYTHON}",
    f"{_SITE}/code_ai/__main__.py",
    f"{_SITE}/playwright/driver/node",
    f"{_SITE}/pydantic_core/_pydantic_core.cpython-*-darwin.so",
    f"{_SITE}/tiktoken/_tiktoken.cpython-*-darwin.so",
)

LAUNCHER = f"""#!/bin/sh
# Code-AI for macOS: the Python beside this script, with Code-AI installed in
# it. A symlink to this file (a PATH shim) is followed back to the bundle.
self=$0
while [ -L "$self" ]; do
  link=$(readlink "$self")
  case $link in
    /*) self=$link ;;
    *) self=$(dirname "$self")/$link ;;
  esac
done
here=$(cd "$(dirname "$self")" && pwd -P)
python="$here/runtime/bin/python{PYTHON}"
# CODE_AI_TRACE=1 shows every step here, and where Python is if it stalls.
[ -z "${{CODE_AI_TRACE:-}}" ] || set -x
# The runtime ships as one archive: as 11,000 loose files a VSIX takes minutes
# to pack and to install. The first start unpacks it beside this script, once;
# a second start meanwhile waits for it instead of unpacking it again.
unpack() {{
  echo "Code-AI: unpacking its Python, once (a minute or more)..." >&2
  echo $$ > "$here/.unpacking/pid"
  # Cut off halfway, it must not leave a lock the next start waits on.
  trap 'rm -rf "$here/runtime.partial" "$here/.unpacking"; exit 130' INT TERM HUP
  rm -rf "$here/runtime.partial"
  mkdir "$here/runtime.partial"
  if tar -xzf "$here/runtime.tar.gz" -C "$here/runtime.partial"; then
    mv "$here/runtime.partial/runtime" "$here/runtime"
    rm -f "$here/runtime.tar.gz"
  fi
  rm -rf "$here/runtime.partial" "$here/.unpacking"
  trap - INT TERM HUP
}}
if [ ! -x "$python" ] && [ -f "$here/runtime.tar.gz" ]; then
  if mkdir "$here/.unpacking" 2>/dev/null; then
    unpack
  else
    owner=$(cat "$here/.unpacking/pid" 2>/dev/null || true)
    if [ -n "$owner" ] && kill -0 "$owner" 2>/dev/null; then
      echo "Code-AI: another start is unpacking its Python; waiting for it..." >&2
      waited=0
      while [ -d "$here/.unpacking" ] && [ ! -x "$python" ] && [ "$waited" -lt 600 ]; do
        sleep 1
        waited=$((waited + 1))
      done
    else
      # A lock with no live owner is an unpack that was cut off: nobody is
      # coming to finish it, so this start does.
      rm -rf "$here/.unpacking"
      if mkdir "$here/.unpacking" 2>/dev/null; then unpack; fi
    fi
  fi
fi
if [ ! -x "$python" ]; then
  echo "Code-AI: its Python is missing from $here - install Code-AI again." >&2
  exit 1
fi
# Unpacked from a browser download, the folder carries macOS's quarantine flag
# and Gatekeeper refuses the Python in it ("could not verify"). Clearing the
# flag on this folder is what Open Anyway would do, file by file.
if xattr -p com.apple.quarantine "$python" >/dev/null 2>&1; then
  xattr -dr com.apple.quarantine "$here" 2>/dev/null || true
fi
CODE_AI_BUNDLE=$here
export CODE_AI_BUNDLE
exec "$python" -I -m code_ai "$@"
"""


def release_version() -> str:
    data = json.loads(VERSION_FILE.read_text(encoding="utf-8"))
    return f"{data['major']}.{data['minor']}.{data['patch']}"


def pbs_url(arch: Arch) -> str:
    base = (os.environ.get("CODE_AI_PBS_MIRROR") or PBS_BASE).rstrip("/")
    name = f"cpython-{PBS_PYTHON}+{PBS_RELEASE}-{arch.triple}-install_only.tar.gz"
    return f"{base}/{PBS_RELEASE}/{name}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def download(url: str, sha256: str, cache: Path) -> Path:
    """The file at ``url``, from the cache when the copy there has the pinned hash."""
    target = cache / url.rsplit("/", 1)[-1]
    if target.is_file() and _sha256(target) == sha256:
        return target
    cache.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    # Unverified TLS on purpose: the proxy re-signs it, and the hash is the check.
    context = ssl._create_unverified_context()
    request = urllib.request.Request(url, headers={"User-Agent": "code-ai-build"})
    with urllib.request.urlopen(request, context=context, timeout=120) as response:
        with open(partial, "wb") as out:
            shutil.copyfileobj(response, out, 1 << 20)
    actual = _sha256(partial)
    if actual != sha256:
        partial.unlink()
        raise SystemExit(f"{url}: SHA-256 is {actual}, expected {sha256}")
    os.replace(partial, target)
    return target


def unpack_runtime(archive: Path, bundle: Path) -> Path:
    runtime = bundle / "runtime"
    with tempfile.TemporaryDirectory() as scratch, tarfile.open(archive) as tar:
        tar.extractall(scratch, filter="data")
        shutil.move(os.path.join(scratch, "python"), runtime)
    return runtime


def prune(runtime: Path) -> None:
    for relative in _PRUNE:
        shutil.rmtree(runtime / relative, ignore_errors=True)
    for pattern in _PRUNE_GLOBS:
        for path in runtime.glob(pattern):
            shutil.rmtree(path) if path.is_dir() else path.unlink()
    for path in (runtime / "lib" / f"python{PYTHON}" / "lib-dynload").glob("_tkinter*"):
        path.unlink()
    # Only the interpreter itself: the rest of bin/ is symlinks and scripts
    # whose shebangs point at the machine python-build-standalone built on.
    for path in (runtime / "bin").iterdir():
        if path.name != f"python{PYTHON}":
            path.unlink()


def wheel_metadata(wheel: Path) -> str:
    with zipfile.ZipFile(wheel) as archive:
        name = next(n for n in archive.namelist() if n.endswith(".dist-info/METADATA"))
        return archive.read(name).decode("utf-8")


def mac_requirements(metadata: str, arch: Arch) -> list[str]:
    """Code-AI's requirements, their markers read as on that Mac.

    pip reads markers against the machine it runs on, so a Windows host would
    ask for pywinpty, which has no Mac wheel, and leave pexpect out.
    """
    from pip._vendor.packaging.requirements import Requirement

    environment = {
        "sys_platform": "darwin",
        "platform_system": "Darwin",
        "os_name": "posix",
        "platform_machine": arch.machine,
        "python_version": PYTHON,
        "python_full_version": PBS_PYTHON,
        "implementation_name": "cpython",
        "platform_python_implementation": "CPython",
        "extra": "",
    }
    wanted = []
    for line in metadata.splitlines():
        if not line.startswith("Requires-Dist:"):
            continue
        requirement = Requirement(line.split(":", 1)[1].strip())
        if requirement.marker is None or requirement.marker.evaluate(environment):
            requirement.marker = None
            wanted.append(str(requirement))
    return wanted


def pip_install_args(site: Path, arch: Arch, requirements: list[str]) -> list[str]:
    """Wheels for that Mac only: nothing is built here, so nothing can be built for it."""
    return [
        "install",
        "--target",
        str(site),
        "--upgrade",
        "--no-compile",
        "--disable-pip-version-check",
        "--no-warn-script-location",
        "--only-binary=:all:",
        "--platform",
        arch.wheel_platform,
        "--python-version",
        PYTHON,
        "--implementation",
        "cp",
        "--abi",
        "cp" + PYTHON.replace(".", ""),
        *requirements,
    ]


def _pip(*args: str) -> None:
    subprocess.run([sys.executable, "-m", "pip", *args], check=True)


def build_wheel(scratch: Path) -> Path:
    _pip("wheel", "--no-deps", "--disable-pip-version-check", "-w", str(scratch), str(ROOT))
    return next(scratch.glob("code_ai-*.whl"))


def install_code_ai(runtime: Path, arch: Arch, wheel: Path) -> None:
    site = runtime / "lib" / f"python{PYTHON}" / "site-packages"
    # Code-AI itself is pure Python; its requirements go in with markers decided above.
    _pip("install", "--target", str(site), "--no-deps", "--no-compile", str(wheel))
    _pip(*pip_install_args(site, arch, mac_requirements(wheel_metadata(wheel), arch)))
    shutil.rmtree(site / "bin", ignore_errors=True)


def compile_bytecode(runtime: Path) -> None:
    """Precompiled, so the first start on the Mac does not pay for it.

    Only when this Python matches the runtime's: bytecode is per version. The
    hash is not checked, since unpacking a VSIX shifts the timestamps.
    """
    if f"{sys.version_info[0]}.{sys.version_info[1]}" != PYTHON:
        return
    site = runtime / "lib" / f"python{PYTHON}" / "site-packages"
    compileall.compile_dir(
        site,
        quiet=1,
        workers=0,
        invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH,
    )


def write_launcher(bundle: Path) -> None:
    launcher = bundle / "code-ai"
    launcher.write_bytes(LAUNCHER.encode("utf-8"))
    launcher.chmod(0o755)


def macho(path: Path) -> tuple[set[int], bool] | None:
    """(CPU types, is an executable) of a Mach-O file, None for anything else."""
    with open(path, "rb") as handle:
        head = handle.read(16)
        if len(head) < 16:
            return None
        if head[:4] == b"\xcf\xfa\xed\xfe":
            # 64-bit, little-endian: every Mac CPU. cputype, subtype, filetype.
            cpu, _subtype, filetype = struct.unpack("<III", head[4:16])
            return {cpu}, filetype == 2
        if head[:4] == b"\xca\xfe\xba\xbe":
            count = struct.unpack(">I", head[4:8])[0]
            # Java class files share the magic; their "count" is a version, 45+.
            if not 0 < count < 20:
                return None
            cpus, executable = set(), False
            for index in range(count):
                handle.seek(8 + 20 * index)
                cpu, _subtype, offset, _size, _align = struct.unpack(">IIIII", handle.read(20))
                handle.seek(offset + 12)
                filetype = struct.unpack("<I", handle.read(4))[0]
                cpus.add(cpu)
                executable = executable or filetype == 2
            return cpus, executable
    return None


def check_bundle(bundle: Path, arch: Arch) -> tuple[list[str], list[str]]:
    """Problems that would only show on the Mac, and what has to stay executable."""
    problems: list[str] = []
    executables = ["code-ai"]
    for path in sorted(bundle.rglob("*")):
        relative = path.relative_to(bundle).as_posix()
        if path.is_symlink():
            problems.append(f"{relative} is a symlink, which a VSIX cannot carry")
            continue
        if not path.is_file():
            continue
        info = macho(path)
        if info is not None:
            cpus, executable = info
            if arch.cpu not in cpus:
                names = ", ".join(sorted(_CPU_NAMES.get(cpu, hex(cpu)) for cpu in cpus))
                problems.append(f"{relative} is built for {names}, not {arch.name}")
            if executable:
                executables.append(relative)
        elif path.suffix == ".sh":
            executables.append(relative)
        elif path.suffix == ".so":
            with open(path, "rb") as handle:
                if handle.read(4) == b"\x7fELF":
                    problems.append(f"{relative} is a Linux library")
    for required in _REQUIRED:
        if not any(bundle.glob(required)):
            problems.append(f"missing {required}")
    return problems, executables


def pack(bundle: Path, executables: set[str], out: Path) -> None:
    """A tarball whose modes come from the manifest, not from the build host's disk."""
    top = bundle.name
    partial = out.with_name(out.name + ".part")
    with tarfile.open(partial, "w:gz", compresslevel=6) as tar:
        for path in [bundle, *sorted(bundle.rglob("*"))]:
            relative = path.relative_to(bundle).as_posix()
            name = top if relative == "." else f"{top}/{relative}"
            info = tar.gettarinfo(str(path), name)
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            if path.is_dir():
                info.mode = 0o755
                tar.addfile(info)
                continue
            info.mode = 0o755 if relative in executables else 0o644
            with open(path, "rb") as handle:
                tar.addfile(info, handle)
    os.replace(partial, out)


def build(arch_name: str, out_dir: Path, cache: Path, wheel: Path) -> Path:
    arch = ARCHES[arch_name]
    version = release_version()
    bundle = out_dir / f"code-ai-mac-{arch.name}"
    shutil.rmtree(bundle, ignore_errors=True)
    bundle.mkdir(parents=True)

    runtime = unpack_runtime(download(pbs_url(arch), arch.sha256, cache), bundle)
    prune(runtime)
    install_code_ai(runtime, arch, wheel)
    compile_bytecode(runtime)
    write_launcher(bundle)

    problems, executables = check_bundle(bundle, arch)
    if problems:
        raise SystemExit(f"code-ai-mac-{arch.name}:\n  " + "\n  ".join(problems))
    # One archive for the runtime, which the launcher unpacks on the first start.
    inside = {name.removeprefix("runtime/") for name in executables if name.startswith("runtime/")}
    pack(runtime, inside, bundle / "runtime.tar.gz")
    shutil.rmtree(runtime)
    manifest = {
        "version": version,
        "arch": arch.name,
        "python": PBS_PYTHON,
        "executables": ["code-ai"],
    }
    (bundle / "code-ai-bundle.json").write_text(json.dumps(manifest, indent=2) + "\n")

    tarball = out_dir / f"code-ai-mac-{arch.name}-v{version}.tar.gz"
    pack(bundle, {"code-ai"}, tarball)
    return tarball


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="build_mac_bundle", description=__doc__.splitlines()[0])
    parser.add_argument(
        "--arch", action="append", choices=sorted(ARCHES), help="Repeat for both; default both."
    )
    parser.add_argument("--out", type=Path, default=ROOT / "dist" / "mac")
    cache = os.environ.get("CODE_AI_BUILD_CACHE") or Path.home() / ".cache" / "code-ai-build"
    parser.add_argument("--cache", type=Path, default=Path(cache))
    args = parser.parse_args(argv)

    args.out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as scratch:
        wheel = build_wheel(Path(scratch))
        for arch in args.arch or sorted(ARCHES):
            tarball = build(arch, args.out, args.cache, wheel)
            size = tarball.stat().st_size / 1024 / 1024
            print(f"{tarball.name}: {size:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
