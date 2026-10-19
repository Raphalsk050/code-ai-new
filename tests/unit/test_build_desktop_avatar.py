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


def test_a_mirror_can_stand_in_for_the_public_hosts(monkeypatch) -> None:
    monkeypatch.setenv("CODE_AI_CRATES_MIRROR", "https://mirror.example/crates/")
    monkeypatch.setenv("CODE_AI_RUST_DIST_MIRROR", "https://mirror.example/dist/")
    assert avatar.crate_url("atk", "0.18.2") == "https://mirror.example/crates/atk/atk-0.18.2.crate"
    assert avatar.rust_dist_url("rust-std") == "https://mirror.example/dist/rust-std.tar.xz"
