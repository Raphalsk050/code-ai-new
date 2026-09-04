from __future__ import annotations

import json
import os
import platform
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from code_ai.sandbox import reaper as reaper_module
from code_ai.sandbox.layout import MARKER_FILENAME
from code_ai.sandbox.reaper import SandboxReaper, _process_is_alive
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
    sandbox = make_sandbox(tmp_path, "orphan")
    # A pid that cannot be running: process ids start at 1.
    age(sandbox, hours=0, pid=2**30)

    removed = reaper(tmp_path).sweep()

    assert removed == [sandbox.root]


class FakeKernel32:
    """Just enough of kernel32 to drive the Windows liveness probe on any host."""

    def __init__(self, *, handle: int, exit_code: int, last_error: int = 0) -> None:
        self.handle = handle
        self.exit_code = exit_code
        self.last_error = last_error
        self.closed: list[int] = []

    def OpenProcess(self, access, inherit, pid):  # noqa: N802 - the Win32 name
        self.opened = (access, inherit, pid)
        return self.handle

    def GetLastError(self):  # noqa: N802
        return self.last_error

    def GetExitCodeProcess(self, handle, out):  # noqa: N802
        out._obj.value = self.exit_code
        return 1

    def CloseHandle(self, handle):  # noqa: N802
        self.closed.append(handle)
        return 1


def use_fake_windows(monkeypatch, kernel32: FakeKernel32) -> None:
    monkeypatch.setattr(reaper_module.os, "name", "nt")
    monkeypatch.setattr(
        reaper_module.ctypes, "windll", SimpleNamespace(kernel32=kernel32), raising=False
    )


def test_the_windows_probe_never_kills_the_process_it_asks_about(monkeypatch) -> None:
    """os.kill(pid, 0) is not a liveness check on Windows - it terminates.

    CPython implements os.kill there with TerminateProcess, so the POSIX idiom
    would reap a sandbox by killing the session that owns it. The probe has to
    go through OpenProcess instead, and this asserts it never reaches os.kill.
    """

    def forbidden(*args, **kwargs):
        raise AssertionError("os.kill must never be called on Windows")

    monkeypatch.setattr(reaper_module.os, "kill", forbidden)
    kernel32 = FakeKernel32(handle=1234, exit_code=259)  # STILL_ACTIVE
    use_fake_windows(monkeypatch, kernel32)

    assert _process_is_alive(4321, platform.node()) is True
    assert kernel32.closed == [1234]  # and the handle is not leaked


def test_a_finished_process_reads_as_gone_on_windows(monkeypatch) -> None:
    use_fake_windows(monkeypatch, FakeKernel32(handle=1234, exit_code=0))

    assert _process_is_alive(4321, platform.node()) is False


def test_a_pid_windows_does_not_know_reads_as_gone(monkeypatch) -> None:
    # OpenProcess fails with ERROR_INVALID_PARAMETER for a pid that is not there.
    use_fake_windows(monkeypatch, FakeKernel32(handle=0, exit_code=0, last_error=87))

    assert _process_is_alive(4321, platform.node()) is False


def test_a_process_windows_will_not_talk_about_is_left_alone(monkeypatch) -> None:
    # ERROR_ACCESS_DENIED: it exists, it is just not ours. Unknown, not dead -
    # deleting on this answer would reap a live session's sandbox.
    use_fake_windows(monkeypatch, FakeKernel32(handle=0, exit_code=0, last_error=5))

    assert _process_is_alive(4321, platform.node()) is None


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
