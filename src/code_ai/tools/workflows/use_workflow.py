from __future__ import annotations

from typing import Any

from code_ai.core.errors import ToolExecutionError
from code_ai.core.workflows import WorkflowService, native_workflow_sources
from code_ai.tools.base import ToolCapability, ToolContext
from code_ai.tools.output import bound_text
from code_ai.tools.schema import tool_schema


def _service(context: ToolContext) -> WorkflowService:
    """The session's workflow service, or a Code-AI-only fallback.

    The wired service also searches workflow directories other agents own (see
    :mod:`code_ai.interop`). A directly constructed context has none, so fall
    back to Code-AI's own directories rather than failing the call.
    """

    configured = getattr(context, "workflows", None)
    if configured is not None:
        return configured
    return WorkflowService(sources=native_workflow_sources(context.config.workspace))


class UseWorkflowTool:
    name = "use_workflow"
    description = (
        "Load a saved workflow: a named, step-by-step procedure stored as markdown. "
        "Call with no name to list the available workflows; call with a name to load "
        "its steps, then follow them in order for the current task. Use it whenever "
        "the user invokes a workflow by name (for example '/deploy' or 'run the "
        "release workflow')."
    )
    capabilities = frozenset({ToolCapability.LOCAL_READ})
    input_schema = tool_schema(
        {
            "name": {
                "type": "string",
                "description": (
                    "Name of the workflow to load, with or without the '.md' suffix. "
                    "Omit (null) to list every available workflow with its description."
                ),
            },
        },
        required=(),
    )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        service = _service(context)
        requested = arguments.get("name")
        name = str(requested).strip() if requested is not None else ""

        if not name:
            workflows = service.load()
            return {
                "workflow_dirs": [str(source.root) for source in service.sources],
                "count": len(workflows),
                "workflows": [record.to_summary() for record in workflows],
            }

        record = service.find(name)
        if record is None:
            available = ", ".join(item.name for item in service.load()) or "(none)"
            raise ToolExecutionError(
                f"Workflow not found: {name}. Available workflows: {available}."
            )
        max_chars = context.config.budgets.max_tool_output_chars
        return {
            "name": record.name,
            "description": record.description,
            "path": str(record.path),
            "scope": record.scope,
            "origin": record.origin,
            "steps": bound_text(record.body, max_chars),
        }
