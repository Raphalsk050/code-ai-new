from __future__ import annotations

from code_ai.core.memory_recall import MemoryRecall, extract_terms

PYTEST_MEMORY = ("Always run this project's tests with pytest -q.", "feedback")
MIGRATION_MEMORY = ("The alembic migrations directory must never be edited by hand.", "project")


def _recall(*entries, **kwargs) -> MemoryRecall:
    return MemoryRecall.from_contents(entries, **kwargs)


def test_terms_keep_identifiers_and_their_parts() -> None:
    terms = extract_terms("call build_system_prompt in orchestration.py")
    assert "build_system_prompt" in terms
    assert "system" in terms
    assert "prompt" in terms
    assert "orchestration" in terms


def test_terms_drop_filler_words() -> None:
    """Common words must not make two unrelated texts look like a topic match."""

    assert extract_terms("this is about the thing that would have been") == frozenset()
    assert extract_terms("quando você quiser fazer isso") == frozenset({"quiser", "fazer"})


def test_memory_returns_when_the_work_is_about_it() -> None:
    recall = _recall(MIGRATION_MEMORY)
    note = recall.consider("edit_code alembic/migrations/0004_add_column.py")
    assert note is not None
    assert "alembic migrations" in note


def test_unrelated_work_surfaces_nothing() -> None:
    recall = _recall(MIGRATION_MEMORY)
    assert recall.consider("read_file docs/readme.md") is None


def test_a_single_shared_term_is_not_enough() -> None:
    """One coincidental word in common is not a topic match."""

    recall = _recall(MIGRATION_MEMORY)
    assert recall.consider("write_file notes/directory-layout.md") is None


def test_memory_is_not_repeated_within_a_turn() -> None:
    recall = _recall(PYTEST_MEMORY)
    focus = "execute_command pytest tests/unit --quiet project"
    assert recall.consider(focus) is not None
    assert recall.consider(focus) is None


def test_per_turn_cap_is_respected() -> None:
    recall = _recall(PYTEST_MEMORY, MIGRATION_MEMORY, max_per_turn=1)
    assert recall.consider("pytest tests project run") is not None
    assert recall.consider("alembic migrations directory edited") is None


def test_the_strongest_match_wins() -> None:
    recall = _recall(PYTEST_MEMORY, MIGRATION_MEMORY)
    note = recall.consider("alembic migrations directory hand-edited in this project")
    assert "alembic" in note


def test_recalled_memory_is_marked_as_possibly_stale() -> None:
    note = _recall(PYTEST_MEMORY).consider("pytest tests project")
    assert "still holds" in note


def test_memories_without_distinctive_terms_are_ignored() -> None:
    recall = _recall(("do it", "feedback"))
    assert recall.consider("do it now") is None


def test_empty_focus_surfaces_nothing() -> None:
    assert _recall(PYTEST_MEMORY).consider("") is None


def test_no_memories_means_no_recall() -> None:
    assert MemoryRecall.from_contents([]).consider("pytest tests project") is None
