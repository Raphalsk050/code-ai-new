from __future__ import annotations

from typing import Any

from code_ai.core.errors import ToolArgumentError
from code_ai.tools.base import ToolContext


def desktop_controller(context: ToolContext) -> Any:
    """Return the shared desktop controller or fail with a clear message."""

    if context.desktop_controller is None:
        raise ToolArgumentError("Desktop controller is not configured.")
    return context.desktop_controller


# The two coordinate systems a pointer argument can be written in.
SCREEN_SPACE = "screen"
IMAGE_SPACE = "image"


def resolve_point(controller: Any, arguments: dict[str, Any], x: Any, y: Any) -> tuple[int, int]:
    """Turn the coordinates a call carries into real desktop pixels.

    A model that has just looked at a screenshot reads positions off that
    image, and the image is a shrunken picture of a desktop whose origin may
    not be (0, 0). Making it do the arithmetic is what puts a click in the
    wrong place: the conversion is one multiplication and one offset, and it is
    silently wrong rather than an error when it is skipped. So the call says
    which space its numbers are in and the conversion happens here, against the
    geometry of the capture the model actually saw.
    """

    space = str(arguments.get("coordinate_space") or SCREEN_SPACE).strip().lower()
    if space not in {SCREEN_SPACE, IMAGE_SPACE}:
        raise ToolArgumentError(
            f"coordinate_space must be '{SCREEN_SPACE}' or '{IMAGE_SPACE}', got {space!r}."
        )
    try:
        point = (float(x), float(y))
    except (TypeError, ValueError):
        raise ToolArgumentError("Coordinates must be numbers.") from None
    if space == SCREEN_SPACE:
        return int(round(point[0])), int(round(point[1]))
    geometry = getattr(controller, "last_capture", None)
    if geometry is None:
        raise ToolArgumentError(
            "coordinate_space='image' needs a screenshot to measure against: "
            "call capture_screen first, then give the coordinates you read off it."
        )
    return geometry.to_screen(*point)


# Reused by every tool that takes a pointer coordinate, so the choice is
# described identically wherever it appears.
COORDINATE_SPACE_SCHEMA = {
    "type": "string",
    "description": (
        "Which coordinates these are. 'image' means read off the most recent "
        "capture_screen image - use this whenever you are clicking something "
        "you saw in a screenshot, and the scaling and monitor offset are "
        "applied for you. 'screen' (the default) means real desktop pixels."
    ),
}


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
