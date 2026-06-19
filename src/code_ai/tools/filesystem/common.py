from __future__ import annotations

import hashlib
from pathlib import Path

from code_ai.core.errors import ToolExecutionError


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def reject_binary(data: bytes, *, path: Path) -> None:
    if b"\x00" in data:
        raise ToolExecutionError(f"Refusing to read binary file: {path}")


def read_text_file(path: Path) -> tuple[str, str]:
    data = path.read_bytes()
    reject_binary(data, path=path)
    try:
        return data.decode("utf-8"), sha256_bytes(data)
    except UnicodeDecodeError as exc:
        raise ToolExecutionError(f"File is not valid UTF-8 text: {path}") from exc
