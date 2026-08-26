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


def reject_binary(data: bytes, *, path: Path) -> None:
    if b"\x00" in data:
        raise ToolExecutionError(f"Refusing to read binary file: {path}")


def read_text_file(path: Path, *, policy: RetryPolicy = NO_RETRY) -> tuple[str, str]:
    data = read_bytes(path, policy=policy)
    reject_binary(data, path=path)
    try:
        return data.decode("utf-8"), sha256_bytes(data)
    except UnicodeDecodeError as exc:
        raise ToolExecutionError(f"File is not valid UTF-8 text: {path}") from exc
