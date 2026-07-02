from __future__ import annotations

import asyncio

import pytest

from code_ai.config.models import AppConfig
from code_ai.core.errors import ToolArgumentError, ToolExecutionError
from code_ai.events.bus import AsyncEventBus
from code_ai.tools.base import ToolCapability, ToolContext
from code_ai.tools.logcat import AnalyzeLogcatTool
from code_ai.util.paths import WorkspacePolicy

FATAL_JAVA = (
    "06-01 12:00:00.100  4321  4321 E AndroidRuntime: FATAL EXCEPTION: main\n"
    "06-01 12:00:00.100  4321  4321 E AndroidRuntime: Process: com.acme.app, PID: 4321\n"
    "06-01 12:00:00.100  4321  4321 E AndroidRuntime: java.lang.RuntimeException: boom\n"
    "06-01 12:00:00.100  4321  4321 E AndroidRuntime: Caused by:"
    " java.lang.NullPointerException: name is null\n"
    "06-01 12:00:00.100  4321  4321 E AndroidRuntime: \tat com.acme.app.Main.onCreate"
    "(Main.java:42)\n"
)


def make_context(tmp_path) -> ToolContext:
    config = AppConfig.from_mapping({"api_mode": "ollama", "workspace": str(tmp_path)})
    return ToolContext(
        config=config,
        workspace=WorkspacePolicy.from_path(tmp_path),
        event_bus=AsyncEventBus(session_id="session"),
        cancel_event=asyncio.Event(),
    )


def test_tool_declares_read_and_process_capabilities() -> None:
    tool = AnalyzeLogcatTool()
    assert ToolCapability.PROCESS in tool.capabilities
    assert ToolCapability.LOCAL_READ in tool.capabilities
    assert tool.input_schema["required"] == list(tool.input_schema["properties"].keys())


async def test_inline_source_returns_structured_report(tmp_path) -> None:
    tool = AnalyzeLogcatTool()
    result = await tool.execute({"source": "inline", "content": FATAL_JAVA}, make_context(tmp_path))

    assert result["source"] == "inline"
    assert result["crash_count"] == 1
    group = result["groups"][0]
    assert group["kind"] == "java_exception"
    assert group["severity"] == "critical"
    assert "NullPointerException" in group["root_cause"]
    assert result["input_truncated"] is False


async def test_source_inferred_from_content(tmp_path) -> None:
    tool = AnalyzeLogcatTool()
    result = await tool.execute({"content": FATAL_JAVA}, make_context(tmp_path))
    assert result["source"] == "inline"
    assert result["crash_count"] == 1


async def test_file_source_reads_workspace_log(tmp_path) -> None:
    (tmp_path / "crash.log").write_text(FATAL_JAVA, encoding="utf-8")
    tool = AnalyzeLogcatTool()
    result = await tool.execute({"source": "file", "path": "crash.log"}, make_context(tmp_path))
    assert result["source"] == "file:crash.log"
    assert result["crash_count"] == 1


async def test_file_source_rejects_binary(tmp_path) -> None:
    (tmp_path / "bugreport.zip").write_bytes(b"PK\x03\x04\x00\x00\x00\x00\x00\x00\x00\x00\x00")
    tool = AnalyzeLogcatTool()
    with pytest.raises(ToolExecutionError, match="binary"):
        await tool.execute(
            {"source": "file", "path": "bugreport.zip"}, make_context(tmp_path)
        )


async def test_inline_without_content_errors(tmp_path) -> None:
    tool = AnalyzeLogcatTool()
    with pytest.raises(ToolArgumentError):
        await tool.execute({"source": "inline"}, make_context(tmp_path))


async def test_min_severity_filter_applied(tmp_path) -> None:
    log = FATAL_JAVA + (
        "06-01 12:00:05.000  700  700 D LeakCanary: com.acme.app.Leaky instance\n"
        "06-01 12:00:05.000  700  700 D LeakCanary: leaking: YES\n"
    )
    tool = AnalyzeLogcatTool()
    result = await tool.execute(
        {"source": "inline", "content": log, "min_severity": "high"}, make_context(tmp_path)
    )
    assert all(group["severity"] in {"high", "critical"} for group in result["groups"])


async def test_kinds_filter_applied(tmp_path) -> None:
    tool = AnalyzeLogcatTool()
    result = await tool.execute(
        {"source": "inline", "content": FATAL_JAVA, "kinds": ["native_crash"]},
        make_context(tmp_path),
    )
    assert result["groups"] == []


async def test_unknown_kind_rejected(tmp_path) -> None:
    tool = AnalyzeLogcatTool()
    with pytest.raises(ToolArgumentError, match="crash kind"):
        await tool.execute(
            {"source": "inline", "content": FATAL_JAVA, "kinds": ["bogus"]},
            make_context(tmp_path),
        )


async def test_adb_missing_gives_actionable_error(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("code_ai.tools.logcat.analyze_logcat.shutil.which", lambda _name: None)
    tool = AnalyzeLogcatTool()
    with pytest.raises(ToolExecutionError, match="adb was not found"):
        await tool.execute({"source": "adb"}, make_context(tmp_path))
