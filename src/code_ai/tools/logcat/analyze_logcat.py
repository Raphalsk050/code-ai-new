from __future__ import annotations

import asyncio
import shutil
from typing import Any

from code_ai.core.errors import ToolArgumentError, ToolExecutionError
from code_ai.tools.base import ToolCapability, ToolContext
from code_ai.tools.logcat.analyzer import LogcatAnalyzer
from code_ai.tools.logcat.models import CrashKind, Severity
from code_ai.tools.schema import tool_schema

# Input is read in full and analysed locally; only the compact report leaves
# this tool. This ceiling bounds memory for a pathological file - beyond it we
# keep the most recent tail (where fresh crashes live), never a head slice.
_MAX_INPUT_CHARS = 16_000_000


class AnalyzeLogcatTool:
    name = "analyze_logcat"
    description = (
        "Analyse Android logcat for crashes and bugs and return a compact, structured report "
        "instead of raw log text. Detects Java/Kotlin exceptions (with cause chains), native "
        "tombstones (SIGSEGV/SIGABRT), ANRs, OutOfMemory, low-memory kills, watchdog aborts, "
        "and memory leaks / GC thrash; groups repeats, ranks by severity, and gives a clean "
        "stacktrace plus probable root cause for each. Sources: 'adb' (runs 'adb logcat -d', "
        "the default), 'file' (a workspace log or bugreport at 'path'), or 'inline' ('content'). "
        "Because it returns a distilled report, huge logs are never truncated mid-stacktrace."
    )
    capabilities = frozenset({ToolCapability.PROCESS, ToolCapability.LOCAL_READ})
    input_schema = tool_schema(
        {
            "source": {
                "type": "string",
                "description": (
                    "Where to read the log: 'adb', 'file', or 'inline'. Defaults to 'adb' when "
                    "omitted, or is inferred from 'content' (inline) / 'path' (file)."
                ),
            },
            "path": {
                "type": "string",
                "description": "Workspace-relative log or bugreport file (source='file').",
            },
            "content": {
                "type": "string",
                "description": "Inline log text to analyse (source='inline').",
            },
            "serial": {
                "type": "string",
                "description": "adb device serial for 'adb -s <serial>' with multiple devices.",
            },
            "buffers": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "adb log buffers, e.g. ['crash','system','main'] or ['all']. "
                    "Defaults to adb's own default buffers."
                ),
            },
            "context_lines": {
                "type": "integer",
                "description": "Lines of surrounding context per crash (0-10, default 3).",
            },
            "max_groups": {
                "type": "integer",
                "description": "Maximum crash groups to return, ranked by severity (default 20).",
            },
            "min_severity": {
                "type": "string",
                "description": (
                    "Drop groups below this severity: critical, high, medium, low, or info."
                ),
            },
            "kinds": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Restrict to these crash kinds: java_exception, native_crash, anr, "
                    "out_of_memory, low_memory_kill, watchdog, memory_leak."
                ),
            },
        },
        required=(),
    )

    def __init__(self, analyzer: LogcatAnalyzer | None = None) -> None:
        self._analyzer = analyzer or LogcatAnalyzer()

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        source = self._resolve_source(arguments)
        context_lines = _clamp(arguments.get("context_lines"), default=3, low=0, high=10)
        max_groups = _clamp(arguments.get("max_groups"), default=20, low=1, high=200)
        min_severity = _parse_severity(arguments.get("min_severity"))
        kinds = _parse_kinds(arguments.get("kinds"))

        text, source_label = await self._read(source, arguments, context)
        text, input_truncated = _bound_input(text)

        report = self._analyzer.analyze(
            text,
            source=source_label,
            context_lines=context_lines,
            max_groups=max_groups,
            kinds=kinds,
            min_severity=min_severity,
        )
        result = report.to_dict()
        result["input_truncated"] = input_truncated
        if input_truncated:
            result["note"] = (
                "Input exceeded the size ceiling; analysed the most recent tail only."
            )
        return result

    @staticmethod
    def _resolve_source(arguments: dict[str, Any]) -> str:
        explicit = arguments.get("source")
        if explicit in {"adb", "file", "inline"}:
            return explicit
        if arguments.get("content"):
            return "inline"
        if arguments.get("path"):
            return "file"
        return "adb"

    async def _read(
        self, source: str, arguments: dict[str, Any], context: ToolContext
    ) -> tuple[str, str]:
        if source == "inline":
            content = arguments.get("content")
            if not isinstance(content, str) or not content:
                raise ToolArgumentError("source='inline' requires non-empty 'content'.")
            return content, "inline"
        if source == "file":
            path_value = str(arguments.get("path") or "")
            if not path_value:
                raise ToolArgumentError("source='file' requires 'path'.")
            path = context.workspace.resolve(path_value, must_exist=True)
            data = path.read_bytes()
            if data.count(b"\x00") > 8:
                raise ToolExecutionError(
                    f"{path_value} looks binary (a zipped bugreport?); unzip it to text first."
                )
            text = data.decode("utf-8", errors="replace")
            return text, f"file:{path.relative_to(context.workspace.root).as_posix()}"
        return await self._read_adb(arguments, context)

    async def _read_adb(
        self, arguments: dict[str, Any], context: ToolContext
    ) -> tuple[str, str]:
        adb_path = shutil.which("adb")
        if adb_path is None:
            raise ToolExecutionError(
                "adb was not found on PATH. Install Android platform-tools, or analyse a saved "
                "log by passing source='file' with 'path', or source='inline' with 'content'."
            )
        argv = [adb_path]
        serial = arguments.get("serial")
        if serial:
            argv += ["-s", str(serial)]
        argv += ["logcat", "-d", "-v", "threadtime"]
        for buffer in _string_list(arguments.get("buffers")):
            argv += ["-b", buffer]

        timeout = min(
            context.config.budgets.default_tool_timeout_s,
            context.config.budgets.max_tool_call_seconds,
        )
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise ToolExecutionError(f"Failed to launch adb: {exc}") from exc
        try:
            stdout_raw, stderr_raw = await asyncio.wait_for(
                process.communicate(), timeout=timeout
            )
        except TimeoutError as exc:
            process.kill()
            await process.wait()
            raise ToolExecutionError(f"adb logcat timed out after {timeout}s.") from exc

        if process.returncode != 0:
            stderr = stderr_raw.decode("utf-8", errors="replace").strip()
            raise ToolExecutionError(
                f"adb logcat failed (exit {process.returncode}): {stderr or 'no output'}. "
                "Is a device connected and authorised (`adb devices`)?"
            )
        label = "adb" + (f":{serial}" if serial else "")
        return stdout_raw.decode("utf-8", errors="replace"), label


def _bound_input(text: str) -> tuple[str, bool]:
    if len(text) <= _MAX_INPUT_CHARS:
        return text, False
    tail = text[-_MAX_INPUT_CHARS:]
    # Drop the first (now partial) line so parsing starts on a clean boundary.
    newline = tail.find("\n")
    if newline != -1:
        tail = tail[newline + 1 :]
    return tail, True


def _clamp(value: Any, *, default: int, low: int, high: int) -> int:
    if value is None:
        return default
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ToolArgumentError(f"Expected an integer, got {value!r}.") from exc
    return max(low, min(high, number))


def _parse_severity(value: Any) -> Severity | None:
    if value is None:
        return None
    try:
        return Severity(str(value).strip().lower())
    except ValueError as exc:
        raise ToolArgumentError(f"Unknown severity: {value!r}.") from exc


def _parse_kinds(value: Any) -> frozenset[CrashKind] | None:
    items = _string_list(value)
    if not items:
        return None
    kinds: set[CrashKind] = set()
    for item in items:
        try:
            kinds.add(CrashKind(item.strip().lower()))
        except ValueError as exc:
            raise ToolArgumentError(f"Unknown crash kind: {item!r}.") from exc
    return frozenset(kinds)


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ToolArgumentError("Expected a list of strings.")
    return [item for item in value if item]
