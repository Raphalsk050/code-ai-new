from __future__ import annotations

import hashlib
from pathlib import Path

from code_ai.core.errors import ToolExecutionError
from code_ai.util.fileio import NO_RETRY, RetryPolicy, read_bytes, retry_transient


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path, *, policy: RetryPolicy = NO_RETRY) -> str:
    """Hash a file in chunks, waiting out whatever has it open.

    Retried as a whole rather than per chunk: a file another process is
    rewriting mid-read would otherwise hash a mixture of two versions.
    """

    def digest_once() -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    return retry_transient(digest_once, policy=policy, what="hash", path=path).value


# The largest text file this program will read whole. The cost past it is
# not the reading but everything after: the NUL scan, the UTF-8 decode and the
# line split each walk the whole buffer, and they hold the caller for as long
# as the file is big. Nothing that large is source anyone meant to read - a
# minified bundle, a rotated log, a vendored blob - so refusing early, with the
# size named, beats freezing on it.
MAX_TEXT_FILE_BYTES = 8 * 1024 * 1024


def reject_oversized(path: Path) -> None:
    """Refuse a file too big to read in one piece.

    A missing or unreadable stat is not this function's business: the open
    that follows will report it with a better message.
    """

    try:
        size = path.stat().st_size
    except OSError:
        return
    if size > MAX_TEXT_FILE_BYTES:
        raise ToolExecutionError(
            f"File is too large to read whole ({size / 1048576:.1f} MiB, limit "
            f"{MAX_TEXT_FILE_BYTES // 1048576} MiB): {path}. Use search_code or "
            "search_index to find the lines you need."
        )


def reject_binary(data: bytes, *, path: Path) -> None:
    if b"\x00" in data:
        raise ToolExecutionError(f"Refusing to read binary file: {path}")


def read_text_file(path: Path, *, policy: RetryPolicy = NO_RETRY) -> tuple[str, str]:
    """Read a whole text file. Blocking: call it from a worker thread."""

    reject_oversized(path)
    data = read_bytes(path, policy=policy)
    reject_binary(data, path=path)
    try:
        return data.decode("utf-8"), sha256_bytes(data)
    except UnicodeDecodeError as exc:
        raise ToolExecutionError(f"File is not valid UTF-8 text: {path}") from exc
