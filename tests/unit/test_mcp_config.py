"""Reading MCP server declarations, from the user's config and the workspace."""

from __future__ import annotations

import json

from code_ai.config.models import AppConfig
from code_ai.mcp.config import (
    expand,
    load_servers,
    missing_variables,
    parse_server,
    server_name,
)


def test_a_claude_style_stdio_declaration() -> None:
    spec = parse_server(
        "github",
        {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-github"],
            "env": {"GITHUB_TOKEN": "${GH}"},
        },
    )

    assert spec.transport == "stdio"
    assert spec.command == "npx"
    assert spec.args == ["-y", "@modelcontextprotocol/server-github"]
    assert spec.env == {"GITHUB_TOKEN": "${GH}"}
    assert spec.problem == ""


def test_the_remote_shapes_other_agents_write() -> None:
    assert parse_server("a", {"type": "http", "url": "https://x/mcp"}).transport == "http"
    assert parse_server("b", {"url": "https://x/mcp"}).transport == "http"
    assert parse_server("c", {"type": "streamable-http", "url": "https://x"}).transport == "http"
    assert parse_server("d", {"transport": "sse", "url": "https://x/sse"}).transport == "sse"
    assert parse_server("e", {"serverUrl": "https://x/mcp"}).url == "https://x/mcp"


def test_a_broken_declaration_is_reported_not_raised() -> None:
    assert "Unknown transport" in parse_server("x", {"type": "ws", "url": "ws://x"}).problem
    assert parse_server("x", {}).problem
    assert "http://" in parse_server("x", {"type": "http", "url": "ftp://x"}).problem
    assert parse_server("x", "npx server").problem
    assert "command" in parse_server("x", {"type": "stdio"}).problem


def test_both_ways_of_switching_a_server_off() -> None:
    assert parse_server("x", {"command": "a", "disabled": True}).enabled is False
    assert parse_server("x", {"command": "a", "enabled": False}).enabled is False
    assert parse_server("x", {"command": "a"}).enabled is True


def test_trust_counts_only_in_the_users_own_config() -> None:
    raw = {"command": "a", "trust": True}

    assert parse_server("x", raw).trust is True
    assert parse_server("x", raw, source="claude").trust is False


def test_names_are_safe_inside_a_tool_name() -> None:
    assert server_name("my server!") == "my_server"
    assert server_name("git-hub_2") == "git-hub_2"
    assert server_name("") == "server"


def test_workspace_files_are_read_and_the_users_config_wins(tmp_path) -> None:
    (tmp_path / ".mcp.json").write_text(
        json.dumps(
            {"mcpServers": {"shared": {"command": "theirs"}, "claude_only": {"command": "c"}}}
        ),
        encoding="utf-8",
    )
    (tmp_path / ".vscode").mkdir()
    (tmp_path / ".vscode" / "mcp.json").write_text(
        json.dumps({"servers": {"vs": {"type": "http", "url": "https://vs/mcp"}}}), encoding="utf-8"
    )
    (tmp_path / ".cursor").mkdir()
    (tmp_path / ".cursor" / "mcp.json").write_text("{ not json", encoding="utf-8")

    servers = {spec.name: spec for spec in load_servers({"shared": {"command": "mine"}}, tmp_path)}

    assert set(servers) == {"shared", "claude_only", "vs"}
    assert servers["shared"].command == "mine"
    assert not servers["shared"].from_workspace
    assert servers["claude_only"].source == "claude"
    assert servers["claude_only"].from_workspace
    assert servers["vs"].source == "vscode"
    assert servers["vs"].source_path.endswith("mcp.json")


def test_variables_are_filled_and_the_missing_ones_named(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MCP_TEST_TOKEN", "abc")
    monkeypatch.delenv("MCP_TEST_MISSING", raising=False)

    assert expand("Bearer ${MCP_TEST_TOKEN}", workspace=tmp_path) == "Bearer abc"
    assert expand("${env:MCP_TEST_TOKEN}", workspace=tmp_path) == "abc"
    assert expand("${MCP_TEST_MISSING:-fallback}", workspace=tmp_path) == "fallback"
    assert expand("${workspaceFolder}/data", workspace=tmp_path) == f"{tmp_path}/data"
    assert expand("${MCP_TEST_MISSING}", workspace=tmp_path) == ""
    assert expand("${LOCAL}", workspace=tmp_path, env={"LOCAL": "x"}) == "x"
    assert missing_variables(
        ["${MCP_TEST_MISSING}", "${MCP_TEST_TOKEN}", "${workspaceFolder}", "${input:pat}"]
    ) == ["MCP_TEST_MISSING", "input:pat"]


def test_an_approval_is_bound_to_what_runs(tmp_path) -> None:
    def spec(**raw):
        return parse_server("x", {"command": "npx", "args": ["a"], **raw}, source="claude")

    approved = spec().fingerprint(tmp_path)

    assert spec().fingerprint(tmp_path) == approved
    assert spec(args=["b"]).fingerprint(tmp_path) != approved
    assert spec(env={"NODE_OPTIONS": "--require ./x.js"}).fingerprint(tmp_path) != approved
    assert spec().fingerprint(tmp_path / "other") != approved
    assert spec(timeout_s=5).fingerprint(tmp_path) == approved


def test_a_declaration_survives_the_trip_through_config() -> None:
    spec = parse_server(
        "x",
        {
            "type": "http",
            "url": "https://x/mcp",
            "headers": {"Authorization": "Bearer t"},
            "trust": True,
            "defer": "always",
            "tool_timeout_s": 30,
        },
    )

    assert parse_server("x", spec.to_config()).payload() == spec.payload()
    assert parse_server("y", {"command": "a"}).to_config() == {"command": "a"}


def test_app_config_carries_the_mcp_settings(tmp_path) -> None:
    config = AppConfig.from_mapping(
        {
            "api_mode": "ollama",
            "workspace": str(tmp_path),
            "mcp_servers": {"fs": {"command": "npx"}},
            "mcp_approved": ["abc", "abc", " "],
        }
    )
    defaults = AppConfig.from_mapping({"api_mode": "ollama", "workspace": str(tmp_path)})

    assert config.mcp_servers == {"fs": {"command": "npx"}}
    assert config.mcp_approved == ["abc"]
    assert config.mcp_enabled is True
    assert defaults.mcp_servers == {}
    assert defaults.mcp_approved == []
