from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from code_ai.util.fileio import RetryPolicy, atomic_write_bytes

_SLUG = re.compile(r"[^a-z0-9]+")
_MAX_SLUG_LENGTH = 40
_TRUNCATION_NOTICE = "\n[truncated: artifact size limit reached]\n"

# The recorder is handed a size cap rather than the app configuration, so it
# uses the built-in retry defaults that mirror the file_io section.
_POLICY = RetryPolicy()


def _slugify(label: str) -> str:
    slug = _SLUG.sub("-", label.strip().lower()).strip("-")[:_MAX_SLUG_LENGTH].strip("-")
    return slug or "run"


@dataclass(frozen=True, slots=True)
class RunRecord:
    """Where one command's full output was persisted.

    The agent gets these paths back instead of the whole log, so an output too
    large to fit in a tool result is still reachable: it reads the file when it
    needs the detail, and works from the summary when it does not.
    """

    directory: Path
    stdout: Path
    stderr: Path
    result: Path
    truncated: bool

    def to_dict(self, *, relative_to: Path) -> dict[str, Any]:
        def rel(path: Path) -> str:
            return path.relative_to(relative_to).as_posix()

        return {
            "directory": rel(self.directory),
            "stdout": rel(self.stdout),
            "stderr": rel(self.stderr),
            "result": rel(self.result),
            "truncated": self.truncated,
        }


class ArtifactRecorder:
    """Persists command runs under the sandbox as structured, readable records.

    One directory per run holding the two streams verbatim and a JSON summary,
    each stream capped so a runaway build cannot fill the disk through the very
    mechanism meant to keep it contained.
    """

    def __init__(self, root: Path, *, max_bytes: int) -> None:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive.")
        self._root = root
        self._max_bytes = max_bytes
        self._sequence = 0

    @property
    def root(self) -> Path:
        return self._root

    def record(
        self,
        *,
        label: str,
        stdout: str,
        stderr: str,
        metadata: Mapping[str, Any],
    ) -> RunRecord:
        directory = self._allocate(_slugify(label))
        directory.mkdir(parents=True, exist_ok=True)
        stdout_path = directory / "stdout.log"
        stderr_path = directory / "stderr.log"
        result_path = directory / "result.json"

        stdout_truncated = self._write_stream(stdout_path, stdout)
        stderr_truncated = self._write_stream(stderr_path, stderr)
        truncated = stdout_truncated or stderr_truncated

        summary = dict(metadata)
        summary["artifacts"] = {
            "stdout": stdout_path.name,
            "stderr": stderr_path.name,
            "stdout_truncated": stdout_truncated,
            "stderr_truncated": stderr_truncated,
        }
        _write(
            result_path,
            json.dumps(summary, indent=2, sort_keys=True, default=str).encode("utf-8"),
        )
        return RunRecord(
            directory=directory,
            stdout=stdout_path,
            stderr=stderr_path,
            result=result_path,
            truncated=truncated,
        )

    def _allocate(self, slug: str) -> Path:
        """Pick the next free ``NNNN-slug`` directory.

        The counter is advisory, not authoritative: the loop still checks the
        filesystem, so a recorder rebuilt against an existing sandbox never
        overwrites a run recorded before it.
        """

        while True:
            self._sequence += 1
            candidate = self._root / f"{self._sequence:04d}-{slug}"
            if not candidate.exists():
                return candidate

    def _write_stream(self, path: Path, text: str) -> bool:
        data = text.encode("utf-8", errors="replace")
        if len(data) <= self._max_bytes:
            _write(path, data)
            return False
        # Cut on a character boundary so the saved log stays valid UTF-8.
        head = data[: self._max_bytes].decode("utf-8", errors="ignore")
        _write(path, (head + _TRUNCATION_NOTICE).encode("utf-8"))
        return True


def _write(path: Path, data: bytes) -> None:
    """Write one artifact, waiting out a scanner that grabbed it as it appeared."""

    atomic_write_bytes(path, data, policy=_POLICY, allow_non_atomic_fallback=True)
