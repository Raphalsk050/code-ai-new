from __future__ import annotations

from pathlib import Path

from code_ai.sandbox.layout import MARKER_FILENAME, SandboxLayout, safe_session_id


def test_layout_nests_every_area_under_one_session_root() -> None:
    layout = SandboxLayout.under(Path("/base"), "abc123")

    assert layout.root == Path("/base/abc123")
    for directory in layout.directories():
        assert directory == layout.root or directory.parent == layout.root
    assert layout.marker == layout.root / MARKER_FILENAME
    assert layout.project_link == layout.root / "project"


def test_layout_areas_are_distinct() -> None:
    layout = SandboxLayout.under(Path("/base"), "abc123")

    assert len(set(layout.directories())) == len(layout.directories())


def test_session_id_is_reduced_to_one_path_segment() -> None:
    layout = SandboxLayout.under(Path("/base"), "../../etc/passwd")

    assert layout.root.parent == Path("/base")
    assert ".." not in layout.root.name


def test_unsafe_characters_are_folded_away() -> None:
    assert safe_session_id("a/b c:d") == "a-b-c-d"
    assert safe_session_id("keeps.dots-and_1") == "keeps.dots-and_1"


def test_an_id_with_nothing_usable_falls_back_to_a_digest() -> None:
    first = safe_session_id("///")
    second = safe_session_id("...")

    assert first.startswith("session-")
    assert second.startswith("session-")
    # Different ids must not collide on one directory.
    assert first != second


def test_a_very_long_id_is_truncated() -> None:
    assert len(safe_session_id("x" * 500)) <= 64
