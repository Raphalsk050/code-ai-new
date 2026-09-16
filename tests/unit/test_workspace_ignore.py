from __future__ import annotations

import os
from pathlib import Path

import pytest

from code_ai.util.ignore import WorkspaceIgnore, is_generated_dir_name, parse_ignore_file


def _write(workspace: Path, relative: str, text: str = "x") -> Path:
    path = workspace / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _walk(workspace: Path, **kwargs) -> tuple[list[str], list[str]]:
    rules = WorkspaceIgnore(workspace, **kwargs)
    files = sorted(path.relative_to(workspace).as_posix() for path in rules.walk())
    return files, sorted(rules.pruned)


# --------------------------------------------------------------------------- #
# gitignore syntax
# --------------------------------------------------------------------------- #
def _ignored(text: str, relative: str, *, is_dir: bool = False) -> bool:
    verdict = False
    for rule in parse_ignore_file(text):
        if rule.matches(relative, is_dir=is_dir):
            verdict = not rule.negate
    return verdict


@pytest.mark.parametrize(
    ("pattern", "relative", "is_dir", "expected"),
    [
        ("*.log", "app.log", False, True),
        ("*.log", "logs/app.log", False, True),
        ("*.log", "app.log.txt", False, False),
        ("/build", "build", True, True),
        ("/build", "src/build", True, False),
        ("build/", "src/build", True, True),
        ("build/", "src/build", False, False),
        ("docs/*.md", "docs/a.md", False, True),
        ("docs/*.md", "docs/sub/a.md", False, False),
        ("docs/**/*.md", "docs/sub/deep/a.md", False, True),
        ("**/generated", "src/gen/generated", True, True),
        ("out/**", "out/x/y.o", False, True),
        ("a/**/b", "a/b", False, True),
        ("a/**/b", "a/x/y/b", False, True),
        ("file?.txt", "file1.txt", False, True),
        ("file[0-9].txt", "file7.txt", False, True),
        ("file[!0-9].txt", "file7.txt", False, False),
        ("\\#literal", "#literal", False, True),
        ("  ", "anything", False, False),
        ("# comment", "# comment", False, False),
        ("trailing   ", "trailing", False, True),
    ],
)
def test_gitignore_patterns(pattern: str, relative: str, is_dir: bool, expected: bool) -> None:
    assert _ignored(pattern, relative, is_dir=is_dir) is expected


def test_gitignore_negation_last_match_wins() -> None:
    text = "*.log\n!keep.log\n"
    assert _ignored(text, "a/app.log")
    assert not _ignored(text, "a/keep.log")
    # Re-ignored later in the file.
    assert _ignored(text + "keep.log\n", "a/keep.log")


# --------------------------------------------------------------------------- #
# workspace rules
# --------------------------------------------------------------------------- #
def test_names_are_recognised_in_any_case() -> None:
    generated = ("build", "Build", "BUILD", "node_modules", "Pods", "ThirdParty", "cmake-build-rel")
    for name in generated:
        assert is_generated_dir_name(name), name
    for name in ("src", "lib", "bin", "app", "builder"):
        assert not is_generated_dir_name(name), name
    assert is_generated_dir_name("pkg.egg-info")


def test_walk_prunes_by_name_marker_and_sibling(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    _write(ws, "src/main.cpp")
    _write(ws, "Build/out.o")
    # A CMake tree under a name nobody listed.
    _write(ws, "bld/CMakeCache.txt")
    _write(ws, "bld/generated.h")
    # A virtualenv under a name nobody listed.
    _write(ws, "env310/pyvenv.cfg")
    _write(ws, "env310/Lib/site.py")
    # A cargo-style cache directory tag.
    _write(ws, "artifacts/CACHEDIR.TAG")
    _write(ws, "artifacts/dep.rs")
    # MSBuild output beside its project file.
    _write(ws, "App/App.csproj")
    _write(ws, "App/Program.cs")
    _write(ws, "App/bin/Debug/App.dll.config")
    _write(ws, "App/obj/project.assets.json")
    # A "bin" with no project beside it is source (Rust, scripts).
    _write(ws, "tool/bin/run.sh")
    # Unreal output beside the .uproject.
    _write(ws, "Game/Game.uproject")
    _write(ws, "Game/Source/Game.cpp")
    _write(ws, "Game/Binaries/Win64/Game.txt")
    _write(ws, "Game/Intermediate/Build/x.txt")
    _write(ws, "Game/Saved/Logs/Game.log")
    # Unity output beside ProjectSettings.
    _write(ws, "Unity/ProjectSettings/ProjectVersion.txt")
    _write(ws, "Unity/Assets/Player.cs")
    _write(ws, "Unity/Library/ArtifactDB")
    _write(ws, "Unity/Temp/x.txt")
    # A hidden file and dir.
    _write(ws, ".env")
    _write(ws, ".idea/workspace.xml")

    files, pruned = _walk(ws)
    assert files == [
        "App/App.csproj",
        "App/Program.cs",
        "Game/Game.uproject",
        "Game/Source/Game.cpp",
        "Unity/Assets/Player.cs",
        "Unity/ProjectSettings/ProjectVersion.txt",
        "src/main.cpp",
        "tool/bin/run.sh",
    ]
    assert pruned == [
        "App/bin",
        "App/obj",
        "Build",
        "Game/Binaries",
        "Game/Intermediate",
        "Game/Saved",
        "Unity/Library",
        "Unity/Temp",
        "artifacts",
        "bld",
        "env310",
    ]


def test_walk_honours_ignore_files_at_every_level(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    (ws / ".git" / "info").mkdir(parents=True)
    _write(ws, ".git/info/exclude", "scratch/\n")
    _write(ws, ".gitignore", "*.log\n/generated\n!important.log\n")
    _write(ws, "pkg/.gitignore", "local/\nkeep.log\n")
    _write(ws, "app.py")
    _write(ws, "app.log")
    _write(ws, "important.log")
    _write(ws, "generated/a.py")
    _write(ws, "src/generated/b.py")
    _write(ws, "scratch/notes.txt")
    _write(ws, "pkg/mod.py")
    _write(ws, "pkg/local/x.py")
    _write(ws, "pkg/keep.log")
    _write(ws, "other/local/y.py")

    files, pruned = _walk(ws)
    assert files == [
        "app.py",
        "important.log",
        "other/local/y.py",
        "pkg/mod.py",
        "src/generated/b.py",
    ]
    assert pruned == ["generated", "pkg/local", "scratch"]

    # A single file inside an ignored directory is refused the same way.
    rules = WorkspaceIgnore(ws)
    assert not rules.file_allowed("generated/a.py")
    assert not rules.file_allowed("pkg/keep.log")
    assert rules.file_allowed("pkg/mod.py")

    # The switch turns the files off but keeps the built-in rules.
    files, _ = _walk(ws, use_ignore_files=False)
    assert "app.log" in files and "generated/a.py" in files and "scratch/notes.txt" in files


def test_nested_checkout_is_external_only_inside_a_repo(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    _write(ws, "src/a.py")
    _write(ws, "libs/thing/.git/HEAD", "ref")
    _write(ws, "libs/thing/thing.py")
    _write(ws, "libs/sub/.git", "gitdir: ../../.git/modules/sub")
    _write(ws, "libs/sub/sub.py")

    # Not a repo itself: nested repos are the user's own projects.
    files, pruned = _walk(ws)
    assert files == ["libs/sub/sub.py", "libs/thing/thing.py", "src/a.py"]
    assert pruned == []

    (ws / ".git").mkdir()
    files, pruned = _walk(ws)
    assert files == ["src/a.py"]
    assert pruned == ["libs/sub", "libs/thing"]


def test_globs_and_subtree_roots(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    _write(ws, "src/a.py")
    _write(ws, "src/a.md")
    _write(ws, "docs/guide.md")
    _write(ws, "build/x.py")

    rules = WorkspaceIgnore(ws, exclude_globs=("docs/*",), include_globs=("*.py",))
    assert sorted(p.name for p in rules.walk()) == ["a.py"]
    assert rules.file_allowed("src/a.py")
    assert not rules.file_allowed("src/a.md")
    assert not rules.file_allowed("docs/guide.md")

    # A subtree inside a refused directory yields nothing; a file root works.
    assert list(WorkspaceIgnore(ws).walk(ws / "build")) == []
    assert [p.name for p in WorkspaceIgnore(ws).walk(ws / "src" / "a.py")] == ["a.py"]
    assert list(WorkspaceIgnore(ws).walk(ws / "build" / "x.py")) == []
    assert list(WorkspaceIgnore(ws).walk(tmp_path / "elsewhere.py")) == []


@pytest.mark.skipif(os.name == "nt", reason="symlinks need a privilege on Windows")
def test_symlinks_are_skipped(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    _write(ws, "src/a.py")
    (ws / "link").symlink_to(ws / "src", target_is_directory=True)
    (ws / "src" / "b.py").symlink_to(ws / "src" / "a.py")
    files, pruned = _walk(ws)
    assert files == ["src/a.py"]
    assert pruned == []
