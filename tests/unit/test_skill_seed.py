from __future__ import annotations

import shutil

from code_ai.tools.skills.seed import (
    SEED_MARKER_NAME,
    bundled_default_skills,
    seed_default_skills,
)


def test_bundle_includes_architecture_and_create_rules() -> None:
    names = {skill.name for skill in bundled_default_skills()}
    assert "create-rules" in names
    assert {"solid-principles", "ports-and-adapters", "testability"} <= names


def test_seed_materialises_all_defaults_and_marker(tmp_path) -> None:
    root = tmp_path / "skills"
    seeded = seed_default_skills(root)

    expected = {skill.name for skill in bundled_default_skills()}
    assert set(seeded) == expected
    for name in expected:
        assert (root / name / "SKILL.md").is_file()
    assert (root / SEED_MARKER_NAME).exists()


def test_seed_is_idempotent(tmp_path) -> None:
    root = tmp_path / "skills"
    seed_default_skills(root)
    assert seed_default_skills(root) == []


def test_seed_respects_user_deletion(tmp_path) -> None:
    root = tmp_path / "skills"
    seed_default_skills(root)
    # Once seeded, a skill the user deletes must not be silently recreated.
    shutil.rmtree(root / "testability")
    seed_default_skills(root)
    assert not (root / "testability").exists()


def test_seed_does_not_overwrite_existing_skill(tmp_path) -> None:
    root = tmp_path / "skills"
    custom = root / "solid-principles"
    custom.mkdir(parents=True)
    (custom / "SKILL.md").write_text("my own version", encoding="utf-8")

    seed_default_skills(root)

    assert (custom / "SKILL.md").read_text(encoding="utf-8") == "my own version"
    assert (root / "testability" / "SKILL.md").is_file()
