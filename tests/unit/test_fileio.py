from __future__ import annotations

import errno
import os
from pathlib import Path

import pytest

from code_ai.config.models import FileIOConfig
from code_ai.util import fileio
from code_ai.util.fileio import (
    NO_RETRY,
    FileOperationError,
    RetryPolicy,
    atomic_write_bytes,
    atomic_write_text,
    describe_os_error,
    is_transient_os_error,
    read_bytes,
    remove_tree,
    retry_transient,
    retry_transient_async,
)

FAST = RetryPolicy(attempts=4, initial_delay_s=0.0, max_delay_s=0.0)


def windows_error(code: int) -> OSError:
    """An OSError shaped like the one Windows raises, portable enough to test with."""

    exc = OSError(errno.EACCES, "simulated")
    exc.winerror = code
    return exc


# ------------------------------------------------------------ classification


@pytest.mark.parametrize(
    "code",
    [
        fileio.ERROR_ACCESS_DENIED,
        fileio.ERROR_SHARING_VIOLATION,
        fileio.ERROR_LOCK_VIOLATION,
        fileio.ERROR_USER_MAPPED_FILE,
        fileio.ERROR_NETNAME_DELETED,
        fileio.ERROR_NOT_READY,
    ],
)
def test_the_windows_codes_other_software_causes_are_transient(code: int) -> None:
    assert is_transient_os_error(windows_error(code)) is True


def test_a_real_windows_failure_is_not_retried() -> None:
    # ERROR_FILE_NOT_FOUND: waiting cannot make the path appear.
    assert is_transient_os_error(windows_error(2)) is False


def test_access_denied_is_transient_on_windows_but_not_on_posix() -> None:
    # The same errno means different things: an antivirus holding a handle
    # versus permissions that are genuinely wrong.
    assert is_transient_os_error(windows_error(fileio.ERROR_ACCESS_DENIED)) is True
    assert is_transient_os_error(OSError(errno.EACCES, "permission denied")) is False


@pytest.mark.parametrize("code", [errno.EAGAIN, errno.EBUSY, errno.EINTR, errno.ETXTBSY])
def test_the_posix_codes_worth_waiting_on_are_transient(code: int) -> None:
    assert is_transient_os_error(OSError(code, "busy")) is True


def test_something_that_is_not_an_os_error_is_never_transient() -> None:
    assert is_transient_os_error(ValueError("nope")) is False


def test_a_known_windows_cause_is_explained_in_words() -> None:
    described = describe_os_error(windows_error(fileio.ERROR_SHARING_VIOLATION))

    assert "another process" in described
    assert "WinError 32" in described


def test_an_unknown_windows_cause_still_names_its_code() -> None:
    assert "WinError 999" in describe_os_error(windows_error(999))


# ------------------------------------------------------------------- policy


def test_the_wait_grows_and_then_stops_growing() -> None:
    policy = RetryPolicy(attempts=6, initial_delay_s=0.05, max_delay_s=0.2)

    assert list(policy.delays()) == [0.05, 0.1, 0.2, 0.2, 0.2]


def test_there_is_one_fewer_wait_than_try() -> None:
    assert list(RetryPolicy(attempts=1).delays()) == []
    assert len(list(RetryPolicy(attempts=3).delays())) == 2


def test_the_policy_comes_from_configuration() -> None:
    policy = RetryPolicy.from_config(
        FileIOConfig(retry_attempts=3, retry_initial_delay_ms=25, retry_max_delay_ms=200)
    )

    assert policy.attempts == 3
    assert policy.initial_delay_s == 0.025
    assert policy.max_delay_s == 0.2


# -------------------------------------------------------------------- retry


def test_an_operation_that_recovers_reports_how_many_tries_it_took(tmp_path) -> None:
    calls = {"n": 0}

    def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise windows_error(fileio.ERROR_SHARING_VIOLATION)
        return "done"

    result = retry_transient(flaky, policy=FAST, what="write", path=tmp_path)

    assert result.value == "done"
    assert result.attempts == 3


def test_a_permanent_failure_is_raised_on_the_first_try(tmp_path) -> None:
    calls = {"n": 0}

    def broken() -> None:
        calls["n"] += 1
        raise FileNotFoundError(errno.ENOENT, "gone")

    with pytest.raises(FileNotFoundError):
        retry_transient(broken, policy=FAST, what="write", path=tmp_path)

    # Waiting cannot make a missing file appear, so it is not tried again.
    assert calls["n"] == 1


def test_a_lock_that_never_lets_go_becomes_a_readable_failure(tmp_path) -> None:
    def held() -> None:
        raise windows_error(fileio.ERROR_SHARING_VIOLATION)

    with pytest.raises(FileOperationError) as caught:
        retry_transient(held, policy=FAST, what="write", path=tmp_path / "a.txt")

    message = str(caught.value)
    assert "another process" in message
    assert "Tried 4 times" in message
    assert "file_io.retry_attempts" in message
    assert caught.value.attempts == 4
    assert caught.value.operation == "write"


# -------------------------------------------------------------------- write


def test_a_plain_write_creates_the_file_and_leaves_nothing_behind(tmp_path) -> None:
    outcome = atomic_write_text(tmp_path / "a.txt", "hello")

    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "hello"
    assert outcome.atomic is True
    assert outcome.bytes_written == 5
    assert [p.name for p in tmp_path.iterdir()] == ["a.txt"]


def test_a_write_creates_missing_parent_directories(tmp_path) -> None:
    atomic_write_text(tmp_path / "deep" / "nested" / "a.txt", "hi")

    assert (tmp_path / "deep" / "nested" / "a.txt").read_text(encoding="utf-8") == "hi"


def test_a_lock_that_lets_go_makes_the_write_go_through(tmp_path, monkeypatch) -> None:
    target = tmp_path / "a.txt"
    target.write_text("old", encoding="utf-8")
    real_replace = os.replace
    calls = {"n": 0}

    def guarded(source, destination, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            raise windows_error(fileio.ERROR_SHARING_VIOLATION)
        return real_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(os, "replace", guarded)

    outcome = atomic_write_text(target, "new", policy=FAST)

    assert target.read_text(encoding="utf-8") == "new"
    assert outcome.atomic is True
    assert outcome.attempts == 3
    assert [p.name for p in tmp_path.iterdir()] == ["a.txt"]


def test_a_lock_that_never_lets_go_falls_back_to_rewriting_in_place(tmp_path, monkeypatch) -> None:
    target = tmp_path / "a.txt"
    target.write_text("old content that is longer", encoding="utf-8")
    identity = target.stat().st_ino

    def always_held(*args, **kwargs):
        raise windows_error(fileio.ERROR_SHARING_VIOLATION)

    monkeypatch.setattr(os, "replace", always_held)

    outcome = atomic_write_text(target, "new", policy=FAST, allow_non_atomic_fallback=True)

    assert target.read_text(encoding="utf-8") == "new"
    # The point of the fallback: the file itself is written, so whatever the
    # encryption agent attached to it survives the edit.
    assert target.stat().st_ino == identity
    assert outcome.atomic is False
    assert [p.name for p in tmp_path.iterdir()] == ["a.txt"]


def test_the_fallback_can_be_refused(tmp_path, monkeypatch) -> None:
    target = tmp_path / "a.txt"
    target.write_text("old", encoding="utf-8")

    def always_held(*args, **kwargs):
        raise windows_error(fileio.ERROR_SHARING_VIOLATION)

    monkeypatch.setattr(os, "replace", always_held)

    with pytest.raises(FileOperationError):
        atomic_write_text(target, "new", policy=FAST, allow_non_atomic_fallback=False)

    # Refusing means the file is left exactly as it was, not half-written.
    assert target.read_text(encoding="utf-8") == "old"
    assert [p.name for p in tmp_path.iterdir()] == ["a.txt"]


def test_the_fallback_can_also_create_a_file_that_never_existed(tmp_path, monkeypatch) -> None:
    def always_held(*args, **kwargs):
        raise windows_error(fileio.ERROR_SHARING_VIOLATION)

    monkeypatch.setattr(os, "replace", always_held)

    atomic_write_text(tmp_path / "new.txt", "fresh", policy=FAST, allow_non_atomic_fallback=True)

    assert (tmp_path / "new.txt").read_text(encoding="utf-8") == "fresh"


def test_a_permanent_replace_failure_is_not_papered_over_by_the_fallback(
    tmp_path, monkeypatch
) -> None:
    target = tmp_path / "a.txt"
    target.write_text("old", encoding="utf-8")

    def broken(*args, **kwargs):
        raise OSError(errno.ENOSPC, "no space left on device")

    monkeypatch.setattr(os, "replace", broken)

    with pytest.raises(OSError) as caught:
        atomic_write_text(target, "new", policy=FAST, allow_non_atomic_fallback=True)

    assert caught.value.errno == errno.ENOSPC
    assert [p.name for p in tmp_path.iterdir()] == ["a.txt"]


def test_the_report_stays_quiet_when_nothing_went_wrong(tmp_path) -> None:
    outcome = atomic_write_bytes(tmp_path / "a.txt", b"x")

    assert outcome.to_dict() == {}


def test_the_report_says_when_it_had_to_wait_or_give_up_atomicity(tmp_path, monkeypatch) -> None:
    target = tmp_path / "a.txt"
    target.write_text("old", encoding="utf-8")
    monkeypatch.setattr(os, "replace", lambda *a, **k: (_ for _ in ()).throw(windows_error(32)))

    outcome = atomic_write_text(target, "new", policy=FAST, allow_non_atomic_fallback=True)
    report = outcome.to_dict()

    assert report["atomic"] is False
    # Four blocked swaps plus the one rewrite that worked.
    assert report["write_attempts"] == 5
    assert "write_waited_s" in report


# --------------------------------------------------------------------- read


def test_a_read_waits_out_whatever_is_holding_the_file(tmp_path, monkeypatch) -> None:
    target = tmp_path / "a.txt"
    target.write_text("payload", encoding="utf-8")
    real = Path.read_bytes
    calls = {"n": 0}

    def guarded(self):
        calls["n"] += 1
        if calls["n"] < 2:
            raise windows_error(fileio.ERROR_SHARING_VIOLATION)
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", guarded)

    assert read_bytes(target, policy=FAST) == b"payload"


def test_a_read_of_something_that_is_not_there_fails_immediately(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        read_bytes(tmp_path / "missing.txt", policy=NO_RETRY)


# ------------------------------------------------------------- async retry


async def test_the_async_driver_waits_out_the_same_failures(tmp_path) -> None:
    calls = {"n": 0}

    async def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise windows_error(fileio.ERROR_SHARING_VIOLATION)
        return "started"

    result = await retry_transient_async(flaky, policy=FAST, what="start", path=tmp_path)

    assert result.value == "started"
    assert result.attempts == 3


async def test_the_async_driver_does_not_retry_a_real_failure(tmp_path) -> None:
    calls = {"n": 0}

    async def missing() -> None:
        calls["n"] += 1
        raise FileNotFoundError(errno.ENOENT, "no such program")

    with pytest.raises(FileNotFoundError):
        await retry_transient_async(missing, policy=FAST, what="start", path=tmp_path)

    assert calls["n"] == 1


async def test_the_async_driver_gives_the_same_readable_failure(tmp_path) -> None:
    async def held() -> None:
        raise windows_error(fileio.ERROR_SHARING_VIOLATION)

    with pytest.raises(FileOperationError) as caught:
        await retry_transient_async(held, policy=FAST, what="start", path=tmp_path / "app.exe")

    assert "another process" in str(caught.value)


# ------------------------------------------------------------ tree removal


def test_removing_a_tree_that_is_free_just_works(tmp_path) -> None:
    tree = tmp_path / "sandbox"
    (tree / "work").mkdir(parents=True)
    (tree / "work" / "out.bin").write_text("x", encoding="utf-8")

    assert remove_tree(tree, policy=FAST) is True
    assert not tree.exists()


def test_removing_a_tree_that_is_not_there_is_not_a_failure(tmp_path) -> None:
    assert remove_tree(tmp_path / "never-existed") is True


def test_a_read_only_file_does_not_stop_the_removal(tmp_path) -> None:
    tree = tmp_path / "sandbox"
    tree.mkdir()
    locked = tree / "build.lock"
    locked.write_text("x", encoding="utf-8")
    locked.chmod(0o444)

    assert remove_tree(tree, policy=FAST) is True


def test_a_tree_that_will_not_go_is_reported_rather_than_raised(tmp_path, monkeypatch) -> None:
    tree = tmp_path / "sandbox"
    tree.mkdir()

    def always_held(*args, **kwargs):
        raise windows_error(fileio.ERROR_SHARING_VIOLATION)

    monkeypatch.setattr(fileio.shutil, "rmtree", always_held)

    # Cleanup runs at shutdown; a dev server still holding a build directory
    # must not turn into an exception on the way out.
    assert remove_tree(tree, policy=FAST) is False
    assert tree.exists()
