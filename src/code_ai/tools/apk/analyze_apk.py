from __future__ import annotations

import asyncio
from typing import Any

from code_ai.core.errors import ToolArgumentError, ToolExecutionError
from code_ai.tools.apk.analyzer import SECTIONS, AnalysisOptions, ApkAnalyzer
from code_ai.tools.apk.models import Severity
from code_ai.tools.base import ToolCapability, ToolContext
from code_ai.tools.schema import tool_schema


class AnalyzeApkTool:
    name = "analyze_apk"
    description = (
        "Analyse an already-built Android APK and return a structured report instead of raw "
        "bytes. Decodes the binary AndroidManifest.xml (package, versionCode/versionName, "
        "min/target SDK, permissions, every activity/service/receiver/provider and which are "
        "exported), resolves @string references through resources.arsc, reads the signature "
        "schemes (v1/v2/v3) with each signing certificate's fingerprint, subject, algorithm and "
        "validity, summarises dex counts, native ABIs, size by category and the UI framework it "
        "was built with, then reports findings: debuggable builds, cleartext HTTP, unprotected "
        "exported components, missing android:exported on Android 12+, debug or expired signing "
        "certificates, 32-bit-only native libraries, and more. Static and offline: nothing is "
        "installed, executed or extracted, and no adb device is needed. Signatures are read, not "
        "cryptographically verified. Use it to inspect a release artifact, compare a build "
        "against what was intended, or answer 'what is inside this APK'."
    )
    capabilities = frozenset({ToolCapability.LOCAL_READ})
    input_schema = tool_schema(
        {
            "path": {
                "type": "string",
                "description": "Workspace-relative path to the .apk file.",
            },
            "sections": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Sections to include: manifest, permissions, components, signing, contents, "
                    "dex. Defaults to all of them. Findings and the summary are always returned, "
                    "so pass [] for a findings-only report."
                ),
            },
            "min_severity": {
                "type": "string",
                "description": (
                    "Drop findings below this severity: critical, high, medium, low, or info."
                ),
            },
            "max_components": {
                "type": "integer",
                "description": "Maximum exported components to list (1-200, default 25).",
            },
            "max_files": {
                "type": "integer",
                "description": "Maximum largest-entry rows to list (0-100, default 15).",
            },
        },
        required=("path",),
    )

    def __init__(self, analyzer: ApkAnalyzer | None = None) -> None:
        self._analyzer = analyzer or ApkAnalyzer()

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        raw_path = arguments.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ToolArgumentError("analyze_apk requires 'path' to the .apk file.")
        path = context.workspace.resolve(raw_path, must_exist=True)
        if not path.is_file():
            raise ToolExecutionError(f"{raw_path} is a directory, not an APK file.")

        options = AnalysisOptions(
            sections=_parse_sections(arguments.get("sections")),
            max_components=_clamp(arguments.get("max_components"), default=25, low=1, high=200),
            max_files=_clamp(arguments.get("max_files"), default=15, low=0, high=100),
            min_severity=_parse_severity(arguments.get("min_severity")),
        )
        display = _display_path(path, context)
        report = await asyncio.to_thread(
            self._analyzer.analyze, path, display_path=display, options=options
        )
        return report.to_dict()


def _display_path(path, context: ToolContext) -> str:
    try:
        return path.relative_to(context.workspace.root).as_posix()
    except ValueError:
        return path.as_posix()


def _parse_sections(value: Any) -> frozenset[str]:
    if value is None:
        return frozenset(SECTIONS)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ToolArgumentError("'sections' must be a list of strings.")
    requested = {item.strip().lower() for item in value if item.strip()}
    unknown = requested - set(SECTIONS)
    if unknown:
        raise ToolArgumentError(
            f"Unknown section(s): {sorted(unknown)}. Choose from {list(SECTIONS)}."
        )
    return frozenset(requested)


def _parse_severity(value: Any) -> Severity | None:
    if value is None:
        return None
    try:
        return Severity(str(value).strip().lower())
    except ValueError as exc:
        raise ToolArgumentError(f"Unknown severity: {value!r}.") from exc


def _clamp(value: Any, *, default: int, low: int, high: int) -> int:
    if value is None:
        return default
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ToolArgumentError(f"Expected an integer, got {value!r}.") from exc
    return max(low, min(high, number))
