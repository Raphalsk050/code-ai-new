from __future__ import annotations

from typing import Any

from code_ai.core.errors import (
    CancellationError,
    CommandTimeoutError,
    ToolExecutionError,
)
from code_ai.tools.base import ToolCapability, ToolContext
from code_ai.tools.process.command_runner import CommandRunner
from code_ai.tools.schema import tool_schema

# Curated, strictly read-only git inspections. Every bundle is hard-coded argv so
# the tool can never mutate the repository, regardless of the model's input.
_BUNDLES: dict[str, list[list[str]]] = {
    "overview": [
        ["git", "status", "--short", "--branch"],
        ["git", "log", "--oneline", "-15"],
        ["git", "diff", "--stat"],
    ],
    "status": [
        ["git", "status"],
    ],
    "log": [
        ["git", "log", "--oneline", "-30"],
    ],
    "diff": [
        ["git", "diff", "--stat"],
        ["git", "diff"],
        ["git", "diff", "--cached"],
    ],
    "branches": [
        ["git", "branch", "-vv"],
        ["git", "remote", "-v"],
    ],
}
_DEFAULT_FOCUS = "overview"


class GitReviewTool:
    name = "git_review"
    description = (
        "Inspect the git repository to understand what is going on: branch and working "
        "state, recent commit history, and pending changes. Runs only read-only git "
        "commands and never modifies the repository. Use 'focus' to scope the inspection "
        "to one of: overview (default), status, log, diff, branches."
    )
    capabilities = frozenset({ToolCapability.PROCESS, ToolCapability.LOCAL_READ})
    input_schema = tool_schema(
        {
            "focus": {
                "type": "string",
                "description": (
                    "What to inspect: overview (default), status, log, diff, or branches. "
                    "Unknown values fall back to overview."
                ),
            },
        },
    )

    def __init__(self) -> None:
        self._runner = CommandRunner()

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        focus = arguments.get("focus")
        focus = focus.strip().lower() if isinstance(focus, str) and focus.strip() else _DEFAULT_FOCUS
        bundle = _BUNDLES.get(focus, _BUNDLES[_DEFAULT_FOCUS])
        cwd = context.workspace.relative_workdir(None)
        timeout = min(
            context.config.budgets.default_tool_timeout_s,
            context.config.budgets.max_tool_call_seconds,
            context.config.budgets.max_tool_wall_time_s,
        )
        max_chars = context.config.budgets.max_tool_output_chars

        commands: list[dict[str, Any]] = []
        for argv in bundle:
            try:
                result = await self._runner.run(
                    argv=argv,
                    cwd=cwd,
                    timeout=timeout,
                    event_bus=context.event_bus,
                    cancel_event=context.cancel_event,
                    max_output_chars=max_chars,
                )
            except CommandTimeoutError as exc:
                raise ToolExecutionError(
                    f"git command timed out after {timeout:g}s: {' '.join(argv)}"
                ) from exc
            except CancellationError:
                raise
            except OSError as exc:
                # git missing or otherwise unable to start: degrade gracefully.
                raise ToolExecutionError(f"git is not available: {exc}") from exc
            payload = result.to_dict(max_chars=max_chars)
            payload["command"] = " ".join(argv)
            commands.append(payload)

        return {"focus": focus if focus in _BUNDLES else _DEFAULT_FOCUS, "commands": commands}
