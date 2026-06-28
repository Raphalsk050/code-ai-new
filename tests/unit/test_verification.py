from __future__ import annotations

from code_ai.core.verification import (
    CommandKind,
    detect_project_verification,
    is_genuine_verification,
)


# --------------------------------------------------------------------------- #
# Detection
# --------------------------------------------------------------------------- #
def test_detects_python_pytest(tmp_path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    project = detect_project_verification(tmp_path)

    assert "python" in project.ecosystems
    primary = project.primary()
    assert primary is not None and primary.kind == CommandKind.TEST
    assert "pytest" in primary.argv


def test_detects_python_venv_interpreter(tmp_path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").write_text("", encoding="utf-8")
    project = detect_project_verification(tmp_path)

    assert project.primary().argv[0] == ".venv/bin/python"


def test_detects_node_scripts(tmp_path) -> None:
    (tmp_path / "package.json").write_text(
        '{"scripts": {"test": "jest", "build": "tsc"}}', encoding="utf-8"
    )
    (tmp_path / "pnpm-lock.yaml").write_text("", encoding="utf-8")
    project = detect_project_verification(tmp_path)

    kinds = {cmd.kind for cmd in project.commands}
    assert CommandKind.TEST in kinds and CommandKind.BUILD in kinds
    assert project.primary().argv == ("pnpm", "run", "test")


def test_detects_rust_go_and_make(tmp_path) -> None:
    (tmp_path / "Cargo.toml").write_text("[package]\nname = 'x'\n", encoding="utf-8")
    rust = detect_project_verification(tmp_path).primary()
    assert rust.argv == ("cargo", "test")

    (tmp_path / "Cargo.toml").unlink()
    (tmp_path / "go.mod").write_text("module x\n", encoding="utf-8")
    go = detect_project_verification(tmp_path).primary()
    assert go.argv == ("go", "test", "./...")

    (tmp_path / "go.mod").unlink()
    (tmp_path / "Makefile").write_text("test:\n\techo hi\nbuild:\n\techo hi\n", encoding="utf-8")
    make = detect_project_verification(tmp_path)
    assert ("make", "test") in {cmd.argv for cmd in make.commands}


def test_greenfield_has_no_commands(tmp_path) -> None:
    project = detect_project_verification(tmp_path)

    assert project.has_any is False
    assert "No test/build system" in project.prompt_hint()


# --------------------------------------------------------------------------- #
# Matching
# --------------------------------------------------------------------------- #
def test_genuine_runners_count_as_verification() -> None:
    assert is_genuine_verification(["pytest", "-q"]) is True
    assert is_genuine_verification([".venv/bin/python", "-m", "pytest"]) is True
    assert is_genuine_verification(["cargo", "test"]) is True
    assert is_genuine_verification(["go", "test", "./..."]) is True
    assert is_genuine_verification(["npm", "run", "build"]) is True
    assert is_genuine_verification(["npm", "test"]) is True
    assert is_genuine_verification(["make", "test"]) is True
    assert is_genuine_verification(["npx", "vitest"]) is True
    assert is_genuine_verification(["gcc", "main.c", "-o", "main"]) is True


def test_trivial_commands_do_not_count() -> None:
    assert is_genuine_verification(["echo", "ok"]) is False
    assert is_genuine_verification(["ls", "-la"]) is False
    assert is_genuine_verification(["cat", "file.py"]) is False
    assert is_genuine_verification(["pytest", "--version"]) is False
    assert is_genuine_verification(["go", "run", "main.go"]) is False
    assert is_genuine_verification(["npm", "install"]) is False
    assert is_genuine_verification(["python", "-c", "print(1)"]) is False
    assert is_genuine_verification(["gcc", "--version"]) is False
    assert is_genuine_verification([]) is False
