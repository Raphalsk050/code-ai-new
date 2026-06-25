from __future__ import annotations

from code_ai.core.memory import Memory, MemoryService, MemoryStore


def test_add_persists_and_reads_back(tmp_path) -> None:
    store = MemoryStore(tmp_path / "mem")
    store.add(kind="project", content="The build command is make release.")

    entries = store.all()
    assert len(entries) == 1
    assert entries[0].content == "The build command is make release."
    assert entries[0].kind == "project"


def test_add_dedups_identical_content(tmp_path) -> None:
    store = MemoryStore(tmp_path / "mem")
    first = store.add(kind="feedback", content="Always run pytest -q.")
    second = store.add(kind="feedback", content="Always run pytest -q.")

    # Same fact collapses onto one file; recency is refreshed in place.
    assert first.id == second.id
    assert len(store.all()) == 1
    assert second.updated >= first.updated


def test_memories_survive_a_fresh_store_instance(tmp_path) -> None:
    MemoryStore(tmp_path / "mem").add(kind="user", content="The user prefers pt-BR.")

    reopened = MemoryStore(tmp_path / "mem")
    assert reopened.all()[0].content == "The user prefers pt-BR."


def test_long_content_is_clipped(tmp_path) -> None:
    store = MemoryStore(tmp_path / "mem")
    entry = store.add(kind="project", content="x" * 5000)
    assert len(entry.content) <= 600


def test_corrupt_entry_is_ignored(tmp_path) -> None:
    directory = tmp_path / "mem"
    directory.mkdir(parents=True)
    (directory / "deadbeef.json").write_text("{not json", encoding="utf-8")

    assert MemoryStore(directory).all() == []


def test_round_trips_through_dict() -> None:
    entry = Memory(kind="project", content="hello", source="remember_tool")
    restored = Memory.from_dict(entry.to_dict())
    assert restored.kind == "project"
    assert restored.content == "hello"
    assert restored.source == "remember_tool"
    assert restored.id == entry.id


def _service(tmp_path) -> MemoryService:
    return MemoryService(
        global_store=MemoryStore(tmp_path / "global"),
        project_store=MemoryStore(tmp_path / "project"),
    )


def test_service_routes_kinds_to_scopes(tmp_path) -> None:
    global_store = MemoryStore(tmp_path / "global")
    project_store = MemoryStore(tmp_path / "project")
    service = MemoryService(global_store=global_store, project_store=project_store)

    service.add(kind="user", content="user fact")
    service.add(kind="project", content="project fact")

    assert [e.content for e in global_store.all()] == ["user fact"]
    assert [e.content for e in project_store.all()] == ["project fact"]


def test_service_rejects_unknown_kind(tmp_path) -> None:
    service = _service(tmp_path)
    try:
        service.add(kind="bogus", content="x")
    except ValueError:
        pass
    else:  # pragma: no cover - guard
        raise AssertionError("expected ValueError for unknown kind")


def test_render_groups_by_scope(tmp_path) -> None:
    service = _service(tmp_path)
    service.add(kind="feedback", content="Run pytest -q.")
    service.add(kind="project", content="Build with make release.")

    rendered = service.render_for_prompt()
    assert "What the user told you" in rendered
    assert "- Run pytest -q." in rendered
    assert "What you have learned about this project" in rendered
    assert "- Build with make release." in rendered


def test_render_empty_when_nothing_saved(tmp_path) -> None:
    assert _service(tmp_path).render_for_prompt() == ""
