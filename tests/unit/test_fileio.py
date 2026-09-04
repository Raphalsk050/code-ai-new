from __future__ import annotations

import errno
import os
import time
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
    in_place_rewrite_could_help,
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


@pytest.fixture(autouse=True)
def force_the_rename_path(monkeypatch):
    """Make the simulated locks bite on a real Windows host too.

    The tests here hold a file by patching ``os.replace``. On Windows the write
    prefers ``ReplaceFileW``, which the patch never reaches, so the lock would
    be silently ignored on the one platform these tests are about. Turning the
    fast path off keeps both hosts running the same code.
    """

    monkeypatch.setattr(fileio, "_replace_file_win", lambda source, target: False)


def test_a_real_windows_failure_is_not_retried() -> None:
    # ERROR_FILE_NOT_FOUND: waiting cannot make the path appear.
    assert is_transient_os_error(windows_error(2)) is False


def test_access_denied_is_transient_on_windows_but_not_on_posix(monkeypatch) -> None:
    # The same errno means different things: an antivirus holding a handle
    # versus permissions that are genuinely wrong.
    assert is_transient_os_error(windows_error(fileio.ERROR_ACCESS_DENIED)) is True

    # A bare EACCES with no Windows code is what ``open()`` raises, because it
    # goes through the C runtime: on Windows that is a lock, on POSIX it is the
    # permissions really being wrong.
    bare = OSError(errno.EACCES, "permission denied")
    monkeypatch.setattr(os, "name", "posix")
    assert is_transient_os_error(bare) is False
    monkeypatch.setattr(os, "name", "nt")
    assert is_transient_os_error(bare) is True


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


def test_a_lock_on_the_staging_file_still_gets_the_write_through(tmp_path, monkeypatch) -> None:
    # A scanner watching the directory can hold the temporary file the moment it
    # appears, which blocks the write before there is anything to swap in.
    target = tmp_path / "a.txt"
    target.write_text("old", encoding="utf-8")

    def always_held(*args, **kwargs):
        raise windows_error(fileio.ERROR_SHARING_VIOLATION)

    monkeypatch.setattr(fileio.tempfile, "mkstemp", always_held)

    outcome = atomic_write_text(target, "new", policy=FAST, allow_non_atomic_fallback=True)

    assert target.read_text(encoding="utf-8") == "new"
    assert outcome.atomic is False
    assert [p.name for p in tmp_path.iterdir()] == ["a.txt"]


def test_a_lock_on_the_staging_file_is_reported_when_the_fallback_is_refused(
    tmp_path, monkeypatch
) -> None:
    target = tmp_path / "a.txt"
    target.write_text("old", encoding="utf-8")

    def always_held(*args, **kwargs):
        raise windows_error(fileio.ERROR_SHARING_VIOLATION)

    monkeypatch.setattr(fileio.tempfile, "mkstemp", always_held)

    with pytest.raises(FileOperationError):
        atomic_write_text(target, "new", policy=FAST, allow_non_atomic_fallback=False)

    assert target.read_text(encoding="utf-8") == "old"


def test_a_lock_with_no_windows_code_is_still_ridden_out(tmp_path, monkeypatch) -> None:
    """The shape ``open()`` raises on Windows: an errno, and no winerror at all.

    The in-place fallback goes through the C runtime, so the lock that sends it
    there arrives without the Windows code the classifier normally reads. Left
    unrecognised it was raised raw, past the retry and past every caller that
    knows what a :class:`FileOperationError` means.
    """

    target = tmp_path / "a.txt"
    target.write_text("old", encoding="utf-8")
    monkeypatch.setattr(os, "name", "nt")

    def always_held(*args, **kwargs):
        raise windows_error(fileio.ERROR_SHARING_VIOLATION)

    monkeypatch.setattr(os, "replace", always_held)

    real_rewrite = fileio._rewrite_in_place
    calls = {"n": 0}

    def guarded(path, data):
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError(errno.EACCES, "Permission denied")
        return real_rewrite(path, data)

    monkeypatch.setattr(fileio, "_rewrite_in_place", guarded)

    outcome = atomic_write_text(target, "new", policy=FAST, allow_non_atomic_fallback=True)

    assert target.read_text(encoding="utf-8") == "new"
    assert outcome.atomic is False
    assert calls["n"] == 3


def test_the_fallback_can_also_create_a_file_that_never_existed(tmp_path, monkeypatch) -> None:
    def always_held(*args, **kwargs):
        raise windows_error(fileio.ERROR_SHARING_VIOLATION)

    monkeypatch.setattr(os, "replace", always_held)

    atomic_write_text(tmp_path / "new.txt", "fresh", policy=FAST, allow_non_atomic_fallback=True)

    assert (tmp_path / "new.txt").read_text(encoding="utf-8") == "fresh"


def test_an_unrecognised_code_still_reaches_the_fallback(tmp_path, monkeypatch) -> None:
    """The failure that motivated all of this: a code no table here predicted.

    The transient tables are a guess about what someone else's antivirus, sync
    client or encryption driver raises. Being wrong about a code used to cost
    the write entirely - the raw OSError went straight past the ``except`` that
    reaches for the fallback - which is how a host ended up with a staged
    temporary file and no file.
    """

    target = tmp_path / "a.txt"
    target.write_text("old", encoding="utf-8")

    def refused(*args, **kwargs):
        # ERROR_FILE_ENCRYPTED, one of many codes not in TRANSIENT_WINDOWS_ERRORS.
        raise windows_error(6002)

    monkeypatch.setattr(fileio.tempfile, "mkstemp", refused)

    outcome = atomic_write_text(target, "new", policy=FAST, allow_non_atomic_fallback=True)

    assert target.read_text(encoding="utf-8") == "new"
    assert outcome.atomic is False


def test_an_unrecognised_code_on_the_swap_reaches_the_fallback_too(tmp_path, monkeypatch) -> None:
    target = tmp_path / "a.txt"
    target.write_text("old", encoding="utf-8")

    def refused(*args, **kwargs):
        raise windows_error(6002)

    monkeypatch.setattr(os, "replace", refused)

    outcome = atomic_write_text(target, "new", policy=FAST, allow_non_atomic_fallback=True)

    assert target.read_text(encoding="utf-8") == "new"
    assert outcome.atomic is False
    assert [p.name for p in tmp_path.iterdir()] == ["a.txt"]


def test_a_write_blocked_by_an_unrecognised_code_still_fails_when_refused(
    tmp_path, monkeypatch
) -> None:
    target = tmp_path / "a.txt"
    target.write_text("old", encoding="utf-8")

    def refused(*args, **kwargs):
        raise windows_error(6002)

    monkeypatch.setattr(fileio.tempfile, "mkstemp", refused)

    with pytest.raises(OSError):
        atomic_write_text(target, "new", policy=FAST, allow_non_atomic_fallback=False)

    assert target.read_text(encoding="utf-8") == "old"


def test_the_error_that_blocked_the_write_survives_a_failed_fallback(tmp_path, monkeypatch) -> None:
    """Two failures, and the first one is the one that explains the machine."""

    target = tmp_path / "a.txt"
    target.write_text("old", encoding="utf-8")

    def refused(*args, **kwargs):
        raise windows_error(fileio.ERROR_SHARING_VIOLATION)

    monkeypatch.setattr(fileio.tempfile, "mkstemp", refused)
    monkeypatch.setattr(
        fileio,
        "_rewrite_in_place",
        lambda path, data: (_ for _ in ()).throw(OSError(errno.EIO, "the drive gave up")),
    )

    with pytest.raises(OSError) as caught:
        atomic_write_text(target, "new", policy=FAST, allow_non_atomic_fallback=True)

    assert caught.value.__cause__ is not None
    assert "WinError 32" in describe_os_error(caught.value.__cause__.__cause__)


@pytest.mark.parametrize("code", sorted(fileio.UNRECOVERABLE_ERRNOS))
def test_a_filesystem_that_cannot_take_the_write_is_not_worth_a_second_approach(
    code: int,
) -> None:
    assert in_place_rewrite_could_help(OSError(code, "simulated")) is False


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


def test_a_blocked_write_stages_one_file_not_one_per_retry(tmp_path, monkeypatch) -> None:
    """Every retry used to take a fresh mkstemp, and every one was left behind.

    The thing that blocks staging is the thing that blocks removing what was
    staged, so a write that retried six times could abandon six files in the
    user's project directory - and nothing ever collected them.
    """

    target = tmp_path / "a.txt"
    target.write_text("old", encoding="utf-8")
    created: list[str] = []
    real_mkstemp = fileio.tempfile.mkstemp

    def counted(*args, **kwargs):
        handle_id, name = real_mkstemp(*args, **kwargs)
        created.append(name)
        return handle_id, name

    monkeypatch.setattr(fileio.tempfile, "mkstemp", counted)
    monkeypatch.setattr(os, "replace", lambda *a, **k: (_ for _ in ()).throw(windows_error(32)))
    # Held hard enough that the cleanup cannot remove it either.
    monkeypatch.setattr(os, "unlink", lambda *a, **k: (_ for _ in ()).throw(windows_error(32)))

    atomic_write_text(target, "new", policy=FAST, allow_non_atomic_fallback=True)

    assert len(created) == 1


def test_the_cleanup_waits_out_a_lock_instead_of_giving_up_at_once(
    tmp_path, monkeypatch
) -> None:
    target = tmp_path / "a.txt"
    target.write_text("old", encoding="utf-8")
    real_unlink = os.unlink
    calls = {"n": 0}

    def held(path, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            raise windows_error(fileio.ERROR_SHARING_VIOLATION)
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "replace", lambda *a, **k: (_ for _ in ()).throw(windows_error(32)))
    monkeypatch.setattr(os, "unlink", held)

    atomic_write_text(target, "new", policy=FAST, allow_non_atomic_fallback=True)

    assert target.read_text(encoding="utf-8") == "new"
    # The staged file goes once the lock lets go, rather than staying forever.
    assert [p.name for p in tmp_path.iterdir()] == ["a.txt"]


def test_a_staged_file_left_by_an_earlier_write_is_collected_later(tmp_path) -> None:
    abandoned = tmp_path / f".a.txt.{fileio._STAGING_MARKER}old.tmp"
    abandoned.write_text("litter", encoding="utf-8")
    stale = time.time() - fileio._STALE_STAGING_AGE_S - 60
    os.utime(abandoned, (stale, stale))

    atomic_write_text(tmp_path / "a.txt", "new")

    assert not abandoned.exists()


def test_the_sweep_leaves_alone_what_it_did_not_write(tmp_path) -> None:
    stale = time.time() - fileio._STALE_STAGING_AGE_S - 60
    mine_but_fresh = tmp_path / f".a.txt.{fileio._STAGING_MARKER}fresh.tmp"
    someone_elses = tmp_path / ".vim.swp.tmp"
    not_hidden = tmp_path / f"a.txt.{fileio._STAGING_MARKER}visible.tmp"
    for path in (mine_but_fresh, someone_elses, not_hidden):
        path.write_text("keep", encoding="utf-8")
    for path in (someone_elses, not_hidden):
        os.utime(path, (stale, stale))

    atomic_write_text(tmp_path / "a.txt", "new")

    assert mine_but_fresh.exists()  # ours, but a write could still be using it
    assert someone_elses.exists()  # not ours to delete
    assert not_hidden.exists()  # not the name we stage under


def test_on_windows_an_existing_file_is_written_where_it_stands(tmp_path, monkeypatch) -> None:
    """The change that matches what every other editor on the platform does.

    Staging a replacement is the pair of operations a filter driver intercepts:
    a new file in a watched directory, then a plaintext file swapped over an
    encrypted one. Writing into the existing file avoids both.
    """

    target = tmp_path / "a.txt"
    target.write_text("old", encoding="utf-8")
    identity = target.stat().st_ino
    staged: list[object] = []
    monkeypatch.setattr(fileio, "_on_windows", lambda: True)
    monkeypatch.setattr(fileio.tempfile, "mkstemp", lambda *a, **k: staged.append(1))

    atomic_write_text(target, "new", policy=FAST)

    assert target.read_text(encoding="utf-8") == "new"
    assert target.stat().st_ino == identity  # same file, not a replacement
    assert staged == []  # nothing was ever staged


def test_writing_in_place_by_choice_is_not_reported_as_a_degraded_write(
    tmp_path, monkeypatch
) -> None:
    target = tmp_path / "a.txt"
    target.write_text("old", encoding="utf-8")
    monkeypatch.setattr(fileio, "_on_windows", lambda: True)

    outcome = atomic_write_text(target, "new", policy=FAST)

    assert outcome.atomic is False
    # Not flagged: the report exists to surface interference, and flagging
    # every Windows write would teach the reader to ignore it.
    assert outcome.to_dict() == {}


def test_a_file_that_does_not_exist_yet_is_still_staged_and_swapped(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(fileio, "_on_windows", lambda: True)

    outcome = atomic_write_text(tmp_path / "new.txt", "fresh", policy=FAST)

    assert (tmp_path / "new.txt").read_text(encoding="utf-8") == "fresh"
    assert outcome.atomic is True


def test_a_blocked_in_place_write_still_falls_through_to_the_swap(tmp_path, monkeypatch) -> None:
    target = tmp_path / "a.txt"
    target.write_text("old", encoding="utf-8")
    monkeypatch.setattr(fileio, "_on_windows", lambda: True)
    monkeypatch.setattr(
        fileio,
        "_rewrite_in_place",
        lambda path, data: (_ for _ in ()).throw(windows_error(fileio.ERROR_SHARING_VIOLATION)),
    )

    outcome = atomic_write_text(target, "new", policy=FAST)

    assert target.read_text(encoding="utf-8") == "new"
    assert outcome.atomic is True  # the staged swap picked it up


def test_posix_keeps_the_staged_write_by_default(tmp_path, monkeypatch) -> None:
    target = tmp_path / "a.txt"
    target.write_text("old", encoding="utf-8")
    monkeypatch.setattr(fileio, "_on_windows", lambda: False)

    assert atomic_write_text(target, "new", policy=FAST).atomic is True


def test_windows_prefers_the_in_place_write_by_default(tmp_path, monkeypatch) -> None:
    target = tmp_path / "a.txt"
    target.write_text("old", encoding="utf-8")
    monkeypatch.setattr(fileio, "_on_windows", lambda: True)

    assert atomic_write_text(target, "new", policy=FAST).atomic is False


def test_the_flag_turns_the_windows_behaviour_off_but_never_on(tmp_path, monkeypatch) -> None:
    """It is an opt-out, matching its name: POSIX keeps the atomic swap."""

    target = tmp_path / "a.txt"
    target.write_text("old", encoding="utf-8")

    monkeypatch.setattr(fileio, "_on_windows", lambda: False)
    assert atomic_write_text(target, "new", policy=FAST, in_place_first=True).atomic is True

    monkeypatch.setattr(fileio, "_on_windows", lambda: True)
    assert atomic_write_text(target, "newer", policy=FAST, in_place_first=False).atomic is True


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
