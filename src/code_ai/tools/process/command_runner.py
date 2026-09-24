from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from code_ai.core.errors import CancellationError, CommandTimeoutError, ToolArgumentError
from code_ai.events.bus import AsyncEventBus
from code_ai.tools.output import bound_text
from code_ai.util.environment import command_environment, resolve_argv
from code_ai.util.fileio import NO_RETRY, RetryPolicy, retry_transient_async


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
        base_env: Mapping[str, str] | None = None,
        max_output_chars: int = 12000,
        spawn_policy: RetryPolicy = NO_RETRY,
    ) -> CommandResult:
        if (
            not isinstance(argv, list)
            or not argv
            # An empty argument is legitimate (start "" app, git commit -m "");
            # only the program itself has to be named.
            or not all(isinstance(item, str) for item in argv)
            or not argv[0]
        ):
            raise ToolArgumentError("argv must be a non-empty list of strings.")
        if any("\x00" in item for item in argv):
            raise ToolArgumentError("argv contains a NUL byte.")

        # The user's environment, as their own terminal has it (see
        # code_ai.util.environment); the caller's extra entries win.
        env = dict(base_env) if base_env is not None else command_environment().variables
        if extra_env:
            for key, value in extra_env.items():
                if "\x00" in key or "\x00" in value:
                    raise ToolArgumentError("Environment entries must not contain NUL bytes.")
                env[key] = value

        start = time.monotonic()
        preexec_fn = os.setsid if hasattr(os, "setsid") else None
        launch = resolve_argv(argv, env)

        async def spawn() -> asyncio.subprocess.Process:
            return await asyncio.create_subprocess_exec(
                *launch,
                cwd=str(cwd),
                # Nothing to read: a prompt gets end-of-file at once instead of
                # waiting out the timeout - and the bridge's own stdin, which
                # carries the editor's messages, is never handed to a child.
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                preexec_fn=preexec_fn,
            )

        # A program the agent just wrote is often still being scanned by the
        # antivirus when it is asked to run, which on Windows is a hard refusal
        # rather than a wait. Retrying turns that into a short pause.
        process = (
            await retry_transient_async(
                spawn, policy=spawn_policy, what="start", path=Path(launch[0])
            )
        ).value
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
        if os.name == "nt":
            # npm.cmd is cmd.exe running node: ending cmd alone leaves node behind.
            _end_tree_windows(process.pid)
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


def _end_tree_windows(pid: int) -> None:
    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            timeout=10,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        pass
