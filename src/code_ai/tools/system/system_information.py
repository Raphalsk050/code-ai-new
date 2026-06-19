from __future__ import annotations

import os
import platform
import shutil
import sys
from typing import Any

from code_ai.tools.base import ToolContext


class SystemInformationTool:
    name = "system_information"
    description = "Return non-sensitive system information useful for development tasks."
    input_schema = {
        "type": "object",
        "properties": {
            "commands": {"type": "array", "items": {"type": "string"}},
        },
        "additionalProperties": False,
    }

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        commands = arguments.get("commands") or []
        availability = {}
        if isinstance(commands, list):
            availability = {str(command): bool(shutil.which(str(command))) for command in commands}
        return {
            "os": platform.system(),
            "os_release": platform.release(),
            "architecture": platform.machine(),
            "python_version": sys.version.split()[0],
            "current_shell": os.path.basename(os.environ.get("SHELL", "")),
            "workspace": str(context.workspace.root),
            "cpu_count": os.cpu_count(),
            "terminal": {
                "term": os.environ.get("TERM", ""),
                "columns": shutil.get_terminal_size(fallback=(80, 24)).columns,
                "rows": shutil.get_terminal_size(fallback=(80, 24)).lines,
            },
            "commands": availability,
        }
