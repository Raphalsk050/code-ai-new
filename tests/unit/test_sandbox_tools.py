from __future__ import annotations

import asyncio
import json
import shlex
import sys

import pytest

from code_ai.config.models import AppConfig
from code_ai.core.errors import ToolArgumentError, ToolExecutionError, WorkspaceBoundaryError
from code_ai.events.bus import AsyncEventBus
from code_ai.sandbox.session import SessionSandbox
from code_ai.tools.base import ToolContext
from code_ai.tools.filesystem import ListFilesTool, ReadFileTool, WriteFileTool
from code_ai.tools.locations import ToolLocation, for_context, resolve_location
from code_ai.tools.process import ExecuteCommandTool
from code_ai.tools.terminal import StartTerminalTool
from code_ai.util.paths import WorkspacePolicy


def make_context(tmp_path, *, with_sandbox: bool = True) -> ToolContext:
    workspace = tmp_path / "project"
    workspace.mkdir(exist_ok=True)
    config = AppConfig.from_mapping({"api_mode": "ollama", "workspace": str(workspace)})
    sandbox = (
        SessionSandbox.create(
            session_id="session-under-test",
            workspace=workspace,
            base_dir=tmp_path / "sandboxes",
        )
        if with_sandbox
        else None
    )
    return ToolContext(
        config=config,
        workspace=WorkspacePolicy.from_path(workspace),
        event_bus=AsyncEventBus(session_id="session"),
        cancel_event=asyncio.Event(),
        sandbox=sandbox,
    )


def project_entries(context: ToolContext) -> set[str]:
    return {path.name for path in context.workspace.root.iterdir()}


# ----------------------------------------------------------------- routing


def test_an_absent_location_means_the_project() -> None:
    assert resolve_location(None) is ToolLocation.PROJECT
    assert resolve_location("null") is ToolLocation.PROJECT
    assert resolve_location("  ") is ToolLocation.PROJECT
    assert resolve_location("SANDBOX") is ToolLocation.SANDBOX


def test_an_unknown_location_is_rejected() -> None:
    with pytest.raises(ToolArgumentError):
        resolve_location("elsewhere")
    with pytest.raises(ToolArgumentError):
        resolve_location(7)


def test_asking_for_a_sandbox_that_does_not_exist_fails_loudly(tmp_path) -> None:
    context = make_context(tmp_path, with_sandbox=False)

    # Silently writing to the project instead is the exact accident the
    # sandbox exists to prevent.
    with pytest.raises(ToolExecutionError):
        for_context(context, "sandbox")


def test_the_sandbox_default_workdir_is_the_work_area(tmp_path) -> None:
    context = make_context(tmp_path)

    location = for_context(context, "sandbox")

    assert location.workdir(None) == context.sandbox.layout.work
    assert location.is_sandbox is True


# ------------------------------------------------------------------- files


async def test_a_sandbox_write_never_reaches_the_project(tmp_path) -> None:
    context = make_context(tmp_path)
    before = project_entries(context)

    result = await WriteFileTool().execute(
        {"path": "work/scratch.py", "content": "print(1)\n", "location": "sandbox"},
        context,
    )

    assert result["location"] == "sandbox"
    assert result["path"] == "work/scratch.py"
    assert (context.sandbox.root / "work" / "scratch.py").read_text(encoding="utf-8")
    assert project_entries(context) == before


async def test_a_write_without_a_location_still_goes_to_the_project(tmp_path) -> None:
    context = make_context(tmp_path)

    result = await WriteFileTool().execute({"path": "app.py", "content": "x\n"}, context)

    assert result["location"] == "project"
    assert (context.workspace.root / "app.py").exists()


async def test_a_sandbox_write_cannot_escape_into_the_project(tmp_path) -> None:
    context = make_context(tmp_path)

    with pytest.raises(WorkspaceBoundaryError):
        await WriteFileTool().execute(
            {
                "path": str(context.workspace.root / "injected.py"),
                "content": "x",
                "location": "sandbox",
            },
            context,
        )

    assert not (context.workspace.root / "injected.py").exists()


async def test_the_sandbox_can_be_read_and_listed_back(tmp_path) -> None:
    context = make_context(tmp_path)
    await WriteFileTool().execute(
        {"path": "work/notes.txt", "content": "hello\n", "location": "sandbox"}, context
    )

    read = await ReadFileTool().execute(
        {"path": "work/notes.txt", "location": "sandbox"}, context
    )
    listed = await ListFilesTool().execute({"path": "work", "location": "sandbox"}, context)

    assert read["content"] == "hello\n"
    assert read["location"] == "sandbox"
    assert any(entry["path"] == "work/notes.txt" for entry in listed["entries"])


# --------------------------------------------------------------- commands


async def test_a_sandboxed_command_runs_in_the_sandbox_work_area(tmp_path) -> None:
    context = make_context(tmp_path)
    script = "import os; print(os.getcwd())"

    result = await ExecuteCommandTool().execute(
        {
            "command": f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}",
            "location": "sandbox",
        },
        context,
    )

    assert result["exit_code"] == 0
    assert result["stdout"].strip() == str(context.sandbox.layout.work)
    assert result["location"] == "sandbox"


async def test_a_project_command_still_runs_in_the_project(tmp_path) -> None:
    context = make_context(tmp_path)
    script = "import os; print(os.getcwd())"

    result = await ExecuteCommandTool().execute(
        {"command": f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}"}, context
    )

    assert result["stdout"].strip() == str(context.workspace.root)
    assert result["location"] == "project"


async def test_a_project_command_writes_its_temp_files_into_the_sandbox(tmp_path) -> None:
    context = make_context(tmp_path)
    script = "import tempfile; print(tempfile.gettempdir())"

    result = await ExecuteCommandTool().execute(
        {"command": f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}"}, context
    )

    assert result["stdout"].strip() == str(context.sandbox.layout.tmp)


async def test_bytecode_from_a_project_run_lands_in_the_sandbox(tmp_path) -> None:
    context = make_context(tmp_path)
    (context.workspace.root / "module_under_test.py").write_text("VALUE = 1\n", encoding="utf-8")
    script = "import module_under_test; print(module_under_test.VALUE)"

    await ExecuteCommandTool().execute(
        {"command": f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}"}, context
    )

    assert not (context.workspace.root / "__pycache__").exists()
    cached = list((context.sandbox.layout.cache / "python" / "bytecode").rglob("*.pyc"))
    assert cached


async def test_an_explicit_env_still_wins_over_the_redirection(tmp_path) -> None:
    context = make_context(tmp_path)
    script = "import os; print(os.environ['TMPDIR'])"

    result = await ExecuteCommandTool().execute(
        {
            "command": f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}",
            "env": {"TMPDIR": "/deliberate"},
        },
        context,
    )

    assert result["stdout"].strip() == "/deliberate"


async def test_a_run_is_captured_as_a_structured_artifact(tmp_path) -> None:
    context = make_context(tmp_path)
    script = "import sys; print('out'); print('err', file=sys.stderr)"

    result = await ExecuteCommandTool().execute(
        {"command": f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}"}, context
    )

    artifacts = result["artifacts"]
    root = context.sandbox.root
    assert (root / artifacts["stdout"]).read_text(encoding="utf-8").strip() == "out"
    assert (root / artifacts["stderr"]).read_text(encoding="utf-8").strip() == "err"
    summary = json.loads((root / artifacts["result"]).read_text(encoding="utf-8"))
    assert summary["exit_code"] == 0
    assert summary["location"] == "project"
    assert artifacts["root"] == str(root)


async def test_a_failing_sandbox_command_leaves_the_project_untouched(tmp_path) -> None:
    context = make_context(tmp_path)
    before = project_entries(context)
    script = (
        "import pathlib, sys; "
        "pathlib.Path('half-written.txt').write_text('partial'); "
        "sys.exit(3)"
    )

    result = await ExecuteCommandTool().execute(
        {
            "command": f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}",
            "location": "sandbox",
        },
        context,
    )

    assert result["exit_code"] == 3
    assert project_entries(context) == before
    assert (context.sandbox.layout.work / "half-written.txt").exists()


async def test_commands_work_without_a_sandbox(tmp_path) -> None:
    context = make_context(tmp_path, with_sandbox=False)

    result = await ExecuteCommandTool().execute(
        {"command": f"{shlex.quote(sys.executable)} -c {shlex.quote('print(1)')}"}, context
    )

    assert result["exit_code"] == 0
    assert "artifacts" not in result


# --------------------------------------------------------------- terminals


class RecordingTerminalManager:
    def __init__(self) -> None:
        self.created: dict[str, object] = {}

    def create(self, *, cwd, command=None, rows: int = 24, cols: int = 80, env=None) -> str:
        self.created = {"cwd": cwd, "command": command, "env": env}
        return "term-1"

    def read_screen(self, session_id: str, *, include_cursor: bool = True) -> dict[str, object]:
        return {"session_id": session_id, "rows": 24, "columns": 80, "screen": ""}


async def test_a_sandboxed_terminal_starts_in_the_sandbox(tmp_path) -> None:
    context = make_context(tmp_path)
    manager = RecordingTerminalManager()
    context.terminal_manager = manager

    await StartTerminalTool().execute({"location": "sandbox"}, context)

    assert manager.created["cwd"] == context.sandbox.layout.work


async def test_a_project_terminal_still_inherits_the_redirection(tmp_path) -> None:
    context = make_context(tmp_path)
    manager = RecordingTerminalManager()
    context.terminal_manager = manager

    await StartTerminalTool().execute({}, context)

    env = manager.created["env"]
    assert manager.created["cwd"] == context.workspace.root
    assert env["TMPDIR"] == str(context.sandbox.layout.tmp)
    # A shell needs its whole environment, not just the redirection.
    assert "PATH" in env


async def test_a_terminal_without_a_sandbox_inherits_the_parent_environment(tmp_path) -> None:
    context = make_context(tmp_path, with_sandbox=False)
    manager = RecordingTerminalManager()
    context.terminal_manager = manager

    await StartTerminalTool().execute({}, context)

    assert manager.created["env"] is None
