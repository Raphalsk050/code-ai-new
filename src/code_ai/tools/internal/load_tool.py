from __future__ import annotations

from collections.abc import Callable
from typing import Any

from code_ai.core.errors import EnvironmentUnavailableError, ToolArgumentError
from code_ai.tools.base import ToolCapability, ToolContext
from code_ai.tools.groups import group_of
from code_ai.tools.schema import tool_schema


class LoadToolTool:
    """Bring one tool into the request (experimental on-demand mode).

    Stateless like ``load_tools``: it validates the name and the orchestrator
    records what was loaded. It only exists for the model while
    ``experimental.on_demand_tools`` is on; the orchestrator hides it otherwise.
    ``enabled`` is the registry's view of which tools are switched on.
    """

    name = "load_tool"
    description = (
        "Load one tool by its exact name, so it appears in your tool list, with "
        "its parameters, from the next step on. The loadable tools are listed in "
        "the system prompt. Load several at once by calling it several times in "
        "the same batch."
    )
    capabilities = frozenset({ToolCapability.INTERNAL_TRANSITION})
    input_schema = tool_schema(
        {
            "name": {
                "type": "string",
                "description": "Exact name of the tool to load, e.g. read_file.",
            },
        },
        required=("name",),
    )

    def __init__(self, enabled: Callable[[str], bool] | None = None) -> None:
        self._enabled = enabled

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        name = str(arguments.get("name") or "").strip()
        known = bool(name) and (self._enabled is None or self._enabled(name))
        if not known or name in {"load_tool", "load_tools"}:
            raise ToolArgumentError(f"Unknown tool {name!r}.")
        group = group_of(name)
        if group is not None and group.name == "desktop":
            controller = getattr(context, "desktop_controller", None)
            if controller is not None and not controller.has_pointer_backend:
                from code_ai.tools.computer.controller import _INSTALL_HINT

                raise EnvironmentUnavailableError(_INSTALL_HINT)
        return {"tool": name, "status": "loaded - available from your next step"}
