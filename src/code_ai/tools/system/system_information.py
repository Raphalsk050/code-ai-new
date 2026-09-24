from __future__ import annotations

import os
import platform
import shutil
from typing import Any

from code_ai.tools.base import ToolCapability, ToolContext
from code_ai.tools.schema import tool_schema
from code_ai.util.environment import command_environment, is_secret_name


class SystemInformationTool:
    name = "system_information"
    description = (
        "Describe the machine commands run on: OS, the interactive terminal's shell, "
        "where given programs are on PATH, and the environment variables commands "
        "see (secret-looking values are never shown). Ask it before concluding a "
        "tool is missing or setting a path by hand."
    )
    capabilities = frozenset({ToolCapability.LOCAL_READ})
    input_schema = tool_schema(
        {
            "commands": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Programs to look up on PATH, e.g. ['python', 'gradle'].",
            },
            "variables": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Environment variables to read, e.g. ['JAVA_HOME'].",
            },
        }
    )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        # What the agent's commands get, not this process's own view: in the
        # packaged app sys.executable is the Python inside Code-AI, not the user's.
        environment = command_environment(pass_secrets=context.config.commands_see_secrets)
        env = environment.variables
        search = env.get("PATH") or env.get("Path") or ""
        commands = [str(item) for item in arguments.get("commands") or [] if str(item).strip()]
        wanted = [str(item) for item in arguments.get("variables") or [] if str(item).strip()]
        windows = os.name == "nt"
        return {
            "os": platform.system(),
            "os_release": platform.release(),
            "architecture": platform.machine(),
            "shell": (env.get("COMSPEC") if windows else env.get("SHELL")) or "",
            "commands_run": (
                "directly, without a shell; cmd built-ins and .cmd/.bat tools are handled"
                if windows
                else "directly, without a shell"
            ),
            "workspace": str(context.workspace.root),
            "cpu_count": os.cpu_count(),
            "python": shutil.which("python", path=search) or shutil.which("python3", path=search),
            "commands": {name: shutil.which(name, path=search) for name in commands},
            "variables": {
                name: ("<set, secret>" if is_secret_name(name) and env.get(name) else env.get(name))
                for name in wanted
            },
            "withheld_secrets": list(environment.withheld),
            "path": [entry for entry in search.split(os.pathsep) if entry][:40],
        }
