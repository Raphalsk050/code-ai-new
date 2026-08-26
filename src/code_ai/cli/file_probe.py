"""``code-ai doctor file-io``: measure how hostile this host is to file writes.

Written for the case that motivated the retry layer: a locked-down Windows
machine where an antivirus, a sync client or a disk-encryption agent holds
files for a moment and makes ordinary writes fail. Those failures are invisible
in a bug report - "it sometimes fails" - so this exercises the exact code path
the agent uses and reports what actually happened: how often a write needed a
second try, how long it waited, whether it ever had to give up atomicity, and
which error the operating system raised when it failed outright.

Run it in the directory that misbehaves. It writes only to its own temporary
file and removes it afterwards.
"""

from __future__ import annotations

import platform
import time
from dataclasses import dataclass, field
from pathlib import Path

from code_ai.config.models import AppConfig
from code_ai.util.fileio import (
    FileOperationError,
    RetryPolicy,
    atomic_write_bytes,
    describe_os_error,
    read_bytes,
)

PROBE_FILENAME = ".code-ai-file-probe.tmp"


@dataclass(slots=True)
class ProbeReport:
    """What a run of the probe observed."""

    rounds: int = 0
    clean: int = 0
    retried: int = 0
    non_atomic: int = 0
    failed: int = 0
    waited_s: float = 0.0
    causes: dict[str, int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.failed == 0

    def note_cause(self, cause: str) -> None:
        self.causes[cause] = self.causes.get(cause, 0) + 1

    def render(self) -> str:
        lines = [
            f"rounds:            {self.rounds}",
            f"clean:             {self.clean}",
            f"needed a retry:    {self.retried}",
            f"lost atomicity:    {self.non_atomic}",
            f"failed outright:   {self.failed}",
            f"total time waited: {self.waited_s:.2f}s",
        ]
        if self.causes:
            lines.append("causes seen:")
            for cause, count in sorted(self.causes.items(), key=lambda item: -item[1]):
                lines.append(f"  {count:>4}x {cause}")
        return "\n".join(lines)


def probe_directory(
    directory: Path, *, policy: RetryPolicy, rounds: int, fallback: bool
) -> ProbeReport:
    """Write, read back and replace a file ``rounds`` times, recording what it cost."""

    report = ProbeReport()
    target = directory / PROBE_FILENAME
    try:
        for index in range(rounds):
            report.rounds += 1
            payload = f"code-ai file probe round {index}\n".encode()
            try:
                outcome = atomic_write_bytes(
                    target,
                    payload,
                    policy=policy,
                    allow_non_atomic_fallback=fallback,
                )
            except FileOperationError as exc:
                report.failed += 1
                report.waited_s += exc.waited_s
                report.note_cause(str(exc).split(". Tried")[0].split(": ", 1)[-1])
                continue
            except OSError as exc:
                report.failed += 1
                report.note_cause(describe_os_error(exc))
                continue

            report.waited_s += outcome.waited_s
            if not outcome.atomic:
                report.non_atomic += 1
            elif outcome.attempts > 1:
                report.retried += 1
            else:
                report.clean += 1

            try:
                if read_bytes(target, policy=policy) != payload:
                    report.failed += 1
                    report.note_cause("the file read back different from what was written")
            except (FileOperationError, OSError) as exc:
                report.failed += 1
                report.note_cause(
                    describe_os_error(exc) if isinstance(exc, OSError) else str(exc)
                )
    finally:
        try:
            target.unlink()
        except OSError:
            pass
    return report


def run_file_probe(config: AppConfig, *, rounds: int = 25, directory: Path | None = None) -> int:
    """Entry point for the CLI. Returns a process exit code."""

    target_dir = (directory or config.workspace).expanduser().resolve()
    policy = RetryPolicy.from_config(config.file_io)
    print(f"host:      {platform.system()} {platform.release()}")
    print(f"directory: {target_dir}")
    print(
        "policy:    "
        f"{policy.attempts} attempts, "
        f"{policy.initial_delay_s * 1000:.0f}ms to {policy.max_delay_s * 1000:.0f}ms, "
        f"non-atomic fallback {'on' if config.file_io.allow_non_atomic_fallback else 'off'}"
    )
    started = time.monotonic()
    report = probe_directory(
        target_dir,
        policy=policy,
        rounds=rounds,
        fallback=config.file_io.allow_non_atomic_fallback,
    )
    print(f"elapsed:   {time.monotonic() - started:.2f}s")
    print()
    print(report.render())
    print()
    if report.ok and not report.retried and not report.non_atomic:
        print("Nothing is interfering with writes in this directory.")
    elif report.ok:
        print(
            "Writes are being interfered with, and the retry layer is absorbing it. "
            "Raise file_io.retry_attempts if the waits grow."
        )
    else:
        print(
            "Writes are failing even with retries. Raise file_io.retry_attempts and "
            "file_io.retry_max_delay_ms, and check the causes above against the "
            "software holding these files."
        )
    return 0 if report.ok else 1
