from __future__ import annotations

import asyncio

import pytest

from code_ai.config.models import AppConfig
from code_ai.core.errors import ToolArgumentError, ToolExecutionError
from code_ai.events.bus import AsyncEventBus
from code_ai.tools.base import ToolContext
from code_ai.tools.skills import CreateSkillTool, UseSkillTool
from code_ai.tools.skills.common import (
    SKILLS_DIR_ENV,
    parse_skill_markdown,
    sanitize_skill_name,
)
from code_ai.util.paths import WorkspacePolicy


def make_context(tmp_path) -> ToolContext:
    config = AppConfig.from_mapping({"api_mode": "ollama", "workspace": str(tmp_path)})
    return ToolContext(
        config=config,
        workspace=WorkspacePolicy.from_path(tmp_path),
        event_bus=AsyncEventBus(session_id="session"),
        cancel_event=asyncio.Event(),
    )


@pytest.fixture
def skills_dir(tmp_path, monkeypatch):
    target = tmp_path / "skills"
    target.mkdir()
    monkeypatch.setenv(SKILLS_DIR_ENV, str(target))
    return target


def test_sanitize_skill_name_rejects_traversal() -> None:
    assert sanitize_skill_name("Release Notes") == "release-notes"
    for bad in ("../escape", "a/b", "", ".", "foo.md"):
        with pytest.raises(ToolArgumentError):
            sanitize_skill_name(bad)


def test_parse_skill_markdown_reads_frontmatter() -> None:
    front, body = parse_skill_markdown(
        "---\nname: demo\ndescription: a demo skill\n---\n\nDo the thing.\n"
    )
    assert front == {"name": "demo", "description": "a demo skill"}
    assert body == "Do the thing."


def test_parse_skill_markdown_without_frontmatter() -> None:
    front, body = parse_skill_markdown("Just instructions.\n")
    assert front == {}
    assert body == "Just instructions."


async def test_create_then_use_skill_roundtrip(skills_dir) -> None:
    context = make_context(skills_dir.parent)
    create = CreateSkillTool()
    result = await create.execute(
        {
            "name": "Release Notes",
            "description": "Draft release notes from git history.",
            "instructions": "1. Read the git log.\n2. Group by type.",
        },
        context,
    )
    assert result["name"] == "release-notes"
    assert result["overwritten"] is False
    entry = skills_dir / "release-notes" / "SKILL.md"
    assert entry.is_file()

    use = UseSkillTool()
    loaded = await use.execute({"name": "release-notes"}, context)
    assert loaded["name"] == "release-notes"
    assert loaded["description"] == "Draft release notes from git history."
    assert "Read the git log" in loaded["instructions"]


async def test_create_skill_refuses_overwrite_without_flag(skills_dir) -> None:
    context = make_context(skills_dir.parent)
    create = CreateSkillTool()
    args = {"name": "dup", "description": "d", "instructions": "i"}
    await create.execute(args, context)
    with pytest.raises(ToolExecutionError):
        await create.execute(args, context)
    overwritten = await create.execute({**args, "overwrite": True}, context)
    assert overwritten["overwritten"] is True


async def test_use_skill_lists_flat_and_directory_skills(skills_dir) -> None:
    context = make_context(skills_dir.parent)
    (skills_dir / "flat.md").write_text(
        "---\nname: flat\ndescription: a flat skill\n---\nbody\n", encoding="utf-8"
    )
    nested = skills_dir / "nested"
    nested.mkdir()
    (nested / "SKILL.md").write_text(
        "---\nname: nested\ndescription: a nested skill\n---\nbody\n", encoding="utf-8"
    )

    use = UseSkillTool()
    listing = await use.execute({"name": None}, context)
    names = {item["name"] for item in listing["skills"]}
    assert names == {"flat", "nested"}
    assert listing["count"] == 2


async def test_use_skill_unknown_name_raises(skills_dir) -> None:
    context = make_context(skills_dir.parent)
    use = UseSkillTool()
    with pytest.raises(ToolExecutionError):
        await use.execute({"name": "missing"}, context)
