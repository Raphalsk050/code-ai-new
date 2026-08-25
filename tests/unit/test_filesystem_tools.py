from __future__ import annotations

import asyncio

import pytest

from code_ai.config.models import AppConfig
from code_ai.core.errors import ToolExecutionError, WorkspaceBoundaryError
from code_ai.events.bus import AsyncEventBus
from code_ai.tools.base import ToolContext
from code_ai.tools.filesystem import EditCodeTool, ListFilesTool, ReadFileTool, WriteFileTool
from code_ai.tools.search import SearchCodeTool
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
    try:
        link.symlink_to(outside)
    except OSError as exc:  # Windows grants this only under Developer Mode/admin
        pytest.skip(f"creating a symlink is not permitted here: {exc}")
    context = make_context(tmp_path)
    with pytest.raises(WorkspaceBoundaryError):
        context.workspace.resolve("link.txt", must_exist=True)


async def test_write_read_and_edit_code_use_minimal_schema(tmp_path) -> None:
    context = make_context(tmp_path)
    write = WriteFileTool()
    read = ReadFileTool()
    edit = EditCodeTool()

    assert set(write.input_schema["properties"]) == {"path", "content", "reason", "location"}
    # strict-mode requires every declared property (even nullable ones) in "required".
    assert set(write.input_schema["required"]) == {"path", "content", "reason", "location"}
    # No hash/occurrence guards exposed: strict mode would force the model to
    # emit them every call, and a stale value aborts otherwise-valid edits.
    assert set(edit.input_schema["properties"]) == {
        "path",
        "old_text",
        "new_text",
        "reason",
        "location",
    }
    assert "expected_sha256" not in edit.input_schema["properties"]
    assert "expected_occurrences" not in edit.input_schema["properties"]
    assert "edits" not in edit.input_schema["properties"]

    written = await write.execute(
        {"path": "src/app.py", "content": "print('old')\n"},
        context,
    )
    assert written["new_sha256"]

    readback = await read.execute({"path": "src/app.py"}, context)
    assert readback["sha256"] == written["new_sha256"]
    assert "print('old')" in readback["content"]

    edited = await edit.execute(
        {
            "path": "src/app.py",
            "old_text": "old",
            "new_text": "new",
        },
        context,
    )
    assert edited["changed"]
    assert "-print('old')" in edited["diff"]
    assert "+print('new')" in edited["diff"]


async def test_edit_code_keeps_legacy_edits_compatibility(tmp_path) -> None:
    context = make_context(tmp_path)
    path = tmp_path / "a.txt"
    path.write_text("one two\n", encoding="utf-8")

    edited = await EditCodeTool().execute(
        {"path": "a.txt", "edits": [{"old": "two", "new": "three"}]},
        context,
    )

    assert edited["changed"]
    assert path.read_text(encoding="utf-8") == "one three\n"


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


async def test_list_files_is_bounded_sorted_and_skips_default_excludes(tmp_path) -> None:
    context = make_context(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "b.py").write_text("print('b')\n", encoding="utf-8")
    (tmp_path / "src" / "a.py").write_text("print('a')\n", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("secret\n", encoding="utf-8")

    listed = await ListFilesTool().execute(
        {"path": ".", "max_depth": 2, "max_entries": 10},
        context,
    )

    paths = [entry["path"] for entry in listed["entries"]]
    assert paths == sorted(paths, key=str.casefold)
    assert "src/a.py" in paths
    assert "src/b.py" in paths
    assert not any(path.startswith(".git") for path in paths)


async def test_search_code_finds_bounded_matches(tmp_path) -> None:
    context = make_context(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text(
        "def answer():\n    return 42\n",
        encoding="utf-8",
    )

    result = await SearchCodeTool().execute(
        {
            "query": "return 42",
            "path": "src",
            "include_globs": ["*.py", "src/*.py"],
            "max_matches": 5,
        },
        context,
    )

    assert result["matches"]
    assert result["matches"][0]["path"] == "src/app.py"
    assert result["matches"][0]["line"] == 2


def test_rg_line_parses_a_path_that_contains_a_colon(tmp_path) -> None:
    # Regression: matches were split on ":", so on Windows the drive letter
    # became the path and the rest of the path became the line number. int()
    # then raised, the except swallowed it, and every single match was dropped -
    # search_code answered [] for any query whenever rg was installed.
    from code_ai.tools.search.search_code import _parse_rg_line

    hit = tmp_path / "src" / "app.py"
    line = f"{hit}\x002:    return 42"

    parsed = _parse_rg_line(line, workspace_root=tmp_path, max_output_chars=600)

    assert parsed is not None
    assert parsed["path"] == "src/app.py"
    assert parsed["line"] == 2
    assert parsed["snippet"].strip() == "return 42"


def test_rg_line_falls_back_to_the_colon_form(tmp_path) -> None:
    # An rg build that ignores --null must not cost us every result either.
    from code_ai.tools.search.search_code import _parse_rg_line

    hit = tmp_path / "src" / "app.py"
    parsed = _parse_rg_line(
        f"{hit}:7:    value = {{}}", workspace_root=tmp_path, max_output_chars=600
    )

    assert parsed is not None
    assert parsed["path"] == "src/app.py"
    assert parsed["line"] == 7


def test_rg_line_rejects_a_non_match_line(tmp_path) -> None:
    from code_ai.tools.search.search_code import _parse_rg_line

    assert _parse_rg_line("", workspace_root=tmp_path, max_output_chars=600) is None
    assert _parse_rg_line("nonsense", workspace_root=tmp_path, max_output_chars=600) is None
