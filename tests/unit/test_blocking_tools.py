from __future__ import annotations

import threading
from pathlib import Path

import pytest

from code_ai.config.models import AppConfig
from code_ai.core.errors import ToolExecutionError
from code_ai.events.bus import AsyncEventBus
from code_ai.tools.base import ToolContext
from code_ai.tools.filesystem import EditCodeTool, ListFilesTool, ReadFileTool
from code_ai.util.paths import WorkspacePolicy

# The orchestrator runs as a task on the same event loop as the terminal UI,
# and its wall-clock backstop for a tool is cooperative: it polls with
# asyncio.wait. A tool that does its filesystem work on that loop therefore
# freezes the screen, the cancel key and its own timeout together - the hang
# these tests exist to catch. Asserting which thread the work lands in is
# deterministic; timing the loop would not be.


def make_context(tmp_path) -> ToolContext:
    import asyncio

    config = AppConfig.from_mapping({"api_mode": "ollama", "workspace": str(tmp_path)})
    return ToolContext(
        config=config,
        workspace=WorkspacePolicy.from_path(tmp_path),
        event_bus=AsyncEventBus(session_id="session"),
        cancel_event=asyncio.Event(),
    )


def thread_spy(recorded: list[int], wrapped):
    def spy(*args, **kwargs):
        recorded.append(threading.get_ident())
        return wrapped(*args, **kwargs)

    return spy


async def test_read_file_reads_off_the_event_loop(tmp_path, monkeypatch) -> None:
    from code_ai.tools.filesystem import read_file as module

    context = make_context(tmp_path)
    (tmp_path / "a.txt").write_text("one\ntwo\n", encoding="utf-8")
    seen: list[int] = []
    monkeypatch.setattr(module, "read_text_file", thread_spy(seen, module.read_text_file))

    await ReadFileTool().execute({"path": "a.txt"}, context)

    assert seen and threading.get_ident() not in seen


async def test_edit_code_reads_off_the_event_loop(tmp_path, monkeypatch) -> None:
    from code_ai.tools.filesystem import edit_code as module

    context = make_context(tmp_path)
    (tmp_path / "a.txt").write_text("one two\n", encoding="utf-8")
    seen: list[int] = []
    monkeypatch.setattr(module, "read_text_file", thread_spy(seen, module.read_text_file))

    await EditCodeTool().execute({"path": "a.txt", "old_text": "two", "new_text": "three"}, context)

    assert seen and threading.get_ident() not in seen


async def test_list_files_walks_off_the_event_loop(tmp_path, monkeypatch) -> None:
    context = make_context(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("print('a')\n", encoding="utf-8")
    seen: list[int] = []
    monkeypatch.setattr(Path, "iterdir", thread_spy(seen, Path.iterdir))

    await ListFilesTool().execute({"path": "."}, context)

    assert seen and threading.get_ident() not in seen


async def test_search_code_python_fallback_runs_off_the_event_loop(tmp_path, monkeypatch) -> None:
    from code_ai.tools.search import search_code as module

    context = make_context(tmp_path)
    (tmp_path / "a.py").write_text("needle\n", encoding="utf-8")
    monkeypatch.setattr(module.shutil, "which", lambda name: None)
    seen: list[int] = []
    monkeypatch.setattr(module, "_python_search", thread_spy(seen, module._python_search))

    result = await module.SearchCodeTool().execute({"query": "needle"}, context)

    assert result["matches"]
    assert seen and threading.get_ident() not in seen


async def test_read_file_refuses_a_file_too_large_to_read_whole(tmp_path, monkeypatch) -> None:
    from code_ai.tools.filesystem import common

    context = make_context(tmp_path)
    (tmp_path / "big.log").write_text("x" * 4096, encoding="utf-8")
    monkeypatch.setattr(common, "MAX_TEXT_FILE_BYTES", 1024)

    with pytest.raises(ToolExecutionError, match="too large"):
        await ReadFileTool().execute({"path": "big.log"}, context)


async def test_read_file_honours_the_line_range_it_advertises(tmp_path) -> None:
    context = make_context(tmp_path)
    (tmp_path / "a.txt").write_text("one\ntwo\nthree\nfour\n", encoding="utf-8", newline="\n")
    read = ReadFileTool()

    # Declared, so the model can bound a read instead of paying for the whole
    # file on every call.
    assert {"start_line", "end_line"} <= set(read.input_schema["properties"])

    result = await read.execute({"path": "a.txt", "start_line": 2, "end_line": 3}, context)

    assert result["content"] == "two\nthree\n"
    assert result["start_line"] == 2
    assert result["end_line"] == 3


async def test_search_code_skips_a_file_too_large_to_scan(tmp_path, monkeypatch) -> None:
    from code_ai.tools.search import search_code as module

    context = make_context(tmp_path)
    (tmp_path / "small.py").write_text("needle\n", encoding="utf-8")
    (tmp_path / "bundle.js").write_text("needle" + "x" * 4096, encoding="utf-8")
    monkeypatch.setattr(module.shutil, "which", lambda name: None)
    monkeypatch.setattr(module, "MAX_TEXT_FILE_BYTES", 1024)

    result = await module.SearchCodeTool().execute({"query": "needle"}, context)

    assert [match["path"] for match in result["matches"]] == ["small.py"]
