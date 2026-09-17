"""A missing or broken document library must never stop Code-AI from starting."""

from __future__ import annotations

import asyncio
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from code_ai.config.models import AppConfig
from code_ai.core.errors import ToolExecutionError
from code_ai.events.bus import AsyncEventBus
from code_ai.tools.base import ToolContext
from code_ai.tools.office import deps
from code_ai.util.paths import WorkspacePolicy

SRC = Path(__file__).resolve().parents[2] / "src"
NEW_TOOLS = {
    "pdf_inspect",
    "pdf_edit",
    "pdf_convert",
    "document_create",
    "document_inspect",
    "document_edit",
    "document_format",
    "document_convert",
    "slides_create",
    "slides_inspect",
    "slides_edit",
    "slides_format",
    "slides_render",
    "design_system",
    "ui_preview",
    "ui_audit",
    "latex_article",
    "latex_compile",
    "latex_setup",
}

# Runs in a fresh interpreter: the libraries are either blocked outright or replaced by
# the PyPI 'docx' impostor, whose Python 2 code fails with "No module named 'exceptions'".
STARTUP = textwrap.dedent(
    """
    import importlib.abc, sys
    BLOCKED = {"pptx", "pypdf", "pypdfium2", "reportlab", "lxml", "cryptography"}

    class Blocker(importlib.abc.MetaPathFinder):
        def find_spec(self, name, path=None, target=None):
            if name.split(".")[0] in BLOCKED:
                raise ModuleNotFoundError(f"No module named {name!r}")
            return None

    sys.meta_path.insert(0, Blocker())
    sys.path.insert(0, sys.argv[1])  # fake 'docx' package that imports 'exceptions'
    sys.path.insert(0, sys.argv[2])  # src

    from code_ai.bootstrap import build_tool_registry
    import code_ai.cli.main  # noqa: F401 - the whole startup import chain
    names = set(build_tool_registry().names())
    print("TOOLS", ",".join(sorted(names)))
    """
)


def test_startup_survives_missing_and_impostor_libraries(tmp_path) -> None:
    impostor = tmp_path / "impostor" / "docx"
    impostor.mkdir(parents=True)
    (impostor / "__init__.py").write_text("import exceptions\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-c", STARTUP, str(impostor.parent), str(SRC)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    line = next(row for row in result.stdout.splitlines() if row.startswith("TOOLS "))
    assert NEW_TOOLS <= set(line.removeprefix("TOOLS ").split(","))


def test_problem_detects_the_docx_impostor(tmp_path, monkeypatch) -> None:
    impostor = tmp_path / "docx"
    impostor.mkdir()
    (impostor / "__init__.py").write_text("import exceptions\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "docx", raising=False)
    reason = deps.problem("docx")
    assert reason and "exceptions" in reason
    assert "docx" not in sys.modules


def test_frozen_binary_explains_instead_of_installing(monkeypatch) -> None:
    monkeypatch.setattr(deps, "problem", lambda module: "ModuleNotFoundError: gone")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(deps, "_install", lambda *a, **k: pytest.fail("no pip in a binary"))
    with pytest.raises(ToolExecutionError, match="newer build"):
        deps.ensure_sync("docx")


def test_from_source_the_impostor_is_replaced(monkeypatch) -> None:
    commands: list[list[str]] = []
    state = {"fixed": False}

    def fake_run(command):
        commands.append(command)
        if "install" in command and "python-docx" in command:
            state["fixed"] = True
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(deps, "_attempted", set())
    monkeypatch.setattr(deps, "problem", lambda module: None if state["fixed"] else "broken")
    monkeypatch.setattr(deps, "_run", fake_run)
    deps.ensure_sync("docx")
    assert commands[0][-3:] == ["uninstall", "-y", "docx"]
    assert "--force-reinstall" in commands[1] and "python-docx" in commands[1]
    assert "--trusted-host" in commands[2] and "python-docx" in commands[2]


def test_a_failed_install_names_the_fix(monkeypatch) -> None:
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(deps, "_attempted", set())
    monkeypatch.setattr(
        deps, "problem", lambda module: "ModuleNotFoundError: No module named 'pptx'"
    )
    monkeypatch.setattr(deps, "_install", lambda modules, verify_ssl: "network unreachable")
    with pytest.raises(ToolExecutionError) as caught:
        deps.ensure_sync("pptx")
    message = str(caught.value)
    assert (
        "network unreachable" in message and "pip install" in message and "python-pptx" in message
    )


async def test_a_tool_reports_a_missing_library_as_a_tool_error(tmp_path, monkeypatch) -> None:
    from code_ai.tools.documents import DocumentCreateTool
    from code_ai.tools.documents import tools as document_tools

    async def unavailable(*modules, verify_ssl=False):
        raise ToolExecutionError("Could not load python-docx")

    monkeypatch.setattr(document_tools, "ensure", unavailable)
    context = ToolContext(
        config=AppConfig.from_mapping({"api_mode": "ollama", "workspace": str(tmp_path)}),
        workspace=WorkspacePolicy.from_path(tmp_path),
        event_bus=AsyncEventBus(session_id="session"),
        cancel_event=asyncio.Event(),
    )
    with pytest.raises(ToolExecutionError, match="python-docx"):
        await DocumentCreateTool().execute({"output": "a.docx", "content": "# Hi"}, context)


def test_lazy_module_turns_import_failures_into_tool_errors() -> None:
    missing = deps.LazyModule("code_ai_module_that_does_not_exist")
    with pytest.raises(ToolExecutionError, match="could not be loaded"):
        missing.anything  # noqa: B018
