"""What a graph node may hand its agent, resolved against a real tool registry."""

from __future__ import annotations

import pytest

from code_ai.bootstrap import build_tool_registry
from code_ai.core.graph.professions import graph_profile_registry
from code_ai.core.graph.runtime import ToolAccess, ToolsetBinding
from code_ai.core.graph.toolbox import apply_access, grantable_names, tools_payload
from code_ai.tools.base import ToolCapability


@pytest.fixture(scope="module")
def registry():
    return build_tool_registry()


def profile(name: str):
    found = graph_profile_registry().get(name)
    assert found is not None
    return found


def granted(registry, resolved) -> set[str]:
    subset = registry.select(
        resolved.allowed_capabilities,
        names=resolved.allowed_tools,
        exclude=resolved.blocked_tools,
    )
    return set(subset.names())


def test_the_list_holds_no_tool_a_sub_agent_may_never_hold(registry) -> None:
    rows = {row["name"]: row for row in tools_payload(registry)}

    assert "read_file" in rows and rows["read_file"]["group"] == "read"
    assert rows["execute_command"]["group"] == "commands"
    assert rows["browser_open"]["group"] == "browser"
    assert rows["code_review"]["group"] == "review"
    for banned in ("start_terminal", "click_mouse", "ask_user", "remember", "load_tools"):
        assert banned not in rows
    assert all(row["enabled"] for row in rows.values())


def test_a_tool_switched_off_in_the_settings_is_listed_as_off(registry) -> None:
    local = build_tool_registry()
    local.set_disabled({"web_search"})

    rows = {row["name"]: row for row in tools_payload(local)}

    assert rows["web_search"]["enabled"] is False


def test_no_access_leaves_the_profession_alone(registry) -> None:
    architect = profile("architect")

    assert apply_access(architect, None, registry).allowed_capabilities == (
        architect.allowed_capabilities
    )


def test_a_switch_turns_a_capability_on_or_off(registry) -> None:
    resolved = apply_access(
        profile("architect"),
        ToolAccess(switches={"can_write": True, "can_browse": False}),
        registry,
    )

    assert ToolCapability.LOCAL_WRITE in resolved.allowed_capabilities
    assert ToolCapability.WEB not in resolved.allowed_capabilities
    names = granted(registry, resolved)
    assert "write_file" in names
    assert "web_search" not in names


def test_wired_tools_are_the_whole_grant_and_ignore_the_switches(registry) -> None:
    resolved = apply_access(
        profile("code_writer"),
        ToolAccess(
            switches={"can_run": True},
            toolsets=(
                ToolsetBinding(tools=("read_file", "document_convert")),
                ToolsetBinding(tools=("read_file", "teleport", "start_terminal")),
            ),
        ),
        registry,
    )

    assert resolved.allowed_tools == frozenset({"read_file", "document_convert"})
    assert granted(registry, resolved) == {"read_file", "document_convert"}


def test_blocked_tools_go_whatever_allowed_them(registry) -> None:
    access = ToolAccess(
        toolsets=(ToolsetBinding(tools=("read_file", "list_files")),),
        blocked=("list_files",),
    )

    assert granted(registry, apply_access(profile("agent"), access, registry)) == {"read_file"}


def test_every_grantable_name_is_in_the_list(registry) -> None:
    assert set(grantable_names(registry)) == {row["name"] for row in tools_payload(registry)}


def test_select_narrows_to_names_and_drops_the_excluded(registry) -> None:
    subset = registry.select(
        frozenset({ToolCapability.LOCAL_READ, ToolCapability.LOCAL_WRITE}),
        names=frozenset({"read_file", "write_file", "execute_command"}),
        exclude=frozenset({"write_file"}),
    )

    assert subset.names() == ["read_file"]
