from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from code_ai.core.errors import ToolArgumentError, ToolExecutionError
from code_ai.providers.models import ToolDefinition
from code_ai.tools.base import BaseTool, ToolCapability, ToolContext
from code_ai.util.fileio import describe_os_error


@dataclass(slots=True)
class ToolRegistry:
    _tools: dict[str, BaseTool] = field(default_factory=dict)
    # Tools the user switched off. Every lookup below skips them, so to the
    # model, the planner and the sub-agents a disabled tool is indistinguishable
    # from one that was never registered. The set is shared by reference with
    # every ``select`` subset, which is what lets a switch reach sub-agents too.
    _disabled: set[str] = field(default_factory=set)

    def register(self, tool: BaseTool) -> None:
        if tool.name in self._tools:
            raise ToolArgumentError(f"Tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def names(self) -> list[str]:
        return sorted(name for name in self._tools if name not in self._disabled)

    def registered_names(self) -> list[str]:
        """Every registered tool, disabled or not. For the switches, not the agent."""

        return sorted(self._tools)

    def tool(self, name: str) -> BaseTool | None:
        """A registered tool whether or not it is switched on."""

        return self._tools.get(name)

    def is_registered(self, name: str) -> bool:
        return name in self._tools

    def disabled_names(self) -> set[str]:
        return set(self._disabled)

    def set_disabled(self, names: Iterable[str]) -> None:
        # In place: subsets already handed to sub-agents hold this same set.
        # Names not registered yet are kept, so a tool registered later (the
        # dispatcher is) still starts out disabled.
        self._disabled.clear()
        self._disabled.update(names)

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
        tool = self.get(name)
        if tool is None:
            raise ToolArgumentError(f"Unknown tool: {name}")
        raw_capabilities = getattr(tool, "capabilities", frozenset())
        return frozenset(raw_capabilities)

    def has(self, name: str) -> bool:
        return name in self._tools and name not in self._disabled

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name) if name not in self._disabled else None

    def select(self, allowed_capabilities: frozenset[ToolCapability]) -> ToolRegistry:
        """Return a new registry holding only tools this capability set permits.

        A tool is included when it declares at least one capability and *all* of
        its capabilities fall within ``allowed_capabilities``. This is how a
        sub-agent gets a registry restricted to its profile: a coder never sees
        the interactive-terminal tools, an explorer never sees the writers. Tool
        instances are shared by reference - they are stateless, so no isolation is
        lost, and nothing that carries per-session state is ever handed over.
        """
        subset = ToolRegistry(_disabled=self._disabled)
        for name in self.registered_names():
            tool = self._tools[name]
            caps = frozenset(getattr(tool, "capabilities", frozenset()))
            if caps and caps <= allowed_capabilities:
                subset.register(tool)
        return subset

    async def execute(
        self, name: str, arguments: dict[str, Any], context: ToolContext
    ) -> dict[str, Any]:
        tool = self.get(name)
        if tool is None:
            raise ToolArgumentError(f"Unknown tool: {name}")
        if not isinstance(arguments, dict):
            raise ToolArgumentError("Tool arguments must be a JSON object.")
        try:
            return await tool.execute(arguments, context)
        except OSError as exc:
            # A filesystem saying no is a failed call, not a failed turn. Raw,
            # an OSError sails past the orchestrator's error handling and ends
            # the turn; as a tool error the model sees it, and can retry the
            # write or work around whatever is holding the file.
            where = getattr(exc, "filename", None)
            location = f" ({where})" if where else ""
            raise ToolExecutionError(
                f"{name} could not use the filesystem: {describe_os_error(exc)}{location}"
            ) from exc
