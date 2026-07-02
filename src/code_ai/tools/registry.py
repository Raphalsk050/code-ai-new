from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from code_ai.core.errors import ToolArgumentError
from code_ai.providers.models import ToolDefinition
from code_ai.tools.base import BaseTool, ToolCapability, ToolContext


@dataclass(slots=True)
class ToolRegistry:
    _tools: dict[str, BaseTool] = field(default_factory=dict)

    def register(self, tool: BaseTool) -> None:
        if tool.name in self._tools:
            raise ToolArgumentError(f"Tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def names(self) -> list[str]:
        return sorted(self._tools)

    def definitions(self, allowed_names: set[str] | None = None) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name=self._tools[name].name,
                description=self._tools[name].description,
                input_schema=self._tools[name].input_schema,
            )
            for name in self.names()
            if allowed_names is None or name in allowed_names
        ]

    def capabilities(self, name: str) -> frozenset[ToolCapability]:
        tool = self._tools.get(name)
        if tool is None:
            raise ToolArgumentError(f"Unknown tool: {name}")
        raw_capabilities = getattr(tool, "capabilities", frozenset())
        return frozenset(raw_capabilities)

    def has(self, name: str) -> bool:
        return name in self._tools

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def select(self, allowed_capabilities: frozenset[ToolCapability]) -> ToolRegistry:
        """Return a new registry holding only tools this capability set permits.

        A tool is included when it declares at least one capability and *all* of
        its capabilities fall within ``allowed_capabilities``. This is how a
        sub-agent gets a registry restricted to its profile: a coder never sees
        the interactive-terminal tools, an explorer never sees the writers. Tool
        instances are shared by reference - they are stateless, so no isolation is
        lost, and nothing that carries per-session state is ever handed over.
        """
        subset = ToolRegistry()
        for name in self.names():
            tool = self._tools[name]
            caps = frozenset(getattr(tool, "capabilities", frozenset()))
            if caps and caps <= allowed_capabilities:
                subset.register(tool)
        return subset

    async def execute(
        self, name: str, arguments: dict[str, Any], context: ToolContext
    ) -> dict[str, Any]:
        tool = self._tools.get(name)
        if tool is None:
            raise ToolArgumentError(f"Unknown tool: {name}")
        if not isinstance(arguments, dict):
            raise ToolArgumentError("Tool arguments must be a JSON object.")
        return await tool.execute(arguments, context)
