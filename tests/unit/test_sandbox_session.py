from __future__ import annotations

import json
import os
import sys

import pytest

from code_ai.core.errors import WorkspaceBoundaryError
from code_ai.sandbox.layout import MARKER_FILENAME, MARKER_KIND
from code_ai.sandbox.session import SessionSandbox, is_sandbox_root, read_marker, remove_sandbox


def make_sandbox(tmp_path, session_id: str = "session-1") -> SessionSandbox:
    workspace = tmp_path / "project"
    workspace.mkdir(exist_ok=True)
    return SessionSandbox.create(
        session_id=session_id,
        workspace=workspace,
        base_dir=tmp_path / "sandboxes",
    )


def test_creation_materialises_every_area(tmp_path) -> None:
    sandbox = make_sandbox(tmp_path)

    for directory in sandbox.layout.directories():
        assert directory.is_dir()
    assert sandbox.root.name == "session-1"


def test_creation_writes_an_ownership_marker(tmp_path) -> None:
    sandbox = make_sandbox(tmp_path)

    payload = json.loads((sandbox.root / MARKER_FILENAME).read_text(encoding="utf-8"))

    assert payload["kind"] == MARKER_KIND
    assert payload["session_id"] == "session-1"
    assert payload["workspace"] == str((tmp_path / "project").resolve())
    assert payload["pid"] == os.getpid()
    assert read_marker(sandbox.root) == payload


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_the_project_is_reachable_read_only_through_a_link(tmp_path) -> None:
    (tmp_path / "project").mkdir()
    (tmp_path / "project" / "app.py").write_text("value = 1\n", encoding="utf-8")
    sandbox = make_sandbox(tmp_path)

    linked = sandbox.layout.project_link / "app.py"

    assert linked.read_text(encoding="utf-8") == "value = 1\n"


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_writes_cannot_escape_into_the_project_through_the_link(tmp_path) -> None:
    sandbox = make_sandbox(tmp_path)

    with pytest.raises(WorkspaceBoundaryError):
        sandbox.resolve("project/evil.py")


def test_writes_cannot_escape_by_traversal(tmp_path) -> None:
    sandbox = make_sandbox(tmp_path)

    with pytest.raises(WorkspaceBoundaryError):
        sandbox.resolve("../../escaped.txt")

    with pytest.raises(WorkspaceBoundaryError):
        sandbox.resolve(str(tmp_path / "project" / "escaped.txt"))


def test_the_default_working_directory_is_the_work_area(tmp_path) -> None:
    sandbox = make_sandbox(tmp_path)

    assert sandbox.workdir() == sandbox.layout.work
    assert sandbox.workdir("  ") == sandbox.layout.work
    assert sandbox.workdir("work") == sandbox.layout.work


def test_a_requested_working_directory_must_stay_inside(tmp_path) -> None:
    sandbox = make_sandbox(tmp_path)

    with pytest.raises(WorkspaceBoundaryError):
        sandbox.workdir(str(tmp_path / "project"))


def test_the_environment_points_toolchains_at_the_sandbox(tmp_path) -> None:
    sandbox = make_sandbox(tmp_path)

    env = sandbox.environment({})

    assert env["TMPDIR"] == str(sandbox.layout.tmp)
    for value in env.values():
        if value.startswith(("/", str(sandbox.root))):
            assert value.startswith(str(sandbox.root)) or not value.startswith("/")


def test_runtime_cache_directories_exist_after_creation(tmp_path) -> None:
    sandbox = make_sandbox(tmp_path)

    assert (sandbox.layout.cache / "python" / "pip").is_dir()
    assert (sandbox.layout.tmp / "pytest").is_dir()


def test_relative_reports_paths_against_the_sandbox_root(tmp_path) -> None:
    sandbox = make_sandbox(tmp_path)

    assert sandbox.relative(sandbox.layout.work / "out.txt") == "work/out.txt"


def test_cleanup_removes_the_whole_sandbox(tmp_path) -> None:
    sandbox = make_sandbox(tmp_path)
    (sandbox.layout.work / "build.log").write_text("x", encoding="utf-8")

    assert sandbox.cleanup() is True
    assert not sandbox.root.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_cleanup_never_follows_the_link_into_the_project(tmp_path) -> None:
    (tmp_path / "project").mkdir()
    (tmp_path / "project" / "keep.py").write_text("keep\n", encoding="utf-8")
    sandbox = make_sandbox(tmp_path)

    sandbox.cleanup()

    assert (tmp_path / "project" / "keep.py").read_text(encoding="utf-8") == "keep\n"


def test_an_unmarked_directory_is_never_deleted(tmp_path) -> None:
    stranger = tmp_path / "not-a-sandbox"
    stranger.mkdir()
    (stranger / "precious.txt").write_text("data", encoding="utf-8")

    assert is_sandbox_root(stranger) is False
    assert remove_sandbox(stranger) is False
    assert (stranger / "precious.txt").exists()


def test_a_directory_with_a_foreign_marker_is_never_deleted(tmp_path) -> None:
    stranger = tmp_path / "foreign"
    stranger.mkdir()
    (stranger / MARKER_FILENAME).write_text('{"kind": "something-else"}', encoding="utf-8")

    assert remove_sandbox(stranger) is False
    assert stranger.exists()


def test_two_sessions_get_separate_sandboxes(tmp_path) -> None:
    first = make_sandbox(tmp_path, "session-a")
    second = make_sandbox(tmp_path, "session-b")

    assert first.root != second.root
    first.cleanup()
    assert second.root.is_dir()


def test_creating_the_same_session_twice_is_idempotent(tmp_path) -> None:
    first = make_sandbox(tmp_path)
    (first.layout.work / "kept.txt").write_text("kept", encoding="utf-8")

    second = make_sandbox(tmp_path)

    assert second.root == first.root
    assert (second.layout.work / "kept.txt").exists()
