from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from code_ai.core.errors import ToolArgumentError
from code_ai.providers.models import ToolDefinition
from code_ai.tools.base import BaseTool, ToolContext


@dataclass(slots=True)
class ToolRegistry:
    _tools: dict[str, BaseTool] = field(default_factory=dict)

    def register(self, tool: BaseTool) -> None:
        if tool.name in self._tools:
            raise ToolArgumentError(f"Tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def names(self) -> list[str]:
        return sorted(self._tools)

    def definitions(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name=self._tools[name].name,
                description=self._tools[name].description,
                input_schema=self._tools[name].input_schema,
            )
            for name in self.names()
        ]

    async def execute(
        self, name: str, arguments: dict[str, Any], context: ToolContext
    ) -> dict[str, Any]:
        tool = self._tools.get(name)
        if tool is None:
            raise ToolArgumentError(f"Unknown tool: {name}")
        if not isinstance(arguments, dict):
            raise ToolArgumentError("Tool arguments must be a JSON object.")
        return await tool.execute(arguments, context)
