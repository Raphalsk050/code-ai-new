from __future__ import annotations

import json
import os
import platform
from datetime import UTC, datetime, timedelta
from pathlib import Path

from code_ai.sandbox.layout import MARKER_FILENAME
from code_ai.sandbox.reaper import SandboxReaper
from code_ai.sandbox.session import SessionSandbox


def make_sandbox(tmp_path, session_id: str) -> SessionSandbox:
    workspace = tmp_path / "project"
    workspace.mkdir(exist_ok=True)
    return SessionSandbox.create(
        session_id=session_id,
        workspace=workspace,
        base_dir=tmp_path / "sandboxes",
    )


def age(sandbox: SessionSandbox, *, hours: float, pid: int | None = None) -> None:
    marker = sandbox.root / MARKER_FILENAME
    payload = json.loads(marker.read_text(encoding="utf-8"))
    payload["created_at"] = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
    payload["pid"] = os.getpid() if pid is None else pid
    marker.write_text(json.dumps(payload), encoding="utf-8")


def reaper(tmp_path, *, hours: int = 24) -> SandboxReaper:
    return SandboxReaper(base=tmp_path / "sandboxes", ttl=timedelta(hours=hours))


def test_a_fresh_sandbox_survives(tmp_path) -> None:
    sandbox = make_sandbox(tmp_path, "fresh")

    assert reaper(tmp_path).sweep() == []
    assert sandbox.root.is_dir()


def test_a_sandbox_past_its_ttl_is_removed(tmp_path) -> None:
    sandbox = make_sandbox(tmp_path, "stale")
    age(sandbox, hours=48)

    removed = reaper(tmp_path).sweep()

    assert removed == [sandbox.root]
    assert not sandbox.root.exists()


def test_the_live_session_is_never_reaped(tmp_path) -> None:
    live = make_sandbox(tmp_path, "live")
    age(live, hours=999)

    assert reaper(tmp_path).sweep(keep=live.root) == []
    assert live.root.is_dir()


def test_a_sandbox_whose_owner_died_is_removed_before_its_ttl(tmp_path) -> None:
    if os.name != "posix":
        return
    sandbox = make_sandbox(tmp_path, "orphan")
    # A pid that cannot be running: process ids start at 1.
    age(sandbox, hours=0, pid=2**30)

    removed = reaper(tmp_path).sweep()

    assert removed == [sandbox.root]


def test_a_sandbox_owned_by_another_machine_waits_for_its_ttl(tmp_path) -> None:
    sandbox = make_sandbox(tmp_path, "remote")
    marker = sandbox.root / MARKER_FILENAME
    payload = json.loads(marker.read_text(encoding="utf-8"))
    payload["hostname"] = "some-other-host"
    payload["pid"] = 2**30
    marker.write_text(json.dumps(payload), encoding="utf-8")

    assert reaper(tmp_path).sweep() == []
    assert sandbox.root.is_dir()


def test_a_marker_without_a_usable_timestamp_falls_back_to_the_directory_age(tmp_path) -> None:
    sandbox = make_sandbox(tmp_path, "undated")
    marker = sandbox.root / MARKER_FILENAME
    payload = json.loads(marker.read_text(encoding="utf-8"))
    payload["created_at"] = "not-a-date"
    payload["pid"] = os.getpid()
    payload["hostname"] = platform.node()
    marker.write_text(json.dumps(payload), encoding="utf-8")
    old = (datetime.now(UTC) - timedelta(days=3)).timestamp()
    os.utime(sandbox.root, (old, old))

    assert reaper(tmp_path).sweep() == [sandbox.root]


def test_a_directory_that_is_not_ours_is_left_alone(tmp_path) -> None:
    base = tmp_path / "sandboxes"
    base.mkdir(parents=True, exist_ok=True)
    stranger = base / "someone-elses-data"
    stranger.mkdir()
    (stranger / "precious.txt").write_text("data", encoding="utf-8")
    unmarked_file = base / "notes.txt"
    unmarked_file.write_text("data", encoding="utf-8")

    assert reaper(tmp_path).sweep() == []
    assert (stranger / "precious.txt").exists()
    assert unmarked_file.exists()


def test_a_missing_base_directory_is_not_an_error(tmp_path) -> None:
    assert SandboxReaper(base=tmp_path / "nope", ttl=timedelta(hours=1)).sweep() == []


def test_one_broken_entry_does_not_abort_the_sweep(tmp_path) -> None:
    stale = make_sandbox(tmp_path, "stale")
    age(stale, hours=48)
    broken = Path(tmp_path / "sandboxes" / "broken")
    broken.mkdir()
    (broken / MARKER_FILENAME).write_text("{not json", encoding="utf-8")

    removed = reaper(tmp_path).sweep()

    assert removed == [stale.root]
    assert broken.is_dir()


def test_the_sweep_never_reaches_the_project_through_the_link(tmp_path) -> None:
    (tmp_path / "project").mkdir(exist_ok=True)
    (tmp_path / "project" / "keep.py").write_text("keep\n", encoding="utf-8")
    sandbox = make_sandbox(tmp_path, "stale")
    age(sandbox, hours=48)

    reaper(tmp_path).sweep()

    assert (tmp_path / "project" / "keep.py").read_text(encoding="utf-8") == "keep\n"
