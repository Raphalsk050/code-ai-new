"""The tools a graph may hand an agent, and a node's say over them.

A graph agent is a sub-agent, so whatever a sub-agent may never hold stays out
of the list the editor shows - and out of any profile a node asks for, even by
a hand-edited name.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from code_ai.core.graph.models import PERMISSIONS
from code_ai.core.graph.runtime import ToolAccess
from code_ai.core.subagents.profiles import FORBIDDEN_SUBAGENT_CAPABILITIES, SubagentProfile
from code_ai.tools.base import ToolCapability
from code_ai.tools.groups import group_of
from code_ai.tools.registry import ToolRegistry

# Delegation needs a coordinator a sub-agent is never given.
_UNGRANTABLE = FORBIDDEN_SUBAGENT_CAPABILITIES | {ToolCapability.DELEGATE}

_CAPABILITY_OF = {field: ToolCapability(capability) for field, capability, _ in PERMISSIONS}

_SUMMARY_MAX_CHARS = 160


def grantable(registry: ToolRegistry, name: str) -> bool:
    tool = registry.tool(name)
    if tool is None:
        return False
    caps = frozenset(getattr(tool, "capabilities", frozenset()))
    return bool(caps) and not caps & _UNGRANTABLE


def grantable_names(registry: ToolRegistry) -> tuple[str, ...]:
    return tuple(name for name in registry.registered_names() if grantable(registry, name))


def tools_payload(registry: ToolRegistry) -> list[dict[str, Any]]:
    """The tool list the editor offers, grantable tools only, by name."""

    rows: list[dict[str, Any]] = []
    for name in grantable_names(registry):
        tool = registry.tool(name)
        caps = frozenset(getattr(tool, "capabilities", frozenset()))
        rows.append(
            {
                "name": name,
                "description": _summary(tool),
                "capabilities": sorted(str(item) for item in caps),
                "group": _group(name, tool, caps),
                # Switched off in the settings: the agent would not get it anyway.
                "enabled": registry.has(name),
            }
        )
    return rows


def capabilities_of(switches: dict[str, bool]) -> frozenset[ToolCapability]:
    """The capabilities a set of permission switches turns on."""

    return frozenset(
        _CAPABILITY_OF[field] for field, on in switches.items() if on and field in _CAPABILITY_OF
    )


def apply_access(
    profile: SubagentProfile, access: ToolAccess | None, registry: ToolRegistry
) -> SubagentProfile:
    """``profile`` narrowed or widened by what the node says.

    Wired toolsets are the exact grant and ignore the switches; otherwise each
    switch the node set overrides the profession's. Blocked tools always go.
    """

    blocked = profile.blocked_tools | frozenset(access.blocked if access else ())
    if access is not None and access.wired:
        names = frozenset(name for name in access.tool_names() if grantable(registry, name))
        caps: set[ToolCapability] = set()
        for name in names:
            caps.update(getattr(registry.tool(name), "capabilities", ()))
        return dataclasses.replace(
            profile,
            allowed_capabilities=frozenset(caps) - _UNGRANTABLE,
            allowed_tools=names,
            blocked_tools=blocked,
        )

    caps = set(profile.allowed_capabilities)
    for field, on in (access.switches if access else {}).items():
        capability = _CAPABILITY_OF.get(field)
        if capability is None:
            continue
        if on:
            caps.add(capability)
        else:
            caps.discard(capability)
    return dataclasses.replace(
        profile,
        allowed_capabilities=frozenset(caps) - _UNGRANTABLE,
        blocked_tools=blocked,
    )


def _group(name: str, tool: Any, caps: frozenset[ToolCapability]) -> str:
    if ToolCapability.MCP in caps and getattr(tool, "server", ""):
        # One group per server, whether or not its tools load on demand.
        return f"mcp:{tool.server}"
    deferred = group_of(name)
    if deferred is not None:
        return deferred.name
    if ToolCapability.REVIEW in caps:
        return "review"
    if ToolCapability.PROCESS in caps:
        return "commands"
    if ToolCapability.LOCAL_WRITE in caps:
        return "edit"
    if ToolCapability.WEB in caps:
        return "web"
    return "read"


def _summary(tool: Any) -> str:
    text = " ".join(str(getattr(tool, "description", "")).split())
    first = text.split(". ")[0].rstrip(".")
    if len(first) <= _SUMMARY_MAX_CHARS:
        return first
    return first[: _SUMMARY_MAX_CHARS - 3].rstrip() + "..."
