from __future__ import annotations

from pathlib import Path

from code_ai.prompts import build_subagent_system_prompt, build_system_prompt

CATALOG = (
    "# Available skills\n\n"
    "These skills are available this session.\n\n"
    "- pdf-magic: Extract tables from PDFs."
)


def test_main_prompt_injects_skill_catalog() -> None:
    prompt = build_system_prompt(
        workspace=Path("/tmp/ws"), language="en", skills=CATALOG
    )
    assert "# Available skills" in prompt
    assert "- pdf-magic: Extract tables from PDFs." in prompt
    # The standing guidance still tells the model to act on a matching skill.
    assert "use_skill" in prompt


def test_main_prompt_without_skills_has_no_catalog_heading() -> None:
    prompt = build_system_prompt(workspace=Path("/tmp/ws"), language="en")
    assert "# Available skills" not in prompt
    # The discovery fallback and create_skill guidance remain available.
    assert "use_skill" in prompt
    assert "create_skill" in prompt


def test_subagent_prompt_injects_skill_catalog() -> None:
    prompt = build_subagent_system_prompt(
        role_prompt="You are a coder.",
        workspace=Path("/tmp/ws"),
        language="en",
        skills=CATALOG,
    )
    assert "# Available skills" in prompt
    assert "- pdf-magic: Extract tables from PDFs." in prompt


def test_subagent_prompt_without_skills_omits_catalog() -> None:
    prompt = build_subagent_system_prompt(
        role_prompt="You are a coder.", workspace=Path("/tmp/ws"), language="en"
    )
    assert "# Available skills" not in prompt
