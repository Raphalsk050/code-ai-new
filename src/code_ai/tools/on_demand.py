"""Experimental: every tool joins the request only once the model loads it.

The deferred groups already keep the browser and desktop schemas out, but the
everyday tools still travel on every request. With ``experimental.
on_demand_tools`` on, the request starts with the control tools the planner
cannot work without, the prompt lists everything else as one line per tool,
and ``load_tool`` (or a direct call) brings a tool in for the session.
"""

from __future__ import annotations

from code_ai.tools.base import ToolCapability
from code_ai.tools.registry import ToolRegistry

# Planner transitions and completion: the turn cannot move or end without them.
_CONTROL_CAPABILITIES = frozenset(
    {ToolCapability.INTERNAL_TRANSITION, ToolCapability.INTERNAL_COMPLETION}
)
# Loaders are chosen by mode, never by capability.
_LOADERS = frozenset({"load_tool", "load_tools"})
_SUMMARY_MAX_CHARS = 140


def always_offered(registry: ToolRegistry, name: str) -> bool:
    if name in _LOADERS:
        return False
    try:
        caps = registry.capabilities(name)
    except Exception:  # noqa: BLE001 - an unknown tool is simply not offered
        return False
    return bool(caps) and caps <= _CONTROL_CAPABILITIES


def loadable_names(registry: ToolRegistry) -> list[str]:
    return [
        name
        for name in registry.names()
        if name not in _LOADERS and not always_offered(registry, name)
    ]


def render_catalog(registry: ToolRegistry) -> str:
    """One line per loadable tool: its name and the first sentence it describes itself with."""

    lines = []
    for name in loadable_names(registry):
        tool = registry.get(name)
        summary = " ".join(str(getattr(tool, "description", "")).split())
        summary = summary.split(". ")[0].rstrip(".")
        if len(summary) > _SUMMARY_MAX_CHARS:
            summary = summary[: _SUMMARY_MAX_CHARS - 3].rstrip() + "..."
        lines.append(f"- {name}: {summary}")
    return "\n".join(lines)
