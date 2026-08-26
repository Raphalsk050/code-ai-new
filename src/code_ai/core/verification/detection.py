from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path

from code_ai.core.verification.models import (
    KIND_PRIORITY,
    CommandKind,
    ProjectVerification,
    VerificationCommand,
)

# An adapter inspects a workspace and returns (ecosystem_label, commands). It
# must be pure and side-effect free: read files, return data. Adding a new
# language is one adapter appended to ``_ADAPTERS``.
Adapter = Callable[[Path], "list[VerificationCommand]"]


def detect_project_verification(workspace: Path) -> ProjectVerification:
    """Detect the verification commands a workspace exposes.

    Runs every ecosystem adapter, aggregates their commands, de-duplicates, and
    orders them by trust (test > build > typecheck > lint). Detection never
    raises: a malformed manifest degrades to whatever the other adapters find.
    """
    commands: list[VerificationCommand] = []
    ecosystems: list[str] = []
    for label, adapter in _ADAPTERS:
        try:
            found = adapter(workspace)
        except Exception:
            found = []
        if found:
            ecosystems.append(label)
            commands.extend(found)

    seen: set[tuple[str, ...]] = set()
    unique: list[VerificationCommand] = []
    for command in sorted(commands, key=lambda c: KIND_PRIORITY[c.kind]):
        if command.argv in seen:
            continue
        seen.add(command.argv)
        unique.append(command)
    return ProjectVerification(commands=tuple(unique), ecosystems=tuple(dict.fromkeys(ecosystems)))


def _exists(workspace: Path, *names: str) -> bool:
    return any((workspace / name).exists() for name in names)


def _python_interpreter(workspace: Path) -> str:
    for candidate in (".venv/bin/python", "venv/bin/python", ".venv/Scripts/python.exe"):
        if (workspace / candidate).exists():
            return candidate
    return "python"


def _python(workspace: Path) -> list[VerificationCommand]:
    manifests = ["pyproject.toml", "setup.py", "setup.cfg", "pytest.ini", "tox.ini"]
    has_tests = (workspace / "tests").is_dir() or any(
        workspace.glob("test_*.py")
    ) or any(workspace.glob("*_test.py"))
    if not (_exists(workspace, *manifests) or has_tests):
        return []
    py = _python_interpreter(workspace)
    source = next((m for m in manifests if (workspace / m).exists()), "tests/")
    commands = [
        VerificationCommand(
            kind=CommandKind.TEST,
            argv=(py, "-m", "pytest", "-q"),
            description="Run the Python test suite with pytest.",
            source=source,
        )
    ]
    config_text = _read(workspace, "pyproject.toml")
    if "[tool.mypy]" in config_text or _exists(workspace, "mypy.ini", ".mypy.ini"):
        commands.append(
            VerificationCommand(
                kind=CommandKind.TYPECHECK,
                argv=(py, "-m", "mypy", "."),
                description="Type-check the project with mypy.",
                source="mypy config",
            )
        )
    if "[tool.ruff]" in config_text or _exists(workspace, "ruff.toml", ".ruff.toml"):
        commands.append(
            VerificationCommand(
                kind=CommandKind.LINT,
                argv=(py, "-m", "ruff", "check", "."),
                description="Lint the project with ruff.",
                source="ruff config",
            )
        )
    return commands


def _node(workspace: Path) -> list[VerificationCommand]:
    if not _exists(workspace, "package.json"):
        return []
    pm = _node_package_manager(workspace)
    try:
        data = json.loads((workspace / "package.json").read_text(encoding="utf-8"))
    except (ValueError, OSError):
        data = {}
    scripts = data.get("scripts") or {}
    commands: list[VerificationCommand] = []
    script_kinds = (
        ("test", CommandKind.TEST),
        ("build", CommandKind.BUILD),
        ("typecheck", CommandKind.TYPECHECK),
        ("type-check", CommandKind.TYPECHECK),
        ("tsc", CommandKind.TYPECHECK),
        ("lint", CommandKind.LINT),
    )
    for name, kind in script_kinds:
        if name in scripts:
            commands.append(
                VerificationCommand(
                    kind=kind,
                    argv=(pm, "run", name),
                    description=f"Run the '{name}' script via {pm}.",
                    source="package.json",
                )
            )
    if not any(c.kind == CommandKind.TYPECHECK for c in commands) and _exists(
        workspace, "tsconfig.json"
    ):
        commands.append(
            VerificationCommand(
                kind=CommandKind.TYPECHECK,
                argv=("npx", "tsc", "--noEmit"),
                description="Type-check TypeScript without emitting output.",
                source="tsconfig.json",
            )
        )
    return commands


def _node_package_manager(workspace: Path) -> str:
    if _exists(workspace, "pnpm-lock.yaml"):
        return "pnpm"
    if _exists(workspace, "yarn.lock"):
        return "yarn"
    if _exists(workspace, "bun.lockb"):
        return "bun"
    return "npm"


def _rust(workspace: Path) -> list[VerificationCommand]:
    if not _exists(workspace, "Cargo.toml"):
        return []
    return [
        VerificationCommand(CommandKind.TEST, ("cargo", "test"), "Run Rust tests.", "Cargo.toml"),
        VerificationCommand(
            CommandKind.BUILD, ("cargo", "build"), "Build the Rust crate.", "Cargo.toml"
        ),
        VerificationCommand(
            CommandKind.LINT, ("cargo", "clippy"), "Lint Rust with clippy.", "Cargo.toml"
        ),
    ]


def _go(workspace: Path) -> list[VerificationCommand]:
    if not _exists(workspace, "go.mod"):
        return []
    return [
        VerificationCommand(
            CommandKind.TEST, ("go", "test", "./..."), "Run Go tests.", "go.mod"
        ),
        VerificationCommand(
            CommandKind.BUILD, ("go", "build", "./..."), "Build all Go packages.", "go.mod"
        ),
        VerificationCommand(
            CommandKind.TYPECHECK, ("go", "vet", "./..."), "Vet Go packages.", "go.mod"
        ),
    ]


def _jvm(workspace: Path) -> list[VerificationCommand]:
    # Covers Java and Kotlin via Gradle or Maven.
    gradle_manifests = (
        "build.gradle",
        "build.gradle.kts",
        "settings.gradle",
        "settings.gradle.kts",
    )
    if _exists(workspace, *gradle_manifests):
        runner = "./gradlew" if _exists(workspace, "gradlew") else "gradle"
        return [
            VerificationCommand(
                CommandKind.TEST, (runner, "test"), "Run JVM tests via Gradle.", "Gradle"
            ),
            VerificationCommand(
                CommandKind.BUILD, (runner, "build"), "Build the project via Gradle.", "Gradle"
            ),
        ]
    if _exists(workspace, "pom.xml"):
        runner = "./mvnw" if _exists(workspace, "mvnw") else "mvn"
        return [
            VerificationCommand(
                CommandKind.TEST, (runner, "test"), "Run JVM tests via Maven.", "pom.xml"
            ),
            VerificationCommand(
                CommandKind.BUILD, (runner, "package"), "Build the project via Maven.", "pom.xml"
            ),
        ]
    return []


def _cpp(workspace: Path) -> list[VerificationCommand]:
    if not _exists(workspace, "CMakeLists.txt"):
        return []
    return [
        VerificationCommand(
            CommandKind.BUILD,
            ("cmake", "--build", "build"),
            "Build the CMake project.",
            "CMakeLists.txt",
        ),
        VerificationCommand(
            CommandKind.TEST,
            ("ctest", "--test-dir", "build", "--output-on-failure"),
            "Run CMake/CTest tests.",
            "CMakeLists.txt",
        ),
    ]


def _php(workspace: Path) -> list[VerificationCommand]:
    commands: list[VerificationCommand] = []
    if _exists(workspace, "phpunit.xml", "phpunit.xml.dist"):
        runner = "vendor/bin/phpunit" if (workspace / "vendor/bin/phpunit").exists() else "phpunit"
        commands.append(
            VerificationCommand(
                CommandKind.TEST, (runner,), "Run PHP tests with PHPUnit.", "phpunit.xml"
            )
        )
    if _exists(workspace, "composer.json"):
        try:
            data = json.loads((workspace / "composer.json").read_text(encoding="utf-8"))
        except (ValueError, OSError):
            data = {}
        if "test" in (data.get("scripts") or {}):
            commands.append(
                VerificationCommand(
                    CommandKind.TEST,
                    ("composer", "test"),
                    "Run the composer 'test' script.",
                    "composer.json",
                )
            )
    return commands


def _shell(workspace: Path) -> list[VerificationCommand]:
    commands: list[VerificationCommand] = []
    if any(workspace.rglob("*.bats")):
        commands.append(
            VerificationCommand(
                CommandKind.TEST, ("bats", "."), "Run Bats shell tests.", "*.bats"
            )
        )
    if _exists(workspace, ".shellcheckrc") and any(workspace.rglob("*.sh")):
        commands.append(
            VerificationCommand(
                CommandKind.LINT, ("shellcheck", "*.sh"), "Lint shell scripts.", ".shellcheckrc"
            )
        )
    return commands


def _dotnet(workspace: Path) -> list[VerificationCommand]:
    if not (any(workspace.glob("*.sln")) or any(workspace.rglob("*.csproj"))):
        return []
    return [
        VerificationCommand(
            CommandKind.TEST, ("dotnet", "test"), "Run .NET tests.", ".NET project"
        ),
        VerificationCommand(
            CommandKind.BUILD, ("dotnet", "build"), "Build the .NET project.", ".NET project"
        ),
    ]


_MAKE_TARGET = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.-]*)\s*:(?!=)", re.MULTILINE)


def _make(workspace: Path) -> list[VerificationCommand]:
    name = next(
        (n for n in ("Makefile", "makefile", "GNUmakefile") if (workspace / n).exists()),
        None,
    )
    if name is None:
        return []
    targets = set(_MAKE_TARGET.findall(_read(workspace, name)))
    wanted = (
        ("test", CommandKind.TEST),
        ("check", CommandKind.TEST),
        ("build", CommandKind.BUILD),
        ("lint", CommandKind.LINT),
    )
    commands = [
        VerificationCommand(
            kind=kind,
            argv=("make", target),
            description=f"Run the make {target} target.",
            source=name,
        )
        for target, kind in wanted
        if target in targets
    ]
    if not any(c.kind == CommandKind.BUILD for c in commands) and "all" in targets:
        commands.append(
            VerificationCommand(
                kind=CommandKind.BUILD,
                argv=("make", "all"),
                description="Run the make all target.",
                source=name,
            )
        )
    return commands


def _read(workspace: Path, name: str) -> str:
    path = workspace / name
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return ""


# Order is informational only; detect_project_verification re-sorts by trust.
_ADAPTERS: tuple[tuple[str, Adapter], ...] = (
    ("python", _python),
    ("node", _node),
    ("rust", _rust),
    ("go", _go),
    ("jvm", _jvm),
    ("cpp", _cpp),
    ("php", _php),
    ("dotnet", _dotnet),
    ("shell", _shell),
    ("make", _make),
)


__all__ = ["detect_project_verification"]
