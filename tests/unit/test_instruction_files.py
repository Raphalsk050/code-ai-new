from __future__ import annotations

from pathlib import Path

from code_ai.config.defaults import (
    global_instructions_file,
    project_instructions_files,
)
from code_ai.core.rules import RuleSource, RulesService


def _service(tmp_path: Path, *extra: RuleSource) -> RulesService:
    return RulesService(
        global_dir=tmp_path / "missing-global",
        project_dir=tmp_path / "missing-project",
        extra_sources=extra,
    )


def _instructions(path: Path) -> RuleSource:
    return RuleSource(path=path, scope="project", authoritative=True)


def test_instruction_file_is_rendered_as_highest_priority(tmp_path) -> None:
    codeai = tmp_path / "CODEAI.md"
    codeai.write_text("Always use tabs in this repo.", encoding="utf-8")

    rendered = _service(tmp_path, _instructions(codeai)).render_for_prompt()

    assert "HIGHEST PRIORITY" in rendered
    assert "Always use tabs in this repo." in rendered
    assert "outranks everything" in rendered


def test_instruction_file_is_rendered_after_ordinary_rules(tmp_path) -> None:
    """The last word in the block has to be the project's own instructions."""

    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    (rules_dir / "style.md").write_text("Prefer spaces.", encoding="utf-8")
    codeai = tmp_path / "CODEAI.md"
    codeai.write_text("Prefer tabs.", encoding="utf-8")

    rendered = RulesService(
        global_dir=tmp_path / "missing",
        project_dir=rules_dir,
        extra_sources=(_instructions(codeai),),
    ).render_for_prompt()

    assert rendered.index("Prefer spaces.") < rendered.index("Prefer tabs.")
    assert rendered.index("# Rules") < rendered.index("# Project instructions")


def test_ordinary_rules_alone_do_not_get_the_instructions_heading(tmp_path) -> None:
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    (rules_dir / "style.md").write_text("Prefer spaces.", encoding="utf-8")

    rendered = RulesService(
        global_dir=tmp_path / "missing", project_dir=rules_dir
    ).render_for_prompt()

    assert "HIGHEST PRIORITY" not in rendered
    assert "# Rules" in rendered


def test_instructions_alone_do_not_get_the_rules_heading(tmp_path) -> None:
    codeai = tmp_path / "CODEAI.md"
    codeai.write_text("Prefer tabs.", encoding="utf-8")

    rendered = _service(tmp_path, _instructions(codeai)).render_for_prompt()

    assert not rendered.startswith("# Rules")
    assert rendered.startswith("# Project instructions")


def test_missing_instruction_file_renders_nothing(tmp_path) -> None:
    rendered = _service(tmp_path, _instructions(tmp_path / "CODEAI.md")).render_for_prompt()
    assert rendered == ""


def test_local_file_comes_after_the_committed_one(tmp_path) -> None:
    """A personal override must be able to win over the committed instructions."""

    committed, local = project_instructions_files(tmp_path)
    committed.write_text("Team rule.", encoding="utf-8")
    local.write_text("My override.", encoding="utf-8")

    rendered = _service(
        tmp_path, _instructions(committed), _instructions(local)
    ).render_for_prompt()

    assert rendered.index("Team rule.") < rendered.index("My override.")


def test_global_instructions_come_before_project_ones(tmp_path) -> None:
    project = tmp_path / "CODEAI.md"
    project.write_text("Project says tabs.", encoding="utf-8")
    personal = tmp_path / "global-CODEAI.md"
    personal.write_text("I say spaces.", encoding="utf-8")

    rendered = _service(
        tmp_path,
        _instructions(project),
        RuleSource(path=personal, scope="global", authoritative=True),
    ).render_for_prompt()

    assert rendered.index("I say spaces.") < rendered.index("Project says tabs.")


def test_frontmatter_title_is_honoured(tmp_path) -> None:
    codeai = tmp_path / "CODEAI.md"
    codeai.write_text(
        "---\nname: House style\n---\nPrefer tabs.", encoding="utf-8"
    )

    rendered = _service(tmp_path, _instructions(codeai)).render_for_prompt()

    assert "## House style (project)" in rendered


def test_project_instruction_paths_are_in_precedence_order(tmp_path) -> None:
    names = [path.name for path in project_instructions_files(tmp_path)]
    assert names == ["CODEAI.md", "CODEAI.local.md"]


def test_global_instruction_file_follows_the_rules_dir_override(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("CODE_AI_RULES_DIR", str(tmp_path / "elsewhere"))
    assert global_instructions_file() == tmp_path / "elsewhere" / "CODEAI.md"
