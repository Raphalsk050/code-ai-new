from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


avatar = _load("build_desktop_avatar")


def test_an_empty_mirror_falls_back_to_the_public_hosts(monkeypatch) -> None:
    # The workflow passes the repository variables through even when they are unset.
    monkeypatch.setenv("CODE_AI_CRATES_MIRROR", "")
    monkeypatch.setenv("CODE_AI_RUST_DIST_MIRROR", "")
    assert (
        avatar.crate_url("atk", "0.18.2") == "https://static.crates.io/crates/atk/atk-0.18.2.crate"
    )
    assert avatar.rust_dist_url("rust-std") == "https://static.rust-lang.org/dist/rust-std.tar.xz"


class _System:
    """A machine with these commands and pkg-config modules, recording what is run."""

    def __init__(self, monkeypatch, commands: set[str], modules: set[str], root: bool = False):
        self.commands, self.modules, self.ran = commands, modules, []
        monkeypatch.setattr(avatar.shutil, "which", self.which)
        monkeypatch.setattr(avatar.subprocess, "run", self.run)
        monkeypatch.setattr(avatar.os, "geteuid", lambda: 0 if root else 1000, raising=False)

    def which(self, name: str, path: str | None = None) -> str | None:
        return f"/usr/bin/{name}" if name in self.commands else None

    def run(self, command: list[str], **_):
        class Done:
            returncode = 0
        if command[:2] == ["pkg-config", "--exists"]:
            Done.returncode = 0 if command[2] in self.modules else 1
        else:
            self.ran.append(command)
            if "install" in command or "-S" in command:  # the install brings everything
                self.commands |= {"pkg-config", "cc", avatar.MINGW}
                self.modules |= set(avatar.MODULES)
        return Done()


def test_nothing_is_installed_when_nothing_is_missing(monkeypatch) -> None:
    system = _System(monkeypatch, {"cc", "pkg-config", "apt-get"}, set(avatar.MODULES))
    avatar.prerequisites(["linux"])
    assert system.ran == []


def test_apt_installs_only_what_is_missing_through_sudo(monkeypatch) -> None:
    system = _System(monkeypatch, {"cc", "pkg-config", "apt-get", "sudo"}, {"gtk+-3.0", "dbus-1"})
    avatar.prerequisites(["linux", "windows"])
    assert system.ran[0] == ["sudo", "apt-get", "update"]
    assert system.ran[1][:2] == ["sudo", "env"]
    assert system.ran[1][-2:] == ["gcc-mingw-w64-x86-64", "libwebkit2gtk-4.1-dev"]


def test_without_pkg_config_every_library_counts_as_missing(monkeypatch) -> None:
    system = _System(monkeypatch, {"cc", "dnf"}, set(), root=True)
    avatar.prerequisites(["linux"])
    (command,) = system.ran
    assert command[:3] == ["dnf", "install", "-y"]
    assert "pkgconfig(webkit2gtk-4.1)" in command and "pkgconf-pkg-config" in command


def test_no_install_names_the_command_instead(monkeypatch) -> None:
    system = _System(monkeypatch, {"cc", "pkg-config", "pacman", "sudo"}, set())
    try:
        avatar.prerequisites(["linux"], install=False)
    except SystemExit as stop:
        assert "sudo pacman -S --needed --noconfirm dbus gtk3 webkit2gtk-4.1" in str(stop)
    else:
        raise AssertionError("expected SystemExit")
    assert system.ran == []


def test_the_lock_reads_without_tomllib(monkeypatch) -> None:
    lock = _ROOT / "desktop-avatar" / "Cargo.lock"
    with_tomllib = avatar.lock_packages(lock)
    monkeypatch.setattr(avatar, "tomllib", None)
    flat = avatar.lock_packages(lock)
    pick = ("name", "version", "checksum")
    assert [{k: p.get(k) for k in pick} for p in flat] == [
        {k: p.get(k) for k in pick} for p in with_tomllib
    ]


def test_a_mirror_can_stand_in_for_the_public_hosts(monkeypatch) -> None:
    monkeypatch.setenv("CODE_AI_CRATES_MIRROR", "https://mirror.example/crates/")
    monkeypatch.setenv("CODE_AI_RUST_DIST_MIRROR", "https://mirror.example/dist/")
    assert avatar.crate_url("atk", "0.18.2") == "https://mirror.example/crates/atk/atk-0.18.2.crate"
    assert avatar.rust_dist_url("rust-std") == "https://mirror.example/dist/rust-std.tar.xz"
