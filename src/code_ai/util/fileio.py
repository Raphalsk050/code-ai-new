"""File operations that survive a Windows host with other software in the way.

On POSIX a file is a name and a inode, and another process reading it never
stops you from replacing it. Windows works the other way round: a handle can
deny sharing, so an antivirus scanning a file, a search indexer reading it, a
sync client uploading it, or a disk-encryption agent rewriting it will make an
ordinary write fail - for a few hundred milliseconds, with an error that reads
like a permanent one.

Every failure of that kind is timing, not permission, so the answer is to wait
and try again, and to say so plainly when the wait was not enough. Three things
here do that work:

- :func:`is_transient_os_error` decides which failures are worth retrying, using
  the Windows error code when there is one and the POSIX errno otherwise. The
  two tables are deliberately different: ``ERROR_ACCESS_DENIED`` on Windows is
  usually an antivirus holding the file, while ``EACCES`` on POSIX really does
  mean the permissions are wrong.
- :func:`atomic_write_bytes` writes through a temporary file and swaps it in,
  preferring the Windows ``ReplaceFileW`` call because - unlike a plain rename -
  it preserves the destination's attributes, ACLs and alternate data streams.
  That is what keeps an encrypted file encrypted after the agent edits it.
- The non-atomic fallback rewrites the file in place when a lock outlives every
  retry. It gives up atomicity, and says so in its result, but it goes through
  when the other process holds the file open without denying writes.

Everything here is synchronous and blocking on purpose: callers on the event
loop hand it to a worker thread, which keeps a slow network drive from freezing
the UI as well.
"""

from __future__ import annotations

import asyncio
import ctypes
import errno
import os
import shutil
import stat
import tempfile
import time
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Generic, TypeVar

from code_ai.core.errors import ToolExecutionError

T = TypeVar("T")

# Windows error codes that mean "someone else is holding this right now".
# ERROR_ACCESS_DENIED is on the list because on Windows it is what a scanner
# holding an open handle produces, not only a genuine permission problem.
ERROR_ACCESS_DENIED = 5
ERROR_NOT_READY = 21
ERROR_SHARING_VIOLATION = 32
ERROR_LOCK_VIOLATION = 33
ERROR_NETNAME_DELETED = 64
ERROR_USER_MAPPED_FILE = 1224

TRANSIENT_WINDOWS_ERRORS = frozenset(
    {
        ERROR_ACCESS_DENIED,
        ERROR_NOT_READY,
        ERROR_SHARING_VIOLATION,
        ERROR_LOCK_VIOLATION,
        ERROR_NETNAME_DELETED,
        ERROR_USER_MAPPED_FILE,
    }
)

# POSIX is far more permissive about concurrent access, so this table is short
# and excludes EACCES: there, a permission error is a real one.
TRANSIENT_POSIX_ERRNOS = frozenset({errno.EAGAIN, errno.EBUSY, errno.EINTR, errno.ETXTBSY})

# Not every Windows failure carries a Windows error code. ``open()``, ``os.open``
# and everything built on them go through the C runtime, which sets errno only -
# a file another process holds arrives as a bare ``EACCES`` with no winerror. On
# Windows that is a lock, so it belongs here even though the POSIX table leaves
# EACCES out on purpose.
TRANSIENT_WINDOWS_ERRNOS = frozenset({errno.EACCES, *TRANSIENT_POSIX_ERRNOS})

_WINDOWS_ERROR_NAMES = {
    ERROR_ACCESS_DENIED: "access denied, usually an antivirus or encryption agent holding it",
    ERROR_NOT_READY: "the device is not ready",
    ERROR_SHARING_VIOLATION: "the file is open in another process",
    ERROR_LOCK_VIOLATION: "another process holds a lock on the file",
    ERROR_NETNAME_DELETED: "the network connection to the file dropped",
    ERROR_USER_MAPPED_FILE: "another process has the file mapped into memory",
}


class FileOperationError(ToolExecutionError):
    """A file operation failed and retrying did not help.

    Carries what was attempted and for how long, because on a machine with an
    encryption or DLP agent the difference between "the path is wrong" and
    "something else had the file for two seconds" is the whole diagnosis.
    """

    def __init__(
        self,
        message: str,
        *,
        path: Path,
        operation: str,
        attempts: int,
        waited_s: float,
    ) -> None:
        super().__init__(message)
        self.path = path
        self.operation = operation
        self.attempts = attempts
        self.waited_s = waited_s


def is_transient_os_error(exc: BaseException) -> bool:
    """Whether ``exc`` is the filesystem saying "not right now" rather than "no"."""

    if not isinstance(exc, OSError):
        return False
    winerror = getattr(exc, "winerror", None)
    if winerror is not None:
        return int(winerror) in TRANSIENT_WINDOWS_ERRORS
    if os.name == "nt":
        return exc.errno in TRANSIENT_WINDOWS_ERRNOS
    return exc.errno in TRANSIENT_POSIX_ERRNOS


def describe_os_error(exc: OSError) -> str:
    """Plain-language cause of ``exc``, naming the Windows code when there is one."""

    winerror = getattr(exc, "winerror", None)
    if winerror is None:
        if os.name == "nt" and exc.errno == errno.EACCES:
            return "access denied, usually another program holding the file open"
        return str(exc)
    known = _WINDOWS_ERROR_NAMES.get(int(winerror))
    if known:
        return f"{known} (WinError {winerror})"
    return f"{exc.strerror or exc} (WinError {winerror})"


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """How many times to try a file operation, and how long to wait between tries."""

    attempts: int = 6
    initial_delay_s: float = 0.05
    max_delay_s: float = 1.0

    @classmethod
    def from_config(cls, config: object) -> RetryPolicy:
        """Build from a ``FileIOConfig``. Duck-typed to keep util independent of config."""

        return cls(
            attempts=max(1, int(getattr(config, "retry_attempts", 6))),
            initial_delay_s=max(0.0, int(getattr(config, "retry_initial_delay_ms", 50)) / 1000),
            max_delay_s=max(0.0, int(getattr(config, "retry_max_delay_ms", 1000)) / 1000),
        )

    def delays(self) -> Iterator[float]:
        """The waits between tries: exponential, capped, one fewer than ``attempts``."""

        delay = self.initial_delay_s
        for _ in range(max(0, self.attempts - 1)):
            yield min(delay, self.max_delay_s)
            delay *= 2


NO_RETRY = RetryPolicy(attempts=1)


@dataclass(frozen=True, slots=True)
class Attempted(Generic[T]):
    """A completed operation plus what it took to get there."""

    value: T
    attempts: int
    waited_s: float


class _RetryLoop:
    """The decisions a retry makes, shared by the blocking and async drivers.

    Only the waiting differs between the two - one sleeps the thread, the other
    yields to the event loop - so everything else lives here rather than in two
    copies that would drift apart.
    """

    def __init__(self, policy: RetryPolicy, *, what: str, path: Path) -> None:
        self._policy = policy
        self._what = what
        self._path = path
        self._delays = list(policy.delays())
        self._last: OSError | None = None
        self.waited_s = 0.0

    def next_delay(self, exc: OSError, attempt: int) -> float | None:
        """How long to wait before try ``attempt + 1``, or ``None`` to stop.

        Re-raises a permanent failure - a missing directory, a bad path, no
        space - because waiting cannot change any of those.
        """

        if not is_transient_os_error(exc):
            raise exc
        self._last = exc
        if attempt > len(self._delays):
            return None
        delay = self._delays[attempt - 1]
        self.waited_s += delay
        return delay

    @property
    def last_error(self) -> OSError | None:
        return self._last

    def exhausted(self) -> FileOperationError:
        last = self._last
        assert last is not None
        return FileOperationError(
            f"Could not {self._what} {self._path}: {describe_os_error(last)}. "
            f"Tried {self._policy.attempts} times over {self.waited_s:.2f}s. "
            "Close whatever is holding the file, or raise file_io.retry_attempts.",
            path=self._path,
            operation=self._what,
            attempts=self._policy.attempts,
            waited_s=self.waited_s,
        )


def retry_transient(
    operation: Callable[[], T],
    *,
    policy: RetryPolicy,
    what: str,
    path: Path,
) -> Attempted[T]:
    """Run ``operation``, retrying only the failures that are worth retrying.

    A permanent error is raised on the first try. A transient one is retried
    until the policy runs out, and only then becomes a
    :class:`FileOperationError` that says what held the file and for how long.
    """

    loop = _RetryLoop(policy, what=what, path=path)
    for attempt in range(1, policy.attempts + 1):
        try:
            return Attempted(value=operation(), attempts=attempt, waited_s=loop.waited_s)
        except OSError as exc:
            delay = loop.next_delay(exc, attempt)
            if delay is None:
                break
            time.sleep(delay)
    raise loop.exhausted() from loop.last_error


async def retry_transient_async(
    operation: Callable[[], Awaitable[T]],
    *,
    policy: RetryPolicy,
    what: str,
    path: Path,
) -> Attempted[T]:
    """Same contract as :func:`retry_transient`, for a caller on the event loop.

    Used where the operation is already asynchronous and cannot simply be moved
    to a worker thread - spawning a process, for one, where a freshly written
    executable is often still being scanned by the antivirus that will release
    it a moment later.
    """

    loop = _RetryLoop(policy, what=what, path=path)
    for attempt in range(1, policy.attempts + 1):
        try:
            value = await operation()
            return Attempted(value=value, attempts=attempt, waited_s=loop.waited_s)
        except OSError as exc:
            delay = loop.next_delay(exc, attempt)
            if delay is None:
                break
            await asyncio.sleep(delay)
    raise loop.exhausted() from loop.last_error


def _best_effort_fsync(handle: object) -> None:
    """Flush to disk, tolerating a filesystem that will not.

    Network shares and some Windows filesystems refuse the flush on handles they
    otherwise accept writes to. Losing the durability hint is not a reason to
    fail a write whose bytes are already out of the process.
    """

    try:
        os.fsync(handle.fileno())  # type: ignore[attr-defined]
    except OSError:
        pass


def _replace_preserving_metadata(source: Path, target: Path) -> None:
    """Swap ``source`` into ``target``'s name, keeping what the target carried.

    On Windows this prefers ``ReplaceFileW``, which - unlike a rename - keeps the
    destination's attributes, ACLs and alternate data streams. That matters on a
    host with disk encryption or a DLP agent, where a plain rename leaves a
    brand-new file that lost the state the original had.

    Any failure falls through to ``os.replace``: it is the known-good path, and
    when the real problem is a lock it raises the same error the caller's retry
    loop already understands.
    """

    if os.name == "nt" and target.exists() and _replace_file_win(source, target):
        return
    os.replace(source, target)


def _replace_file_win(source: Path, target: Path) -> bool:
    """Call ``ReplaceFileW``. Returns whether it succeeded; never raises."""

    # REPLACEFILE_IGNORE_MERGE_ERRORS | REPLACEFILE_IGNORE_ACL_ERRORS: copying
    # the old file's metadata forward is a bonus, not a reason to fail the write.
    flags = 0x00000002 | 0x00000004
    try:
        replace_file = ctypes.windll.kernel32.ReplaceFileW  # type: ignore[attr-defined]
        return bool(replace_file(str(target), str(source), None, flags, None, None))
    except Exception:
        return False


@dataclass(frozen=True, slots=True)
class WriteOutcome:
    """What a write did, including whether it had to give up atomicity."""

    path: Path
    bytes_written: int
    attempts: int
    waited_s: float
    atomic: bool

    def to_dict(self) -> dict[str, object]:
        """The parts worth reporting back, omitting the boring happy path."""

        report: dict[str, object] = {}
        if self.attempts > 1:
            report["write_attempts"] = self.attempts
            report["write_waited_s"] = round(self.waited_s, 3)
        if not self.atomic:
            report["atomic"] = False
        return report


def atomic_write_bytes(
    path: Path,
    data: bytes,
    *,
    policy: RetryPolicy = NO_RETRY,
    allow_non_atomic_fallback: bool = False,
    create_parents: bool = True,
) -> WriteOutcome:
    """Write ``data`` to ``path``, replacing it as one step where possible.

    The bytes go to a temporary file beside the target and are swapped in, so a
    crash mid-write never leaves a half-written file. Both halves are retried:
    the encryption agent can be holding the target, and - because the temporary
    file lands in a directory it watches - the replacement too.

    When the swap still cannot happen and ``allow_non_atomic_fallback`` is set,
    the file is rewritten in place instead. That is not atomic, and the returned
    outcome says so, but a file another process holds open without denying
    writes can still be written this way.
    """

    parent = path.parent
    if create_parents:
        parent.mkdir(parents=True, exist_ok=True)
    try:
        temp_name = _write_temp_file(parent, path.name, data, policy)
    except FileOperationError as blocked:
        # Staging can be blocked as easily as the swap - a scanner watching the
        # directory holds the new temporary file the moment it appears - and a
        # write that fails here is just as stuck as one that fails there.
        if not allow_non_atomic_fallback:
            raise
        return _rewrite_after(path, data, policy, blocked)
    try:
        swap = retry_transient(
            lambda: _replace_preserving_metadata(Path(temp_name), path),
            policy=policy,
            what="replace",
            path=path,
        )
    except FileOperationError as blocked:
        _discard(temp_name)
        if not allow_non_atomic_fallback:
            raise
        return _rewrite_after(path, data, policy, blocked)
    except BaseException:
        _discard(temp_name)
        raise
    return WriteOutcome(
        path=path,
        bytes_written=len(data),
        attempts=swap.attempts,
        waited_s=swap.waited_s,
        atomic=True,
    )


def _rewrite_after(
    path: Path, data: bytes, policy: RetryPolicy, blocked: FileOperationError
) -> WriteOutcome:
    """Give up atomicity after ``blocked``, and rewrite the file where it stands.

    The tries spent before this point are carried into the outcome: the write
    really did take that long, and a report that hid them would understate the
    interference.
    """

    rewrite = retry_transient(
        lambda: _rewrite_in_place(path, data),
        policy=policy,
        what="write",
        path=path,
    )
    return WriteOutcome(
        path=path,
        bytes_written=len(data),
        attempts=blocked.attempts + rewrite.attempts,
        waited_s=blocked.waited_s + rewrite.waited_s,
        atomic=False,
    )


def atomic_write_text(
    path: Path,
    text: str,
    *,
    policy: RetryPolicy = NO_RETRY,
    allow_non_atomic_fallback: bool = False,
    create_parents: bool = True,
) -> WriteOutcome:
    """UTF-8 flavour of :func:`atomic_write_bytes`."""

    return atomic_write_bytes(
        path,
        text.encode("utf-8"),
        policy=policy,
        allow_non_atomic_fallback=allow_non_atomic_fallback,
        create_parents=create_parents,
    )


def read_bytes(path: Path, *, policy: RetryPolicy = NO_RETRY) -> bytes:
    """Read a file, waiting out whatever has it open at the moment."""

    return retry_transient(path.read_bytes, policy=policy, what="read", path=path).value


def _write_temp_file(parent: Path, name: str, data: bytes, policy: RetryPolicy) -> str:
    """Put ``data`` in a fresh file beside the target, retrying a blocked create."""

    def create() -> str:
        handle_id, temp_name = tempfile.mkstemp(prefix=f".{name}.", suffix=".tmp", dir=str(parent))
        try:
            with os.fdopen(handle_id, "wb") as handle:
                handle.write(data)
                handle.flush()
                _best_effort_fsync(handle)
        except BaseException:
            _discard(temp_name)
            raise
        return temp_name

    return retry_transient(create, policy=policy, what="stage a write for", path=parent).value


def _rewrite_in_place(path: Path, data: bytes) -> None:
    """Overwrite the file itself, keeping its identity and everything attached to it.

    Writing before truncating keeps the window in which the file is short as
    small as the write itself, which matters because this path has no atomicity
    to fall back on.
    """

    mode = "r+b" if path.exists() else "wb"
    with open(path, mode) as handle:
        handle.seek(0)
        handle.write(data)
        handle.truncate()
        handle.flush()
        _best_effort_fsync(handle)


def _discard(temp_name: str) -> None:
    """Remove a staged temporary file, tolerating one that is already gone or held."""

    try:
        os.unlink(temp_name)
    except OSError:
        pass


def remove_tree(path: Path, *, policy: RetryPolicy = NO_RETRY) -> bool:
    """Delete a directory tree, waiting out locks and read-only flags.

    Windows refuses to delete a file another process still has open, and marks
    build output read-only often enough that a single ``rmtree`` is not a
    reliable way to remove a scratch directory. Between tries the read-only
    flags are cleared, which is what turns the second attempt into a different
    attempt rather than the same one repeated.

    Returns whether the tree is gone. A tree that survives is not an error -
    the caller is cleaning up, and something else can try again later.
    """

    if not path.exists():
        return True

    def attempt() -> None:
        try:
            shutil.rmtree(path)
        except OSError:
            _clear_readonly_flags(path)
            raise

    try:
        retry_transient(attempt, policy=policy, what="remove", path=path)
    except (OSError, FileOperationError):
        # Cleanup never fails loudly: on Windows a dev server still holding a
        # build directory is a normal thing to meet, and the caller is on its
        # way out. Whatever survives is reported by the return value instead.
        pass
    return not path.exists()


def _clear_readonly_flags(root: Path) -> None:
    """Make everything under ``root`` writable, so the next delete can proceed."""

    for current, directories, files in os.walk(root):
        for name in (*directories, *files):
            target = Path(current) / name
            try:
                target.chmod(target.stat().st_mode | stat.S_IWRITE)
            except OSError:
                continue
