from __future__ import annotations

from typing import Any

from code_ai.core.errors import ToolArgumentError
from code_ai.tools.base import ToolContext


def desktop_controller(context: ToolContext) -> Any:
    """Return the shared desktop controller or fail with a clear message."""

    if context.desktop_controller is None:
        raise ToolArgumentError("Desktop controller is not configured.")
    return context.desktop_controller


async def position_payload(
    context: ToolContext,
    controller: Any,
    action: str,
    extra: dict[str, Any],
) -> dict[str, Any]:
    """Build a uniform response and announce the action on the event bus.

    Every pointer action echoes the resulting cursor position so the model can
    reason about where it landed without a separate round-trip, and emits a
    ``computer.action`` event so the UI can surface what the agent is doing on
    the real screen.
    """

    payload = {"action": action, **extra}
    await context.event_bus.emit("computer.action", payload, source=f"tool.{action}")
    return payload
