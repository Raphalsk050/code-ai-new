"""The macOS build is assembled off a Mac, so everything a Mac would catch is caught here."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import os
import shutil
import struct
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


mac = _load("build_mac_bundle")
ARM64 = mac.ARCHES["arm64"]


def _thin(cpu: int, filetype: int) -> bytes:
    return b"\xcf\xfa\xed\xfe" + struct.pack("<III", cpu, 0, filetype) + b"\0" * 64


def _fat(*slices: tuple[int, int]) -> bytes:
    header = b"\xca\xfe\xba\xbe" + struct.pack(">I", len(slices))
    body = b""
    offset = 8 + 20 * len(slices)
    for cpu, filetype in slices:
        header += struct.pack(">IIIII", cpu, 0, offset + len(body), 32, 0)
        body += _thin(cpu, filetype)
    return header + body


def _write(path: Path, data: bytes | str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode() if isinstance(data, str) else data)
    return path


def test_mach_o_files_tell_their_cpus_and_whether_they_run(tmp_path) -> None:
    exe = _write(tmp_path / "python", _thin(mac.CPU_ARM64, 2))
    lib = _write(tmp_path / "lib.so", _thin(mac.CPU_X86_64, 8))
    both = _write(tmp_path / "universal", _fat((mac.CPU_X86_64, 2), (mac.CPU_ARM64, 2)))
    java = _write(tmp_path / "A.class", b"\xca\xfe\xba\xbe\x00\x00\x00\x34" + b"\0" * 16)
    elf = _write(tmp_path / "linux.so", b"\x7fELF" + b"\0" * 32)

    assert mac.macho(exe) == ({mac.CPU_ARM64}, True)
    assert mac.macho(lib) == ({mac.CPU_X86_64}, False)
    assert mac.macho(both) == ({mac.CPU_X86_64, mac.CPU_ARM64}, True)
    assert mac.macho(java) is None
    assert mac.macho(elf) is None


def _bundle(root: Path, cpu: int = mac.CPU_ARM64) -> Path:
    site = root / mac._SITE
    _write(root / "code-ai", mac.LAUNCHER)
    _write(root / "runtime" / "bin" / "python3.12", _thin(cpu, 2))
    _write(site / "code_ai" / "__main__.py", "")
    _write(site / "playwright" / "driver" / "node", _thin(cpu, 2))
    _write(site / "pydantic_core" / "_pydantic_core.cpython-312-darwin.so", _thin(cpu, 8))
    _write(site / "tiktoken" / "_tiktoken.cpython-312-darwin.so", _thin(cpu, 8))
    return root


def test_a_whole_bundle_passes_and_names_its_programs(tmp_path) -> None:
    problems, executables = mac.check_bundle(_bundle(tmp_path), ARM64)

    assert problems == []
    assert "code-ai" in executables
    assert "runtime/bin/python3.12" in executables
    assert f"{mac._SITE}/playwright/driver/node" in executables
    # Extension modules are loaded, not run.
    assert not any(name.endswith(".so") for name in executables)


def test_what_would_only_fail_on_the_mac_fails_the_build(tmp_path) -> None:
    bundle = _bundle(tmp_path)
    _write(bundle / mac._SITE / "rogue" / "_speedups.so", _thin(mac.CPU_X86_64, 8))
    _write(bundle / mac._SITE / "linux" / "_native.so", b"\x7fELF" + b"\0" * 32)
    (bundle / mac._SITE / "tiktoken" / "_tiktoken.cpython-312-darwin.so").unlink()

    problems, _ = mac.check_bundle(bundle, ARM64)

    text = "\n".join(problems)
    assert "rogue/_speedups.so is built for x86_64, not arm64" in text
    assert "linux/_native.so is a Linux library" in text
    assert "missing runtime/lib/python3.12/site-packages/tiktoken/" in text


def test_a_symlink_is_refused_because_a_vsix_cannot_carry_it(tmp_path) -> None:
    bundle = _bundle(tmp_path)
    try:
        os.symlink("python3.12", bundle / "runtime" / "bin" / "python3")
    except OSError:
        pytest.skip("this machine cannot make symlinks")

    problems, _ = mac.check_bundle(bundle, ARM64)

    assert "runtime/bin/python3 is a symlink, which a VSIX cannot carry" in problems


def test_requirements_are_read_as_on_the_mac_whatever_builds_them() -> None:
    metadata = "\n".join(
        [
            "Name: code-ai",
            "Requires-Dist: textual>=0.50.0",
            "Requires-Dist: pexpect>=4.8.0; platform_system != 'Windows'",
            "Requires-Dist: pywinpty>=2.0.12; platform_system == 'Windows'",
            'Requires-Dist: pytest>=8.0.0; extra == "dev"',
        ]
    )

    assert mac.mac_requirements(metadata, ARM64) == ["textual>=0.50.0", "pexpect>=4.8.0"]


def test_pip_is_only_ever_offered_wheels_for_that_mac(tmp_path) -> None:
    args = mac.pip_install_args(tmp_path, ARM64, ["textual"])

    assert "--only-binary=:all:" in args
    assert args[args.index("--platform") + 1] == "macosx_11_0_arm64"
    assert args[args.index("--python-version") + 1] == "3.12"
    assert args[args.index("--target") + 1] == str(tmp_path)
    assert args[-1] == "textual"


def test_the_launcher_runs_the_bundled_python_from_wherever_it_is_linked() -> None:
    assert 'python="$here/runtime/bin/python3.12"' in mac.LAUNCHER
    assert 'exec "$python" -I -m code_ai "$@"' in mac.LAUNCHER
    assert "export CODE_AI_BUNDLE" in mac.LAUNCHER
    # However it got unpacked, Gatekeeper's quarantine never reaches the Python.
    assert 'xattr -dr com.apple.quarantine "$here"' in mac.LAUNCHER
    # pwd -P: through a PATH shim, it still finds the version folder it lives in.
    assert "pwd -P" in mac.LAUNCHER
    assert "\r" not in mac.LAUNCHER


def test_the_tarball_takes_its_modes_from_the_manifest(tmp_path) -> None:
    bundle = _bundle(tmp_path / "code-ai-mac-arm64")
    out = tmp_path / "code-ai-mac-arm64-v1.2.3.tar.gz"

    mac.pack(bundle, {"code-ai", "runtime/bin/python3.12"}, out)

    with tarfile.open(out) as tar:
        members = {member.name: member for member in tar.getmembers()}
    assert members["code-ai-mac-arm64"].isdir()
    assert members["code-ai-mac-arm64/code-ai"].mode == 0o755
    assert members["code-ai-mac-arm64/runtime/bin/python3.12"].mode == 0o755
    assert members[f"code-ai-mac-arm64/{mac._SITE}/code_ai/__main__.py"].mode == 0o644
    assert {member.uid for member in members.values()} == {0}


def test_a_download_is_held_to_its_hash_and_then_reused(tmp_path) -> None:
    source = _write(tmp_path / "python.tar.gz", b"the runtime")
    digest = hashlib.sha256(b"the runtime").hexdigest()
    cache = tmp_path / "cache"

    with pytest.raises(SystemExit, match="SHA-256"):
        mac.download(source.as_uri(), "0" * 64, cache)
    assert not list(cache.glob("*"))

    first = mac.download(source.as_uri(), digest, cache)
    source.write_bytes(b"changed upstream")
    assert mac.download(source.as_uri(), digest, cache) == first
    assert first.read_bytes() == b"the runtime"


def test_a_mirror_can_stand_in_for_github(monkeypatch) -> None:
    # The workflow passes the repository variable through even when it is unset.
    monkeypatch.setenv("CODE_AI_PBS_MIRROR", "")
    assert mac.pbs_url(ARM64).startswith(mac.PBS_BASE + "/20260924/")

    monkeypatch.setenv("CODE_AI_PBS_MIRROR", "https://mirror.example/pbs/")
    url = mac.pbs_url(ARM64)
    assert url.startswith("https://mirror.example/pbs/20260924/")
    assert url.endswith("-aarch64-apple-darwin-install_only.tar.gz")


# --- The installer Mac users run ---------------------------------------------

_TEMPLATE = _ROOT / "packaging" / "mac-install.sh"


def test_the_installer_template_ends_on_its_payload_marker() -> None:
    text = _TEMPLATE.read_bytes().decode("utf-8").replace("\r\n", "\n")
    assert text.endswith("\nexit 0\n__CODE_AI_PAYLOAD__\n")
    assert "__VERSION__" in text and "__ARCH__" in text


def _installer(tmp_path: Path, version: str) -> Path:
    """What package.mjs writes: the template, then a darwin VSIX."""
    runtime = io.BytesIO()
    with tarfile.open(fileobj=runtime, mode="w:gz") as tar:
        python = b'#!/bin/sh\necho "ran $*"\n'
        info = tarfile.TarInfo("runtime/bin/python3.12")
        info.size, info.mode = len(python), 0o755
        tar.addfile(info, io.BytesIO(python))
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as vsix:
        for name, data, mode in (
            ("extension/bin/code-ai", mac.LAUNCHER, 0o755),
            ("extension/bin/runtime.tar.gz", runtime.getvalue(), 0o644),
            ("extension/package.json", "{}", 0o644),
        ):
            info = zipfile.ZipInfo(name)
            info.external_attr = (0o100000 | mode) << 16
            info.create_system = 3
            vsix.writestr(info, data)
    header = _TEMPLATE.read_bytes().decode("utf-8").replace("\r\n", "\n")
    header = header.replace("__VERSION__", version).replace("__ARCH__", "arm64")
    installer = tmp_path / f"code-ai-mac-arm64-v{version}.sh"
    installer.write_bytes(header.encode() + payload.getvalue())
    return installer


@pytest.mark.skipif(
    os.name != "posix" or not shutil.which("unzip"), reason="needs a POSIX shell and unzip"
)
def test_the_installer_installs_updates_and_keeps_the_running_version(tmp_path) -> None:
    fake = tmp_path / "fakebin"
    _write(fake / "uname", '#!/bin/sh\ncase "$1" in -s) echo Darwin ;; *) echo arm64 ;; esac\n')
    (fake / "uname").chmod(0o755)
    home = tmp_path / "home with space"
    home.mkdir()
    env = {"HOME": str(home), "SHELL": "/bin/zsh", "PATH": f"{fake}:/usr/bin:/bin"}
    app = home / ".code-ai" / "app"

    for version in ("1.0.0", "1.1.0"):
        subprocess.run(
            ["sh", str(_installer(tmp_path, version)), "--no-vscode"], env=env, check=True
        )

    assert os.readlink(app / "current") == "v1.1.0"
    # 1.0.0 was current when 1.1.0 went in, so a Code-AI may still be running it.
    assert (app / "v1.0.0").is_dir()
    assert os.access(app / "v1.1.0" / "runtime" / "bin" / "python3.12", os.X_OK)
    ran = subprocess.run(
        [str(home / ".local" / "bin" / "code-ai"), "--help"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert ran.stdout.strip() == "ran -I -m code_ai --help"
    assert (home / ".zprofile").read_text().count(".local/bin") == 1


@pytest.mark.skipif(os.name != "posix", reason="needs a POSIX shell")
def test_the_launcher_unpacks_the_runtime_on_the_first_start(tmp_path) -> None:
    bundle = tmp_path / "code-ai-mac-arm64"
    _write(bundle / "code-ai", mac.LAUNCHER).chmod(0o755)
    runtime = io.BytesIO()
    with tarfile.open(fileobj=runtime, mode="w:gz") as tar:
        python = b'#!/bin/sh\necho "ran $*"\n'
        info = tarfile.TarInfo("runtime/bin/python3.12")
        info.size, info.mode = len(python), 0o755
        tar.addfile(info, io.BytesIO(python))
    _write(bundle / "runtime.tar.gz", runtime.getvalue())

    for _ in range(2):
        ran = subprocess.run([str(bundle / "code-ai"), "x"], capture_output=True, text=True)
        assert ran.stdout.strip() == "ran -I -m code_ai x"

    assert not (bundle / "runtime.tar.gz").exists()
    assert not (bundle / ".unpacking").exists()


def _runtime_archive() -> bytes:
    """A runtime.tar.gz whose Python echoes how it was started."""
    runtime = io.BytesIO()
    with tarfile.open(fileobj=runtime, mode="w:gz") as tar:
        python = b'#!/bin/sh\necho "ran $*"\n'
        info = tarfile.TarInfo("runtime/bin/python3.12")
        info.size, info.mode = len(python), 0o755
        tar.addfile(info, io.BytesIO(python))
    return runtime.getvalue()


@pytest.mark.skipif(os.name != "posix", reason="needs a POSIX shell")
def test_a_lock_left_by_an_unpack_cut_off_is_taken_over_not_waited_on(tmp_path) -> None:
    bundle = tmp_path / "code-ai-mac-arm64"
    _write(bundle / "code-ai", mac.LAUNCHER).chmod(0o755)
    _write(bundle / "runtime.tar.gz", _runtime_archive())
    # What a Ctrl+C or a VS Code reload mid-unpack used to leave: a lock whose
    # owner is gone, which the next start waited three silent minutes on.
    _write(bundle / ".unpacking" / "pid", "999999")

    ran = subprocess.run(
        [str(bundle / "code-ai"), "x"], capture_output=True, text=True, timeout=30
    )

    assert ran.stdout.strip() == "ran -I -m code_ai x"
    assert "unpacking its Python" in ran.stderr
    assert not (bundle / ".unpacking").exists()


@pytest.mark.skipif(os.name != "posix", reason="needs a POSIX shell")
def test_a_bundle_without_its_python_says_so(tmp_path) -> None:
    bundle = tmp_path / "code-ai-mac-arm64"
    _write(bundle / "code-ai", mac.LAUNCHER).chmod(0o755)

    ran = subprocess.run([str(bundle / "code-ai"), "x"], capture_output=True, text=True, timeout=30)

    assert ran.returncode == 1
    assert "install Code-AI again" in ran.stderr
