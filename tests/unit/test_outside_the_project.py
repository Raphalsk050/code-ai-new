"""The file tools reach files outside the project by absolute path, each change approved."""

from __future__ import annotations

import asyncio

import pytest

from code_ai.bootstrap import build_application
from code_ai.config.models import AppConfig
from code_ai.core.errors import WorkspaceBoundaryError
from code_ai.events.bus import AsyncEventBus
from code_ai.providers.models import ProviderCapabilities, ToolCall
from code_ai.tools.base import ToolContext
from code_ai.tools.filesystem import EditCodeTool, ReadFileTool, WriteFileTool
from code_ai.tools.locations import ToolLocation, for_context, reaches_outside
from code_ai.util.paths import WorkspacePolicy


def _context(project) -> ToolContext:
    config = AppConfig.from_mapping({"api_mode": "ollama", "workspace": str(project)})
    return ToolContext(
        config=config,
        workspace=WorkspacePolicy.from_path(project),
        event_bus=AsyncEventBus(session_id="session"),
        cancel_event=asyncio.Event(),
    )


@pytest.fixture
def places(tmp_path):
    project = tmp_path / "project"
    elsewhere = tmp_path / "elsewhere"
    project.mkdir()
    elsewhere.mkdir()
    return project, elsewhere


def test_only_an_absolute_path_that_lands_outside_reaches_out(places) -> None:
    project, elsewhere = places
    assert reaches_outside(project, str(elsewhere / "a.txt"))
    assert not reaches_outside(project, str(project / "a.txt"))
    assert not reaches_outside(project, "a.txt")
    assert not reaches_outside(project, "../elsewhere/a.txt")
    assert not reaches_outside(project, None)


def test_a_relative_path_still_cannot_leave(places) -> None:
    project, _ = places
    location = for_context(_context(project))
    with pytest.raises(WorkspaceBoundaryError):
        location.resolve_reaching_out("../elsewhere/a.txt")


async def test_write_edit_and_read_a_file_outside_the_project(places) -> None:
    project, elsewhere = places
    target = elsewhere / "settings.ini"
    context = _context(project)

    written = await WriteFileTool().execute({"path": str(target), "content": "a = 1\n"}, context)
    assert target.read_text(encoding="utf-8") == "a = 1\n"
    assert written["path"] == target.resolve().as_posix()

    await EditCodeTool().execute(
        {"path": str(target), "old_text": "a = 1", "new_text": "a = 2"}, context
    )
    assert target.read_text(encoding="utf-8") == "a = 2\n"

    read = await ReadFileTool().execute({"path": str(target)}, context)
    assert "a = 2" in str(read)


def _app(project, mode: str):
    class _Idle:
        @property
        def capabilities(self) -> ProviderCapabilities:
            return ProviderCapabilities(streaming=True, tools=True, image_support=False)

        async def stream(self, request):  # noqa: ANN001 - unused here
            raise AssertionError("no model call expected")

    config = AppConfig.from_mapping(
        {
            "api_mode": "ollama",
            "workspace": str(project),
            "model": "fake",
            "permission_mode": mode,
            "memory": {"reflection_enabled": False},
        }
    )
    return build_application(config=config, provider=_Idle()).orchestrator


def _needs_approval(orchestrator, name: str, path: str, **extra) -> bool:
    call = ToolCall(id="c", name=name, arguments={"path": path, **extra})
    outside = orchestrator._outside_target(call)
    return orchestrator._requires_user_approval(
        name, policy_allowed=True, outside=outside is not None
    )


@pytest.mark.parametrize(
    ("mode", "tool", "asks"),
    [
        ("ask", "write_file", True),
        ("ask", "edit_code", True),
        ("ask", "read_file", True),
        ("auto", "write_file", True),
        ("auto", "edit_code", True),
        ("auto", "read_file", False),
        ("bypass", "write_file", False),
    ],
)
def test_outside_the_project_the_user_approves(places, mode, tool, asks) -> None:
    project, elsewhere = places
    orchestrator = _app(project, mode)
    assert _needs_approval(orchestrator, tool, str(elsewhere / "x.txt")) is asks


def test_inside_the_project_nothing_changes(places) -> None:
    project, _ = places
    assert not _needs_approval(_app(project, "auto"), "write_file", "src/x.py")
    assert not _needs_approval(_app(project, "ask"), "read_file", str(project / "x.py"))


def test_the_sandbox_never_reaches_out(places) -> None:
    project, elsewhere = places
    orchestrator = _app(project, "auto")
    call = ToolCall(
        id="c",
        name="write_file",
        arguments={"path": str(elsewhere / "x.txt"), "location": ToolLocation.SANDBOX.value},
    )
    assert orchestrator._outside_target(call) is None
