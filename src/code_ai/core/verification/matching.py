from __future__ import annotations

from pathlib import PurePosixPath, PureWindowsPath

from code_ai.core.verification.models import ProjectVerification

# Commands that inspect or move things around but prove nothing about whether the
# implemented code works. Running one of these must never satisfy verification.
_TRIVIAL = frozenset(
    {
        "echo", "printf", "ls", "cat", "pwd", "cd", "true", "false", "clear",
        "which", "type", "env", "export", "set", "date", "whoami", "sleep",
        "head", "tail", "wc", "sort", "uniq", "tree", "find", "grep", "rg",
        "mkdir", "touch", "rm", "cp", "mv", "ln", "chmod", "chown", "dirname",
        "basename", "realpath", "stat", "cut", "sed", "awk", "tee", "xargs",
    }
)

# Single-token runners that are genuine verification by themselves.
_RUNNERS = frozenset(
    {
        # python
        "pytest", "tox", "nox", "mypy", "ruff", "flake8", "pylint", "pyright", "pyflakes",
        # js / ts
        "jest", "vitest", "mocha", "ava", "tsc", "eslint", "biome", "playwright", "cypress",
        # build / make
        "make", "gmake", "cmake", "ctest", "ninja", "bazel", "meson", "scons", "ant",
        # jvm
        "gradle", "gradlew", "mvn", "mvnw", "kotlinc", "javac",
        # php
        "phpunit", "phpstan", "psalm", "pest", "phpcs", "phpcbf",
        # ruby
        "rspec", "rake", "rubocop", "minitest",
        # c / c++ / others
        "gcc", "g++", "clang", "clang++", "cc", "c++", "rustc", "ghc", "stack",
        # shell
        "shellcheck", "bats", "shfmt",
    }
)

# Runners whose first argument decides whether it is verification.
_SUBCOMMANDS: dict[str, frozenset[str]] = {
    "cargo": frozenset({"test", "build", "check", "clippy", "bench", "nextest"}),
    "go": frozenset({"test", "build", "vet"}),
    "dotnet": frozenset({"test", "build"}),
    "composer": frozenset({"test", "check"}),
    "bundle": frozenset({"exec"}),
    "gleam": frozenset({"test", "build", "check"}),
    "swift": frozenset({"test", "build"}),
}

_PACKAGE_MANAGERS = frozenset({"npm", "pnpm", "yarn", "bun"})
_PM_SUBCOMMANDS = frozenset({"test", "t", "build", "lint", "typecheck", "type-check", "check"})

_PY_MODULES = frozenset(
    {"pytest", "unittest", "mypy", "tox", "nox", "ruff", "pylint", "pyflakes", "flake8"}
)
_NPX_RUNNERS = frozenset(
    {"jest", "vitest", "tsc", "eslint", "mocha", "playwright", "cypress", "ava", "biome"}
)
_COMPILERS = frozenset(
    {"gcc", "g++", "clang", "clang++", "cc", "c++", "rustc", "javac", "kotlinc", "ghc"}
)


def _basename(token: str) -> str:
    # argv[0] may be a path like ".venv/bin/pytest" or "vendor\\bin\\phpunit".
    name = PurePosixPath(PureWindowsPath(token).as_posix()).name
    return name[:-4] if name.endswith(".exe") else name


def _is_version_probe(rest: list[str]) -> bool:
    return bool(rest) and all(
        token in {"--version", "-V", "-version", "--help", "-h", "version"} for token in rest
    )


def is_genuine_verification(
    argv: list[str] | tuple[str, ...] | None,
    project: ProjectVerification | None = None,
) -> bool:
    """Whether running ``argv`` actually verifies that implemented code works.

    A command counts when it matches one of the project's detected verification
    commands, or a known test/build/typecheck/lint runner. Inspection and
    file-shuffling commands (``echo``, ``ls``, ``cat`` ...) never count, so the
    completion gate cannot be satisfied with a trivial exit-0 command.
    """
    argv = list(argv or [])
    if not argv:
        return False

    if project is not None:
        for command in project.commands:
            if _matches_detected(argv, list(command.argv)):
                return True

    head = _basename(argv[0])
    rest = argv[1:]
    if head in _TRIVIAL:
        return False
    if _is_version_probe(rest):
        return False

    if head in _RUNNERS:
        return True
    if head in _SUBCOMMANDS:
        return bool(rest) and rest[0] in _SUBCOMMANDS[head]
    if head in _PACKAGE_MANAGERS:
        if not rest:
            return False
        if rest[0] in {"run", "run-script"}:
            return len(rest) > 1
        return rest[0] in _PM_SUBCOMMANDS
    if head in {"npx", "dlx", "pnpx"}:
        return bool(rest) and _basename(rest[0]) in _NPX_RUNNERS
    if head.startswith("python") or head in {"py", "python"}:
        return len(rest) >= 2 and rest[0] == "-m" and rest[1] in _PY_MODULES
    if head in _COMPILERS:
        # A compiler invoked on at least one input file is a build check.
        return any(not token.startswith("-") for token in rest)
    return False


def _matches_detected(argv: list[str], detected: list[str]) -> bool:
    if _basename(argv[0]) != _basename(detected[0]):
        return False
    # The model may add flags, but the recognisable verb(s) must be present.
    return detected[1:] == argv[1 : len(detected)] or len(detected) == 1


__all__ = ["is_genuine_verification"]
