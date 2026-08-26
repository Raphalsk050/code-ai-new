from __future__ import annotations

from pathlib import Path

from code_ai.core.rules import RulesService


def _write_rule(path: Path, name: str, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nname: {name}\ndescription: d\n---\n\n{body}\n", encoding="utf-8")


def test_no_rules_renders_empty(tmp_path) -> None:
    service = RulesService(global_dir=tmp_path / "g", project_dir=tmp_path / "p")
    assert service.load() == []
    assert service.render_for_prompt() == ""


def test_loads_global_then_project_and_renders_binding_section(tmp_path) -> None:
    global_dir = tmp_path / "g"
    project_dir = tmp_path / "p"
    _write_rule(global_dir / "lang.md", "reply-in-ptbr", "Sempre responda em pt-BR.")
    _write_rule(project_dir / "tests.md", "run-tests", "Sempre rode pytest -q antes de concluir.")
    service = RulesService(global_dir=global_dir, project_dir=project_dir)

    records = service.load()
    assert [record.scope for record in records] == ["global", "project"]
    assert [record.name for record in records] == ["reply-in-ptbr", "run-tests"]

    rendered = service.render_for_prompt()
    assert "MANDATORY" in rendered
    assert "Sempre responda em pt-BR." in rendered
    assert "Sempre rode pytest -q antes de concluir." in rendered


def test_rule_without_frontmatter_uses_filename_and_full_body(tmp_path) -> None:
    project_dir = tmp_path / "p"
    project_dir.mkdir(parents=True)
    (project_dir / "no-front.md").write_text("Just a bare instruction.", encoding="utf-8")

    service = RulesService(global_dir=tmp_path / "g", project_dir=project_dir)
    record = service.load()[0]
    assert record.name == "no-front"
    assert record.body == "Just a bare instruction."


def test_empty_rule_file_is_skipped(tmp_path) -> None:
    project_dir = tmp_path / "p"
    project_dir.mkdir(parents=True)
    (project_dir / "blank.md").write_text("---\nname: blank\n---\n\n", encoding="utf-8")

    service = RulesService(global_dir=tmp_path / "g", project_dir=project_dir)
    assert service.load() == []
