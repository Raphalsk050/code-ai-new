from __future__ import annotations

from typing import Any

from code_ai.core.errors import CancellationError, CommandTimeoutError, ToolExecutionError
from code_ai.tools.base import ToolCapability, ToolContext
from code_ai.tools.process.command_runner import CommandRunner


class ExecuteCommandTool:
    name = "execute_command"
    description = "Run a bounded non-interactive command inside the workspace."
    capabilities = frozenset({ToolCapability.PROCESS})
    input_schema = {
        "type": "object",
        "properties": {
            "argv": {
                "oneOf": [
                    {"type": "array", "items": {"type": "string"}, "minItems": 1},
                    {"type": "string"},
                ]
            },
            "cwd": {"type": "string"},
            "timeout": {"type": "number", "minimum": 0.1},
            "env": {"type": "object", "additionalProperties": {"type": "string"}},
            "shell": {"type": "boolean"},
        },
        "required": ["argv"],
        "additionalProperties": False,
    }

    def __init__(self) -> None:
        self._runner = CommandRunner()

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        cwd = context.workspace.relative_workdir(arguments.get("cwd"))
        requested_timeout = float(
            arguments.get("timeout") or context.config.budgets.default_tool_timeout_s
        )
        timeout = min(
            requested_timeout,
            context.config.budgets.max_tool_call_seconds,
            context.config.budgets.max_tool_wall_time_s,
        )
        try:
            result = await self._runner.run(
                argv=arguments["argv"],
                cwd=cwd,
                timeout=timeout,
                event_bus=context.event_bus,
                cancel_event=context.cancel_event,
                extra_env=arguments.get("env") if isinstance(arguments.get("env"), dict) else None,
                shell=bool(arguments.get("shell", False)),
                max_output_chars=context.config.budgets.max_tool_output_chars,
            )
        except CommandTimeoutError as exc:
            raise ToolExecutionError(f"Command timed out after {timeout:g}s.") from exc
        except CancellationError:
            raise
        except OSError as exc:
            raise ToolExecutionError(f"Command failed to start: {exc}") from exc
        return result.to_dict(max_chars=context.config.budgets.max_tool_output_chars)
