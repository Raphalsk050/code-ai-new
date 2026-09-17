from __future__ import annotations

from collections.abc import Callable
from typing import Any

from code_ai.core.errors import EnvironmentUnavailableError, ToolArgumentError
from code_ai.tools.base import ToolCapability, ToolContext
from code_ai.tools.groups import group_named, group_names
from code_ai.tools.schema import tool_schema


class LoadToolsTool:
    """Bring a deferred tool group into the request.

    Stateless like every tool: it only validates the name and says which tools
    the group holds. The orchestrator owns which groups a session has loaded
    and offers their schemas from the next model step on. ``enabled`` is the
    registry's view of which tools are switched on, so a group the user emptied
    is neither advertised nor loadable.
    """

    name = "load_tools"
    capabilities = frozenset({ToolCapability.INTERNAL_TRANSITION})

    def __init__(self, enabled: Callable[[str], bool] | None = None) -> None:
        self._enabled = enabled

    @property
    def description(self) -> str:
        return (
            "Load a group of tools that is not offered by default, so its tools "
            "appear in your tool list from the next step on. Groups: "
            + ", ".join(group_names(self._enabled))
            + ". Call it as soon as a task needs one of them; the tools are "
            "described in the system prompt under tool groups."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return tool_schema(
            {
                "group": {
                    "type": "string",
                    "description": "Name of the group to load: "
                    + ", ".join(group_names(self._enabled))
                    + ".",
                },
            },
            required=("group",),
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        name = str(arguments.get("group") or "").strip().lower()
        group = group_named(name)
        if group is None or name not in group_names(self._enabled):
            raise ToolArgumentError(
                f"Unknown tool group {name!r}. "
                f"Known groups: {', '.join(group_names(self._enabled))}."
            )
        if group.name == "desktop":
            # Say so at load time rather than on the first click: the model
            # otherwise retried the same missing-backend error four times over.
            controller = getattr(context, "desktop_controller", None)
            if controller is not None and not controller.has_pointer_backend:
                from code_ai.tools.computer.controller import _INSTALL_HINT

                raise EnvironmentUnavailableError(_INSTALL_HINT)
        tools = group.tools if self._enabled is None else filter(self._enabled, group.tools)
        return {
            "group": group.name,
            "tools": sorted(tools),
            "status": "loaded - these tools are available from your next step",
        }
