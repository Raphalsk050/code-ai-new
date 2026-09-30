"""Both front ends show the workspace's git branch, read without running git."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from code_ai.app import service as service_module
from code_ai.app.service import CodeAIApplication
from code_ai.config.models import AppConfig
from code_ai.core.state import AgentState
from code_ai.util.git_branch import current_branch


def _repo(root: Path, head: str) -> Path:
    (root / ".git").mkdir(parents=True)
    (root / ".git" / "HEAD").write_text(head + "\n", encoding="utf-8")
    return root


def test_the_branch_comes_from_head(tmp_path) -> None:
    repo = _repo(tmp_path / "repo", "ref: refs/heads/feature/mac")
    (repo / "src" / "deep").mkdir(parents=True)

    assert current_branch(repo) == "feature/mac"
    # A workspace inside the repository walks up to it.
    assert current_branch(repo / "src" / "deep") == "feature/mac"


def test_off_a_branch_it_says_where_head_is(tmp_path) -> None:
    repo = _repo(tmp_path / "repo", "0123456789abcdef0123456789abcdef01234567")
    assert current_branch(repo) == "detached at 0123456"


def test_a_worktree_follows_its_git_file(tmp_path) -> None:
    main = _repo(tmp_path / "main", "ref: refs/heads/main")
    linked = main / ".git" / "worktrees" / "wt"
    linked.mkdir(parents=True)
    (linked / "HEAD").write_text("ref: refs/heads/fix/login\n", encoding="utf-8")
    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / ".git").write_text("gitdir: ../main/.git/worktrees/wt\n", encoding="utf-8")

    assert current_branch(worktree) == "fix/login"


def test_outside_a_repository_there_is_no_branch(tmp_path) -> None:
    folder = tmp_path / "plain"
    folder.mkdir()
    # tmp_path itself may sit inside a checkout; a .git file with junk stops the walk.
    (folder / ".git").write_text("not a gitdir line\n", encoding="utf-8")
    assert current_branch(folder) == ""


class _Bus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def emit(self, event_type: str, payload: dict | None = None, *, source: str = "") -> None:
        self.events.append((event_type, payload or {}))


async def test_the_app_says_the_branch_and_again_when_it_changes(tmp_path, monkeypatch) -> None:
    repo = _repo(tmp_path / "repo", "ref: refs/heads/main")
    monkeypatch.setattr(service_module, "_BRANCH_POLL_S", 0.01)
    config = AppConfig.from_mapping({"api_mode": "ollama", "workspace": str(repo)})
    bus = _Bus()
    app = CodeAIApplication(
        session=SimpleNamespace(config=config, state=AgentState.READY),
        event_bus=bus,
        orchestrator=SimpleNamespace(),
        provider=None,
        compressor=None,
        conversation_store=None,
    )

    watcher = asyncio.create_task(app._watch_branch())
    try:
        await asyncio.sleep(0.05)
        (repo / ".git" / "HEAD").write_text("ref: refs/heads/feature/mac\n", encoding="utf-8")
        await asyncio.sleep(0.05)
    finally:
        watcher.cancel()

    said = [payload["branch"] for kind, payload in bus.events if kind == "workspace.branch"]
    # Once each: an unchanged branch is not announced again every poll.
    assert said == ["main", "feature/mac"]
    assert app.get_settings()["branch"] == "feature/mac"
