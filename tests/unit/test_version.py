"""The version file is the source of truth, and X never moves by accident."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

import code_ai
from code_ai.version import VERSION_FILE, VersionInfo, bump, read_version, write_version

_REPO = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO / "scripts" / "bump_version.py"


@pytest.fixture
def version_file(tmp_path) -> Path:
    path = tmp_path / "version.json"
    write_version(VersionInfo(0, 9, 0, 1, updated="2026-09-18", note="baseline"), path)
    return path


def test_the_shipped_file_parses_and_reads_as_a_version() -> None:
    info = read_version()

    assert info.full == f"{info.release}-{info.build}"
    assert info.release == code_ai.__version__
    assert code_ai.full_version() == info.full


def test_a_patch_bump_moves_z_and_the_build(version_file) -> None:
    info = bump("patch", "fix the thing", path=version_file, today=date(2026, 9, 19))

    assert info.full == "0.9.1-2"
    assert info.updated == "2026-09-19"
    assert info.note == "fix the thing"


def test_a_minor_bump_moves_y_and_resets_z(version_file) -> None:
    bump("patch", "one", path=version_file)
    info = bump("minor", "a new capability", path=version_file)

    assert info.full == "0.10.0-3"


def test_a_major_bump_needs_to_be_asked_for(version_file) -> None:
    with pytest.raises(ValueError, match="only moves when the user asks"):
        bump("major", "big", path=version_file)

    assert read_version(version_file).full == "0.9.0-1"


def test_an_allowed_major_bump_resets_y_and_z_but_not_the_build(version_file) -> None:
    info = bump("major", "1.0", path=version_file, allow_major=True)

    assert info.full == "1.0.0-2"


def test_the_build_counter_never_goes_backwards(version_file) -> None:
    builds = [
        bump(kind, "note", path=version_file).build
        for kind in ("patch", "patch", "minor", "patch")
    ]

    assert builds == [2, 3, 4, 5]


def test_a_bump_without_a_note_is_refused(version_file) -> None:
    with pytest.raises(ValueError, match="one-line note"):
        bump("patch", "   ", path=version_file)


def test_an_unknown_kind_is_refused(version_file) -> None:
    with pytest.raises(ValueError, match="Unknown bump kind"):
        bump("build", "note", path=version_file)


def test_a_missing_file_reads_as_zero_instead_of_crashing(tmp_path) -> None:
    assert read_version(tmp_path / "nope.json").full == "0.0.0-0"


def test_a_corrupt_file_is_reported_rather_than_guessed(tmp_path) -> None:
    path = tmp_path / "version.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(ValueError, match="not valid JSON"):
        read_version(path)


def test_a_negative_number_is_refused(tmp_path) -> None:
    path = tmp_path / "version.json"
    path.write_text(json.dumps({"major": 0, "minor": -1, "patch": 0, "build": 1}), encoding="utf-8")

    with pytest.raises(ValueError, match="cannot be negative"):
        read_version(path)


def test_the_file_round_trips(version_file) -> None:
    info = read_version(version_file)
    write_version(info, version_file)

    assert read_version(version_file) == info


def test_the_script_shows_the_current_version() -> None:
    result = subprocess.run(
        [sys.executable, str(_SCRIPT), "--show"],
        capture_output=True,
        text=True,
        check=True,
    )

    assert f"v{read_version().full}" in result.stdout


def test_the_script_refuses_a_major_bump_without_the_flag() -> None:
    before = VERSION_FILE.read_text(encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(_SCRIPT), "major", "-m", "nope"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert VERSION_FILE.read_text(encoding="utf-8") == before


def test_the_script_refuses_a_bump_with_no_note() -> None:
    before = VERSION_FILE.read_text(encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(_SCRIPT), "patch"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert VERSION_FILE.read_text(encoding="utf-8") == before
