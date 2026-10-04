"""The session's MCP servers: connecting, tools in the registry, approval, recovery."""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest

from code_ai.bootstrap import build_application
from code_ai.config.models import AppConfig
from code_ai.core.errors import CancellationError, ToolExecutionError
from code_ai.events.bus import AsyncEventBus
from code_ai.events.models import EventEnvelope
from code_ai.mcp import manager as manager_module
from code_ai.mcp.manager import McpManager
from code_ai.mcp.tools import McpTool
from code_ai.providers.models import ModelResponse, ProviderCapabilities, ProviderEvent
from code_ai.tools.base import ToolContext
from code_ai.tools.groups import group_named, group_of, render_catalog
from code_ai.tools.registry import ToolRegistry
from code_ai.util.paths import WorkspacePolicy

SERVER = Path(__file__).with_name("mcp_fake_server.py")


def _server(*flags: str, **extra: Any) -> dict[str, Any]:
    return {"command": sys.executable, "args": [str(SERVER), *flags], **extra}


def _config(tmp_path: Path, **values: Any) -> AppConfig:
    return AppConfig.from_mapping(
        {"api_mode": "ollama", "workspace": str(tmp_path), "model": "fake", **values}
    )


class _Session:
    def __init__(self, tmp_path: Path, **values: Any) -> None:
        self.config = _config(tmp_path, **values)
        self.registry = ToolRegistry()
        self.bus = AsyncEventBus()
        self.events: list[EventEnvelope] = []
        self.saved: list[dict[str, Any]] = []
        self.bus.subscribe(self.events.append)

        async def persist(changes: dict[str, Any]) -> None:
            self.saved.append(changes)

        self.manager = McpManager(
            config=self.config,
            registry=self.registry,
            event_bus=self.bus,
            client_version="test",
            persist=persist,
        )

    def context(self, cancel: asyncio.Event | None = None) -> ToolContext:
        return ToolContext(
            config=self.config,
            workspace=WorkspacePolicy.from_path(self.config.workspace),
            event_bus=self.bus,
            cancel_event=cancel,
        )

    def status(self, name: str) -> str:
        return self.manager.servers[name].status

    async def until(self, check: Callable[[], bool], timeout: float = 20.0) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        while not check():
            if asyncio.get_running_loop().time() > deadline:
                raise AssertionError("timed out waiting for the MCP session")
            await asyncio.sleep(0.05)


@pytest.fixture(autouse=True)
def _quick_retries(monkeypatch) -> None:
    monkeypatch.setattr(manager_module, "_BACKOFF_S", 0.05)


@pytest.fixture
async def session(tmp_path) -> AsyncIterator[_Session]:
    live = _Session(tmp_path, mcp_servers={"fake": _server("--instructions", "Echo things.")})
    await live.manager.start()
    await live.until(lambda: live.status("fake") != "connecting")
    yield live
    await live.manager.close()


async def test_a_servers_tools_join_the_registry(session: _Session) -> None:
    assert session.status("fake") == "connected"
    assert session.registry.has("mcp__fake__echo")
    assert session.registry.has("mcp__fake__shout")
    # Resources and prompts are reached through the three shared tools.
    for name in ("list_mcp_resources", "read_mcp_resource", "get_mcp_prompt"):
        assert session.registry.has(name)

    result = await session.registry.execute("mcp__fake__echo", {"text": "hi"}, session.context())

    assert result["content"] == "<mcp_output>\nhi\n</mcp_output>"


async def test_the_prompt_names_the_server_and_passes_its_instructions(session: _Session) -> None:
    catalog = session.manager.prompt_catalog()

    assert "- fake (Fake Server): tools named mcp__fake__<tool>" in catalog
    assert "resources and prompts" in catalog
    assert "Echo things." in catalog


async def test_resources_and_prompts_through_the_shared_tools(session: _Session) -> None:
    context = session.context()

    listing = await session.registry.execute("list_mcp_resources", {}, context)
    read = await session.registry.execute(
        "read_mcp_resource", {"server": "fake", "uri": "file:///notes.txt"}, context
    )
    prompt = await session.registry.execute(
        "get_mcp_prompt",
        {"server": "fake", "name": "review", "arguments": {"path": "a.py"}},
        context,
    )

    assert listing["servers"][0]["resources"][0]["uri"] == "file:///notes.txt"
    assert listing["servers"][0]["templates"][0]["uriTemplate"] == "file:///{path}"
    assert "remember the milk" in read["content"]
    assert "Review a.py closely." in prompt["prompt"]


async def test_progress_is_published_and_a_cancel_stops_the_call(session: _Session) -> None:
    await session.manager.call_tool("fake", "slow", {"steps": 3})
    cancel = asyncio.Event()
    call = asyncio.create_task(
        session.registry.execute("mcp__fake__slow", {"steps": 400}, session.context(cancel))
    )
    await asyncio.sleep(0.3)
    cancel.set()

    with pytest.raises(CancellationError):
        await asyncio.wait_for(call, 5)
    progress = [e.payload for e in session.events if e.event_type == "mcp.tool.progress"]
    assert progress[0]["server"] == "fake"
    assert progress[0]["tool"] == "slow"
    assert progress[0]["total"] == 3


async def test_a_new_tool_the_server_announces_is_picked_up(session: _Session) -> None:
    await session.registry.execute("mcp__fake__grow", {}, session.context())

    await session.until(lambda: session.registry.has("mcp__fake__extra"))


async def test_server_logs_are_kept_and_published(session: _Session) -> None:
    await session.registry.execute(
        "mcp__fake__shout", {"text": "disk almost full"}, session.context()
    )

    await session.until(lambda: any(e.event_type == "mcp.server.log" for e in session.events))
    assert "[warning] disk almost full" in session.manager.servers["fake"].logs


async def test_a_failed_call_is_a_tool_error(session: _Session) -> None:
    with pytest.raises(ToolExecutionError, match="it broke"):
        await session.registry.execute("mcp__fake__fail", {}, session.context())


async def test_a_crashed_server_is_restarted_and_its_tools_come_back(session: _Session) -> None:
    with pytest.raises(ToolExecutionError):
        await session.registry.execute("mcp__fake__crash", {}, session.context())

    await session.until(lambda: session.status("fake") == "failed")
    assert "exited with code 3" in session.manager.servers["fake"].error
    await session.until(lambda: session.status("fake") == "connected")
    assert session.registry.has("mcp__fake__echo")


async def test_closing_takes_the_tools_away(session: _Session) -> None:
    await session.manager.close()

    assert not session.registry.has("mcp__fake__echo")
    assert not session.registry.has("list_mcp_resources")


async def test_status_events_never_carry_env_or_header_values(tmp_path) -> None:
    # A name the event bus's own redaction does not recognise as a secret.
    live = _Session(tmp_path, mcp_servers={"s": _server(env={"REGION_LABEL": "hunter2"})})
    await live.manager.start()
    await live.until(lambda: live.status("s") != "connecting")
    await live.manager.close()

    statuses = [e.payload for e in live.events if e.event_type == "mcp.server.status"]
    assert statuses
    assert all(status["spec"]["env"] == {"REGION_LABEL": "***"} for status in statuses)
    assert "hunter2" not in json.dumps([e.payload for e in live.events])
    assert live.manager.snapshot(secrets=True)[0]["spec"]["env"] == {"REGION_LABEL": "hunter2"}


async def test_many_tools_wait_in_a_group(tmp_path) -> None:
    live = _Session(tmp_path, mcp_servers={"big": _server("--bare", "--many", "20")})
    await live.manager.start()
    await live.until(lambda: live.status("big") != "connecting")
    try:
        group = group_named("mcp:big")
        assert group is not None
        assert group_of("mcp__big__echo") is group
        assert "mcp:big: Fake Server - tools: echo, add, slow" in render_catalog()
        assert "tools in the group mcp:big" in live.manager.prompt_catalog()
        assert not live.registry.has("list_mcp_resources")
    finally:
        await live.manager.close()

    assert group_named("mcp:big") is None


async def test_a_trusted_servers_read_only_tools_skip_approval(tmp_path) -> None:
    live = _Session(tmp_path, mcp_servers={"t": _server("--bare", trust=True)})
    await live.manager.start()
    await live.until(lambda: live.status("t") != "connecting")
    try:
        assert live.registry.tool("mcp__t__echo").approval_exempt
        assert not live.registry.tool("mcp__t__add").approval_exempt
    finally:
        await live.manager.close()


async def test_a_server_that_keeps_dying_is_given_up_on(tmp_path) -> None:
    live = _Session(tmp_path, mcp_servers={"bad": _server("--exit-after-init")})
    await live.manager.start()
    try:
        await live.until(
            lambda: live.status("bad") == "failed" and live.manager.servers["bad"].task.done()
        )
        assert "giving up on purpose" in live.manager.servers["bad"].error
    finally:
        await live.manager.close()


async def test_a_workspace_server_waits_for_approval(tmp_path) -> None:
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"repo": _server("--bare")}}), encoding="utf-8"
    )
    live = _Session(tmp_path)
    await live.manager.start()

    assert live.status("repo") == "needs_approval"
    assert not live.registry.has("mcp__repo__echo")

    await live.manager.approve("repo")
    await live.until(lambda: live.status("repo") != "connecting")
    try:
        fingerprint = live.manager.servers["repo"].config.fingerprint(live.config.workspace)
        assert live.saved[-1] == {"mcp_approved": [fingerprint]}
        assert live.status("repo") == "connected"
        assert live.registry.has("mcp__repo__echo")
    finally:
        await live.manager.close()


async def test_switching_off_adding_and_removing_servers(tmp_path) -> None:
    live = _Session(tmp_path, mcp_servers={"one": _server("--bare")})
    await live.manager.start()
    await live.until(lambda: live.status("one") != "connecting")
    try:
        await live.manager.set_enabled("one", False)
        assert live.status("one") == "disabled"
        assert not live.registry.has("mcp__one__echo")
        assert live.saved[-1]["mcp_servers"]["one"]["enabled"] is False

        await live.manager.upsert({"name": "two", **_server("--bare")})
        await live.until(lambda: live.status("two") != "connecting")
        assert live.status("two") == "connected"
        assert "two" in live.config.mcp_servers

        await live.manager.remove("two")
        assert "two" not in live.manager.servers
        assert "two" not in live.config.mcp_servers
        assert not live.registry.has("mcp__two__echo")
    finally:
        await live.manager.close()


async def test_a_broken_declaration_is_listed_as_invalid(tmp_path) -> None:
    live = _Session(tmp_path, mcp_servers={"broken": {"type": "ws", "url": "ws://x"}})
    await live.manager.start()

    assert live.status("broken") == "invalid"
    with pytest.raises(ValueError):
        await live.manager.upsert({"name": "worse", "type": "http", "url": "ftp://x"})


async def test_nothing_starts_when_mcp_is_off(tmp_path) -> None:
    live = _Session(tmp_path, mcp_enabled=False, mcp_servers={"fake": _server()})
    await live.manager.start()

    assert live.manager.servers == {}


class _StubProvider:
    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(streaming=True, tool_calling=True)

    async def stream(self, request):  # noqa: ANN001
        yield ProviderEvent(kind="completed", response=ModelResponse(text=""))

    async def complete(self, request):  # noqa: ANN001
        return ModelResponse(text="")

    async def close(self) -> None:
        return None


def test_mcp_tools_ask_first_unless_trusted_and_read_only(tmp_path) -> None:
    app = build_application(
        config=_config(tmp_path, permission_mode="ask"), provider=_StubProvider()
    )
    registry = app.orchestrator.tool_registry
    registry.register(McpTool(manager=None, server="s", spec={"name": "write"}, trusted=True))
    registry.register(
        McpTool(
            manager=None,
            server="s",
            spec={"name": "look", "annotations": {"readOnlyHint": True}},
            trusted=True,
        )
    )

    assert app.orchestrator._requires_user_approval("mcp__s__write", policy_allowed=True)
    assert not app.orchestrator._requires_user_approval("mcp__s__look", policy_allowed=True)


async def test_the_session_starts_its_servers_and_tells_the_model(tmp_path) -> None:
    config = _config(
        tmp_path,
        mcp_servers={"fake": _server("--bare")},
        index={"enabled": False},
        memories_dir=str(tmp_path / "memories"),
        memory={"reflection_enabled": False},
    )
    app = build_application(config=config, provider=_StubProvider())
    await app.start()
    try:
        state = app.mcp.servers["fake"]
        deadline = asyncio.get_running_loop().time() + 20
        while state.status == "connecting" and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.05)

        assert state.status == "connected"
        assert app.orchestrator.tool_registry.has("mcp__fake__echo")
        prompt = app.orchestrator._system_prompt((), ())
        assert "MCP servers connected to this session" in prompt
        assert "- fake (Fake Server)" in prompt
    finally:
        await app.close()

    assert not app.orchestrator.tool_registry.has("mcp__fake__echo")


async def test_closing_does_not_wait_on_a_server_stuck_in_its_own_cancellation(
    tmp_path, monkeypatch
) -> None:
    # What a Windows spawn does when cancelled mid-flight: the first cancel
    # lands in a cleanup that waits on the child forever, and only a second
    # one gets out of it. One cancel and a bare await held shutdown hostage.
    monkeypatch.setattr(manager_module, "_STOP_GRACE_S", 0.1)
    live = _Session(tmp_path, mcp_servers={"fake": _server()})
    cancels = 0

    async def stuck_connect(state) -> None:
        nonlocal cancels
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancels += 1
            await asyncio.Event().wait()

    monkeypatch.setattr(live.manager, "_connect", stuck_connect)
    await live.manager.start()
    await asyncio.sleep(0.05)

    await asyncio.wait_for(live.manager.close(), timeout=5)

    assert cancels == 1
    assert live.manager.servers["fake"].task is None


async def test_closing_gives_up_on_a_server_that_ignores_every_cancel(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(manager_module, "_STOP_GRACE_S", 0.1)
    live = _Session(tmp_path, mcp_servers={"fake": _server()})
    release = asyncio.Event()

    async def deaf_connect(state) -> None:
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                continue

    monkeypatch.setattr(live.manager, "_connect", deaf_connect)
    await live.manager.start()
    task = live.manager.servers["fake"].task
    await asyncio.sleep(0.05)

    await asyncio.wait_for(live.manager.close(), timeout=5)

    assert not task.done()
    release.set()
    await asyncio.wait({task}, timeout=2)
