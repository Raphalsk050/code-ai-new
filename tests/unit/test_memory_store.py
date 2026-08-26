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


def test_render_groups_by_kind(tmp_path) -> None:
    service = _service(tmp_path)
    service.add(kind="user", content="The user is named Rafael.")
    service.add(kind="feedback", content="Run pytest -q.")
    service.add(kind="project", content="Build with make release.")

    rendered = service.render_for_prompt()
    assert "Who the user is" in rendered
    assert "- The user is named Rafael." in rendered
    assert "How the user wants you to work" in rendered
    assert "- Run pytest -q." in rendered
    assert "What you have learned about this project" in rendered
    assert "- Build with make release." in rendered


def test_identity_is_not_crowded_out_by_feedback(tmp_path) -> None:
    # Regression: a flood of more-recently-updated feedback memories must not
    # push the user's identity out of the rendered prompt.
    service = _service(tmp_path)
    service.add(kind="user", content="The user is named Rafael.")
    for i in range(40):
        service.add(kind="feedback", content=f"Work directive {i}.")

    rendered = service.render_for_prompt()
    assert "Who the user is" in rendered
    assert "Rafael" in rendered


def test_render_limit_caps_feedback_but_never_identity(tmp_path) -> None:
    service = _service(tmp_path)
    service.add(kind="user", content="The user is named Rafael.")
    for i in range(5):
        service.add(kind="feedback", content=f"Work directive {i}.")

    rendered = service.render_for_prompt(limit_per_kind=2)
    # Identity is always rendered in full; feedback is capped at the limit.
    assert "Rafael" in rendered
    assert rendered.count("Work directive") == 2


def test_render_empty_when_nothing_saved(tmp_path) -> None:
    assert _service(tmp_path).render_for_prompt() == ""


def test_find_by_content_matches_exact_text(tmp_path) -> None:
    store = MemoryStore(tmp_path / "mem")
    store.add(kind="project", content="Build with make release.")

    found = store.find_by_content("Build with make release.")
    assert found is not None
    assert found.kind == "project"
    assert store.find_by_content("Build with cargo.") is None


def test_remove_deletes_entry(tmp_path) -> None:
    store = MemoryStore(tmp_path / "mem")
    entry = store.add(kind="project", content="Old fact.")

    assert store.remove(entry.id) is True
    assert store.all() == []
    # Removing again reports nothing was there.
    assert store.remove(entry.id) is False


def test_rewrite_replaces_text_and_keeps_provenance(tmp_path) -> None:
    store = MemoryStore(tmp_path / "mem")
    entry = store.add(kind="feedback", content="The stack is Flask.")

    rewritten = store.rewrite(entry.id, "The stack is FastAPI.", source="consolidation")
    assert rewritten is not None
    assert rewritten.kind == "feedback"
    assert rewritten.created == entry.created
    assert rewritten.source == "consolidation"
    assert [e.content for e in store.all()] == ["The stack is FastAPI."]
    # The old id is gone; rewriting a missing id is a no-op.
    assert store.rewrite(entry.id, "whatever") is None


def test_maintenance_marker_tracks_growth_and_is_not_an_entry(tmp_path) -> None:
    store = MemoryStore(tmp_path / "mem")
    store.add(kind="project", content="fact 1")
    store.add(kind="project", content="fact 2")

    # Never maintained: everything counts as new.
    assert store.new_entries_since_maintenance() == 2

    store.mark_maintained()
    assert store.new_entries_since_maintenance() == 0
    store.add(kind="project", content="fact 3")
    assert store.new_entries_since_maintenance() == 1

    # The marker file lives in the same directory but is never read as a memory.
    assert len(store.all()) == 3


def test_service_remove_by_content_probes_both_scopes(tmp_path) -> None:
    service = _service(tmp_path)
    service.add(kind="feedback", content="global fact")
    service.add(kind="project", content="project fact")

    assert service.remove_by_content("project fact") is True
    assert service.remove_by_content("global fact") is True
    assert service.remove_by_content("never existed") is False
    assert service.render_for_prompt() == ""
