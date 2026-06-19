from __future__ import annotations

import asyncio

import pytest

from code_ai.config.models import AppConfig
from code_ai.core.errors import ToolExecutionError, WorkspaceBoundaryError
from code_ai.events.bus import AsyncEventBus
from code_ai.tools.base import ToolContext
from code_ai.tools.filesystem import EditCodeTool, ReadFileTool, WriteFileTool
from code_ai.util.paths import WorkspacePolicy


def make_context(tmp_path) -> ToolContext:
    config = AppConfig.from_mapping({"api_mode": "ollama", "workspace": str(tmp_path)})
    return ToolContext(
        config=config,
        workspace=WorkspacePolicy.from_path(tmp_path),
        event_bus=AsyncEventBus(session_id="session"),
        cancel_event=asyncio.Event(),
    )


async def test_workspace_rejects_traversal(tmp_path) -> None:
    context = make_context(tmp_path)
    with pytest.raises(WorkspaceBoundaryError):
        context.workspace.resolve("../outside.txt")


async def test_workspace_rejects_symlink_escape(tmp_path) -> None:
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    link = tmp_path / "link.txt"
    link.symlink_to(outside)
    context = make_context(tmp_path)
    with pytest.raises(WorkspaceBoundaryError):
        context.workspace.resolve("link.txt", must_exist=True)


async def test_write_read_and_edit_code_are_hash_guarded(tmp_path) -> None:
    context = make_context(tmp_path)
    write = WriteFileTool()
    read = ReadFileTool()
    edit = EditCodeTool()

    written = await write.execute(
        {"path": "src/app.py", "content": "print('old')\n", "expected_new_file": True},
        context,
    )
    assert written["new_sha256"]

    readback = await read.execute({"path": "src/app.py"}, context)
    assert readback["sha256"] == written["new_sha256"]
    assert "print('old')" in readback["content"]

    edited = await edit.execute(
        {
            "path": "src/app.py",
            "expected_sha256": readback["sha256"],
            "edits": [{"old": "old", "new": "new"}],
        },
        context,
    )
    assert edited["changed"]
    assert "-print('old')" in edited["diff"]
    assert "+print('new')" in edited["diff"]


async def test_edit_code_failure_leaves_original_file_intact(tmp_path) -> None:
    context = make_context(tmp_path)
    path = tmp_path / "a.txt"
    path.write_text("one two\n", encoding="utf-8")
    edit = EditCodeTool()
    with pytest.raises(ToolExecutionError):
        await edit.execute(
            {
                "path": "a.txt",
                "edits": [{"old": "missing", "new": "value"}],
            },
            context,
        )
    assert path.read_text(encoding="utf-8") == "one two\n"
