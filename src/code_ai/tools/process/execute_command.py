from __future__ import annotations

import os
import re
import shlex
from typing import Any

from code_ai.core.errors import (
    CancellationError,
    CommandTimeoutError,
    ToolArgumentError,
    ToolExecutionError,
)
from code_ai.tools.base import ToolCapability, ToolContext
from code_ai.tools.process.command_runner import CommandRunner
from code_ai.tools.schema import tool_schema


class ExecuteCommandTool:
    name = "execute_command"
    description = (
        "Run a bounded non-interactive command inside the workspace. The command runs "
        "directly without a shell, so shell features and wrappers are unavailable: do not "
        "use pipes, redirects, '&&', globbing, or wrapper programs like 'timeout'/'time'. "
        "To set environment variables, use the 'env' argument (a name->value map) instead "
        "of a shell-style VAR=value prefix, which fails because there is no shell. "
        "Execution is already time-bounded; pass the 'timeout' argument to control the limit."
    )
    capabilities = frozenset({ToolCapability.PROCESS})
    input_schema = tool_schema(
        {
            "command": {
                "type": "string",
                "description": "Command line, split shell-like (no shell features).",
            },
            "cwd": {
                "type": "string",
                "description": "Workspace-relative working directory. Defaults to the root.",
            },
            "env": {
                "type": "object",
                "description": (
                    "Environment variables to set for this command, as a name->value map "
                    '(e.g. {"USE_FAKE_LLM": "true"}). Use this instead of a shell-style '
                    "VAR=value prefix, which fails because the command runs without a shell."
                ),
                "additionalProperties": {"type": "string"},
            },
            "timeout": {
                "type": "number",
                "description": "Soft timeout in seconds, clamped to the runtime budget.",
            },
            "reason": {
                "type": "string",
                "description": (
                    "One or two plain-language sentences explaining why this command is being "
                    "run and what it accomplishes. Shown to the user in the approval prompt "
                    "before they decide whether to allow it."
                ),
            },
        },
        required=("command",),
    )

    def __init__(self) -> None:
        self._runner = CommandRunner()

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        if arguments.get("shell") is True:
            raise ToolArgumentError("execute_command does not support shell execution.")
        argv = _coerce_argv(arguments)
        # The runner already bounds execution time, so a leading `timeout`/`gtimeout`
        # wrapper is redundant and breaks where coreutils is absent (e.g. macOS). Strip
        # it and reuse its duration when the caller did not set one explicitly.
        argv, wrapped_timeout = _strip_timeout_wrapper(argv)
        cwd = context.workspace.relative_workdir(arguments.get("cwd"))
        explicit_timeout = arguments.get("timeout")
        requested_timeout = float(
            explicit_timeout
            or wrapped_timeout
            or context.config.budgets.default_tool_timeout_s
        )
        timeout = min(
            requested_timeout,
            context.config.budgets.max_tool_call_seconds,
            context.config.budgets.max_tool_wall_time_s,
        )
        try:
            result = await self._runner.run(
                argv=argv,
                cwd=cwd,
                timeout=timeout,
                event_bus=context.event_bus,
                cancel_event=context.cancel_event,
                extra_env=_coerce_env(arguments.get("env")),
                max_output_chars=context.config.budgets.max_tool_output_chars,
            )
        except CommandTimeoutError as exc:
            raise ToolExecutionError(f"Command timed out after {timeout:g}s.") from exc
        except CancellationError:
            raise
        except OSError as exc:
            raise ToolExecutionError(f"Command failed to start: {exc}") from exc
        return result.to_dict(max_chars=context.config.budgets.max_tool_output_chars)


_TIMEOUT_WRAPPERS = frozenset({"timeout", "gtimeout"})
# GNU timeout options that consume the following token as their value.
_TIMEOUT_VALUE_OPTS = frozenset({"-k", "--kill-after", "-s", "--signal"})
_TIMEOUT_DURATION = re.compile(r"(?i)^(\d+(?:\.\d+)?)([smhd]?)$")
_TIMEOUT_UNIT_SECONDS = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}


def _parse_timeout_duration(token: str) -> float | None:
    """Parse a GNU ``timeout`` DURATION (e.g. ``30``, ``1.5m``) into seconds."""

    match = _TIMEOUT_DURATION.match(token)
    if not match:
        return None
    value = float(match.group(1))
    seconds = value * _TIMEOUT_UNIT_SECONDS[match.group(2).lower()]
    return seconds or None


def _strip_timeout_wrapper(argv: list[str]) -> tuple[list[str], float | None]:
    """Unwrap a leading ``timeout``/``gtimeout`` invocation.

    Returns the inner command plus the wrapper's duration (in seconds) when one
    is present. The wrapper is only stripped when a real command follows it, so
    a bare ``timeout`` with no inner command is left untouched to fail normally.
    """

    if not argv or argv[0] not in _TIMEOUT_WRAPPERS:
        return argv, None

    index = 1
    count = len(argv)
    while index < count and argv[index].startswith("-") and argv[index] != "--":
        option = argv[index]
        if "=" in option:
            index += 1
        elif option in _TIMEOUT_VALUE_OPTS:
            index += 2  # consume the option and its value
        else:
            index += 1
    if index < count and argv[index] == "--":
        index += 1

    # The first non-option token is the DURATION; consume it only when it parses
    # as one, otherwise treat it as the start of the inner command.
    duration: float | None = None
    if index < count:
        parsed = _parse_timeout_duration(argv[index])
        if parsed is not None:
            duration = parsed
            index += 1

    inner = argv[index:]
    if not inner:
        return argv, None
    return inner, duration


def _coerce_env(value: object) -> dict[str, str] | None:
    """Validate the optional ``env`` map into ``{str: str}``.

    Scalar values (numbers, booleans) are coerced to strings so a model that
    passes ``{"PORT": 8080}`` is not punished with a hard failure; structured
    values and non-string keys are rejected with a clear message.
    """
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ToolArgumentError("env must be a map of environment variable names to values.")
    env: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key:
            raise ToolArgumentError("env names must be non-empty strings.")
        if isinstance(item, bool):
            env[key] = "true" if item else "false"
        elif isinstance(item, (str, int, float)):
            env[key] = str(item)
        else:
            raise ToolArgumentError(f"env value for {key!r} must be a string.")
    return env or None


def _split_command_line(command: str) -> list[str]:
    """Split a command line into argv, shell-like but without a shell.

    On Windows the backslash is the path separator, not an escape character.
    POSIX-mode shlex reads it as an escape and eats it, so ``del C:\\ws\\a.txt``
    silently becomes ``del C:wsa.txt`` — a command that then runs against a
    mangled path and reports success. Dropping the escape character leaves
    backslashes literal while keeping quoting and whitespace splitting intact.

    POSIX platforms keep the standard behaviour: there a backslash really is an
    escape, and the convention is worth more than the paths it would protect.
    """
    if os.name != "nt":
        return shlex.split(command)
    lexer = shlex.shlex(command, posix=True)
    lexer.whitespace_split = True
    lexer.escape = ""
    return list(lexer)


def _coerce_argv(arguments: dict[str, Any]) -> list[str]:
    command = arguments.get("command")
    if isinstance(command, str):
        if not command.strip():
            raise ToolArgumentError("command is required.")
        try:
            argv = _split_command_line(command)
        except ValueError as exc:
            raise ToolArgumentError(f"command could not be parsed: {exc}") from exc
        if not argv:
            raise ToolArgumentError("command is required.")
        return argv

    argv = arguments.get("argv")
    if (
        not isinstance(argv, list)
        or not argv
        or not all(isinstance(item, str) and item for item in argv)
    ):
        raise ToolArgumentError("command must be a non-empty string.")
    return argv
