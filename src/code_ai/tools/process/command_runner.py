from __future__ import annotations

import asyncio
import os
import signal
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from code_ai.core.errors import CancellationError, CommandTimeoutError, ToolArgumentError
from code_ai.events.bus import AsyncEventBus
from code_ai.tools.output import bound_text
from code_ai.util.redaction import sanitized_environment


@dataclass(slots=True)
class CommandResult:
    argv: list[str]
    cwd: str
    exit_code: int | None
    stdout: str
    stderr: str
    duration_s: float
    timed_out: bool = False
    cancelled: bool = False

    def to_dict(self, *, max_chars: int) -> dict[str, Any]:
        return {
            "argv": self.argv,
            "cwd": self.cwd,
            "exit_code": self.exit_code,
            "stdout": bound_text(self.stdout, max_chars),
            "stderr": bound_text(self.stderr, max_chars),
            "duration_s": round(self.duration_s, 3),
            "timed_out": self.timed_out,
            "cancelled": self.cancelled,
        }


class CommandRunner:
    """Runs bounded non-interactive subprocesses with separate stdout/stderr streams."""

    async def run(
        self,
        *,
        argv: list[str],
        cwd: Path,
        timeout: float,
        event_bus: AsyncEventBus,
        cancel_event: asyncio.Event | None,
        extra_env: dict[str, str] | None = None,
        max_output_chars: int = 12000,
    ) -> CommandResult:
        if (
            not isinstance(argv, list)
            or not argv
            or not all(isinstance(item, str) and item for item in argv)
        ):
            raise ToolArgumentError("argv must be a non-empty list of strings.")
        if any("\x00" in item for item in argv):
            raise ToolArgumentError("argv contains a NUL byte.")

        env = sanitized_environment(os.environ)
        if extra_env:
            for key, value in extra_env.items():
                if "\x00" in key or "\x00" in value:
                    raise ToolArgumentError("Environment entries must not contain NUL bytes.")
                env[key] = value

        start = time.monotonic()
        preexec_fn = os.setsid if hasattr(os, "setsid") else None
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            preexec_fn=preexec_fn,
        )
        stdout_parts: list[str] = []
        stderr_parts: list[str] = []

        async def read_stream(
            stream: asyncio.StreamReader | None, name: str, sink: list[str]
        ) -> None:
            if stream is None:
                return
            while True:
                chunk = await stream.read(4096)
                if not chunk:
                    return
                text = chunk.decode("utf-8", errors="replace")
                sink.append(text)
                await event_bus.emit(
                    "command.output",
                    {"stream": name, "text": bound_text(text, max_output_chars)},
                    source="tool.execute_command",
                )

        async def wait_process() -> None:
            await asyncio.gather(
                read_stream(process.stdout, "stdout", stdout_parts),
                read_stream(process.stderr, "stderr", stderr_parts),
                process.wait(),
            )

        timed_out = False
        cancelled = False
        try:
            while True:
                if cancel_event and cancel_event.is_set():
                    cancelled = True
                    raise CancellationError("Command cancelled.")
                try:
                    await asyncio.wait_for(wait_process(), timeout=0.1)
                    break
                except TimeoutError:
                    if time.monotonic() - start > timeout:
                        timed_out = True
                        raise CommandTimeoutError("Command timed out.") from None
        except (CommandTimeoutError, CancellationError):
            self._terminate_process_group(process)
            try:
                await asyncio.wait_for(process.wait(), timeout=2.0)
            except TimeoutError:
                self._kill_process_group(process)
                await process.wait()
            if timed_out:
                raise
            raise
        finally:
            duration = time.monotonic() - start

        return CommandResult(
            argv=argv,
            cwd=str(cwd),
            exit_code=process.returncode,
            stdout="".join(stdout_parts),
            stderr="".join(stderr_parts),
            duration_s=duration,
            timed_out=timed_out,
            cancelled=cancelled,
        )

    @staticmethod
    def _terminate_process_group(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except Exception:
            process.terminate()

    @staticmethod
    def _kill_process_group(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except Exception:
            process.kill()
