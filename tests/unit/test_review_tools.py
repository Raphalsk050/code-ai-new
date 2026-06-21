from __future__ import annotations

import asyncio
import subprocess

from code_ai.config.models import AppConfig
from code_ai.events.bus import AsyncEventBus
from code_ai.tools.base import ToolContext
from code_ai.tools.git import GitReviewTool
from code_ai.tools.review import (
    ArchitectureReviewTool,
    GenerateDocumentationTool,
    TestReviewTool,
)
from code_ai.tools.review.service import GenerationResult, ReviewResult
from code_ai.util.paths import WorkspacePolicy


class FakeReviewService:
    def __init__(self) -> None:
        self.review_calls: list[dict[str, str]] = []
        self.generate_calls: list[dict[str, str]] = []

    async def review(self, *, prompt: str, content: str, source: str) -> ReviewResult:
        self.review_calls.append({"prompt": prompt, "content": content, "source": source})
        return ReviewResult(summary="reviewed", findings=[], usage=None)

    async def generate(self, *, prompt: str, content: str, source: str) -> GenerationResult:
        self.generate_calls.append({"prompt": prompt, "content": content, "source": source})
        return GenerationResult(text="# Title\n\nDocs.", usage=None)


def make_context(tmp_path, review_service=None) -> ToolContext:
    config = AppConfig.from_mapping({"api_mode": "ollama", "workspace": str(tmp_path)})
    return ToolContext(
        config=config,
        workspace=WorkspacePolicy.from_path(tmp_path),
        event_bus=AsyncEventBus(session_id="session"),
        cancel_event=asyncio.Event(),
        review_service=review_service,
    )


async def test_test_review_delegates_to_review_service(tmp_path) -> None:
    service = FakeReviewService()
    context = make_context(tmp_path, service)
    tool = TestReviewTool()

    assert set(tool.input_schema["required"]) == {"content"}

    result = await tool.execute({"content": "def test_x(): assert True"}, context)

    assert result["summary"] == "reviewed"
    assert service.review_calls[0]["source"] == "test_review"
    assert "device" in service.review_calls[0]["prompt"].lower()


async def test_architecture_review_uses_architecture_prompt(tmp_path) -> None:
    service = FakeReviewService()
    context = make_context(tmp_path, service)
    tool = ArchitectureReviewTool()

    await tool.execute({"content": "module graph"}, context)

    assert "separation of concerns" in service.review_calls[0]["prompt"].lower()


async def test_generate_documentation_returns_text_and_threads_audience(tmp_path) -> None:
    service = FakeReviewService()
    context = make_context(tmp_path, service)
    tool = GenerateDocumentationTool()

    result = await tool.execute(
        {"content": "class Foo: ...", "audience": "API consumers"}, context
    )

    assert result["documentation"] == "# Title\n\nDocs."
    assert "API consumers" in service.generate_calls[0]["prompt"]


async def test_git_review_runs_readonly_inspection(tmp_path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")

    context = make_context(tmp_path)
    tool = GitReviewTool()

    result = await tool.execute({"focus": "status"}, context)

    assert result["focus"] == "status"
    commands = result["commands"]
    assert commands[0]["command"] == "git status"
    assert commands[0]["exit_code"] == 0
    assert "a.txt" in commands[0]["stdout"]


async def test_git_review_defaults_unknown_focus_to_overview(tmp_path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)

    context = make_context(tmp_path)
    tool = GitReviewTool()

    result = await tool.execute({"focus": "nonsense"}, context)

    assert result["focus"] == "overview"
    assert [c["command"] for c in result["commands"]][0] == "git status --short --branch"
