from __future__ import annotations

import shlex
from pathlib import PurePosixPath, PureWindowsPath

from code_ai.core.verification.models import (
    KIND_PRIORITY,
    CommandKind,
    ProjectVerification,
)

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

# Single-token runners that are genuine verification by themselves, mapped to
# what they actually exercise. The kind feeds the completion gate's strength
# check: a lint pass is real verification, but it must not masquerade as a test
# run when the project exposes a test command.
_RUNNER_KINDS: dict[str, CommandKind] = {
    # tests
    "pytest": CommandKind.TEST,
    "tox": CommandKind.TEST,
    "nox": CommandKind.TEST,
    "jest": CommandKind.TEST,
    "vitest": CommandKind.TEST,
    "mocha": CommandKind.TEST,
    "ava": CommandKind.TEST,
    "playwright": CommandKind.TEST,
    "cypress": CommandKind.TEST,
    "ctest": CommandKind.TEST,
    "phpunit": CommandKind.TEST,
    "pest": CommandKind.TEST,
    "rspec": CommandKind.TEST,
    "minitest": CommandKind.TEST,
    "bats": CommandKind.TEST,
    # typecheck
    "mypy": CommandKind.TYPECHECK,
    "pyright": CommandKind.TYPECHECK,
    "tsc": CommandKind.TYPECHECK,
    "phpstan": CommandKind.TYPECHECK,
    "psalm": CommandKind.TYPECHECK,
    # lint
    "ruff": CommandKind.LINT,
    "flake8": CommandKind.LINT,
    "pylint": CommandKind.LINT,
    "pyflakes": CommandKind.LINT,
    "eslint": CommandKind.LINT,
    "biome": CommandKind.LINT,
    "rubocop": CommandKind.LINT,
    "shellcheck": CommandKind.LINT,
    "phpcs": CommandKind.LINT,
    "phpcbf": CommandKind.LINT,
    "shfmt": CommandKind.LINT,
    # build
    "cmake": CommandKind.BUILD,
    "ninja": CommandKind.BUILD,
    "meson": CommandKind.BUILD,
    "scons": CommandKind.BUILD,
    "ant": CommandKind.BUILD,
    "kotlinc": CommandKind.BUILD,
    "javac": CommandKind.BUILD,
    "ghc": CommandKind.BUILD,
    "stack": CommandKind.BUILD,
}

# Runners that take a target/task argument deciding what actually runs; the kind
# comes from the target token, with a per-runner default for a bare invocation.
_TARGET_RUNNER_DEFAULTS: dict[str, CommandKind] = {
    "make": CommandKind.BUILD,
    "gmake": CommandKind.BUILD,
    "gradle": CommandKind.BUILD,
    "gradlew": CommandKind.BUILD,
    "mvn": CommandKind.BUILD,
    "mvnw": CommandKind.BUILD,
    "bazel": CommandKind.BUILD,
    "rake": CommandKind.TEST,
}

_TARGET_TOKEN_KINDS: dict[str, CommandKind] = {
    "test": CommandKind.TEST,
    "tests": CommandKind.TEST,
    "check": CommandKind.TEST,
    "spec": CommandKind.TEST,
    "verify": CommandKind.TEST,
    "build": CommandKind.BUILD,
    "all": CommandKind.BUILD,
    "package": CommandKind.BUILD,
    "assemble": CommandKind.BUILD,
    "compile": CommandKind.BUILD,
    "dist": CommandKind.BUILD,
    "lint": CommandKind.LINT,
    "checkstyle": CommandKind.LINT,
    "typecheck": CommandKind.TYPECHECK,
    "type-check": CommandKind.TYPECHECK,
    "vet": CommandKind.TYPECHECK,
}

# Runners whose first argument decides whether (and what) it verifies.
_SUBCOMMAND_KINDS: dict[str, dict[str, CommandKind]] = {
    "cargo": {
        "test": CommandKind.TEST,
        "bench": CommandKind.TEST,
        "nextest": CommandKind.TEST,
        "build": CommandKind.BUILD,
        "check": CommandKind.TYPECHECK,
        "clippy": CommandKind.LINT,
    },
    "go": {
        "test": CommandKind.TEST,
        "build": CommandKind.BUILD,
        "vet": CommandKind.TYPECHECK,
    },
    "dotnet": {"test": CommandKind.TEST, "build": CommandKind.BUILD},
    "composer": {"test": CommandKind.TEST, "check": CommandKind.TEST},
    "gleam": {
        "test": CommandKind.TEST,
        "build": CommandKind.BUILD,
        "check": CommandKind.TYPECHECK,
    },
    "swift": {"test": CommandKind.TEST, "build": CommandKind.BUILD},
}

_PACKAGE_MANAGERS = frozenset({"npm", "pnpm", "yarn", "bun"})
_PM_SUBCOMMAND_KINDS: dict[str, CommandKind] = {
    "test": CommandKind.TEST,
    "t": CommandKind.TEST,
    "build": CommandKind.BUILD,
    "lint": CommandKind.LINT,
    "typecheck": CommandKind.TYPECHECK,
    "type-check": CommandKind.TYPECHECK,
    "check": CommandKind.TYPECHECK,
    "tsc": CommandKind.TYPECHECK,
}

_PY_MODULE_KINDS: dict[str, CommandKind] = {
    "pytest": CommandKind.TEST,
    "unittest": CommandKind.TEST,
    "tox": CommandKind.TEST,
    "nox": CommandKind.TEST,
    "mypy": CommandKind.TYPECHECK,
    "ruff": CommandKind.LINT,
    "pylint": CommandKind.LINT,
    "pyflakes": CommandKind.LINT,
    "flake8": CommandKind.LINT,
}

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


def _normalize_argv(argv: list[str] | tuple[str, ...] | str | None) -> list[str]:
    # Tool payloads occasionally carry the command as a single string; split it
    # like a shell would so "pytest -q" is not misread as unclassifiable.
    if isinstance(argv, str):
        try:
            return shlex.split(argv)
        except ValueError:
            return argv.split()
    return list(argv or [])


def verification_kind(
    argv: list[str] | tuple[str, ...] | str | None,
    project: ProjectVerification | None = None,
) -> CommandKind | None:
    """What running ``argv`` actually verifies, or ``None`` for no verification.

    A command classifies when it matches one of the project's detected
    verification commands (whose kind wins), or a known test/build/typecheck/
    lint runner. Inspection and file-shuffling commands (``echo``, ``ls``,
    ``cat`` ...) never classify, so the completion gate cannot be satisfied
    with a trivial exit-0 command — and the returned kind lets the gate demand
    the project's *strongest* check rather than accepting a lint pass as proof
    that a behavior change works.
    """
    argv = _normalize_argv(argv)
    if not argv:
        return None

    if project is not None:
        for command in project.commands:
            if _matches_detected(argv, list(command.argv)):
                return command.kind

    head = _basename(argv[0])
    rest = argv[1:]
    if head in _TRIVIAL:
        return None
    if _is_version_probe(rest):
        return None

    if head in _RUNNER_KINDS:
        return _RUNNER_KINDS[head]
    if head in _TARGET_RUNNER_DEFAULTS:
        return _kind_from_targets(rest, default=_TARGET_RUNNER_DEFAULTS[head])
    if head in _SUBCOMMAND_KINDS:
        if not rest:
            return None
        return _SUBCOMMAND_KINDS[head].get(rest[0])
    if head == "bundle":
        # ``bundle exec <runner>``: classify by the executed runner.
        if len(rest) < 2 or rest[0] != "exec":
            return None
        return _RUNNER_KINDS.get(_basename(rest[1]), CommandKind.TEST)
    if head in _PACKAGE_MANAGERS:
        if not rest:
            return None
        if rest[0] in {"run", "run-script"}:
            if len(rest) < 2:
                return None
            # A custom script name reveals nothing about what it runs; classify
            # it as a build so it stays genuine verification without being able
            # to stand in for the project's detected test command.
            return _PM_SUBCOMMAND_KINDS.get(rest[1], CommandKind.BUILD)
        return _PM_SUBCOMMAND_KINDS.get(rest[0])
    if head in {"npx", "dlx", "pnpx"}:
        if not rest:
            return None
        runner = _basename(rest[0])
        if runner not in _NPX_RUNNERS:
            return None
        return _RUNNER_KINDS.get(runner, CommandKind.TEST)
    if head.startswith("python") or head == "py":
        if len(rest) >= 2 and rest[0] == "-m":
            return _PY_MODULE_KINDS.get(rest[1])
        return None
    if head in _COMPILERS:
        # A compiler invoked on at least one input file is a build check.
        if any(not token.startswith("-") for token in rest):
            return CommandKind.BUILD
        return None
    return None


def _kind_from_targets(rest: list[str], *, default: CommandKind) -> CommandKind | None:
    for token in rest:
        if token.startswith("-"):
            continue
        # Gradle-style paths ("app:testDebugUnitTest") classify by the last
        # segment; "test"-prefixed task names are test runs by convention.
        segment = token.rsplit(":", 1)[-1].lower()
        kind = _TARGET_TOKEN_KINDS.get(segment)
        if kind is not None:
            return kind
        if segment.startswith("test"):
            return CommandKind.TEST
    return default


def is_genuine_verification(
    argv: list[str] | tuple[str, ...] | str | None,
    project: ProjectVerification | None = None,
) -> bool:
    """Whether running ``argv`` actually verifies that implemented code works."""
    return verification_kind(argv, project) is not None


def strongest_kind(kinds: set[CommandKind]) -> CommandKind | None:
    """The most trusted kind in ``kinds`` (test > build > typecheck > lint)."""
    if not kinds:
        return None
    return min(kinds, key=lambda kind: KIND_PRIORITY[kind])


def _matches_detected(argv: list[str], detected: list[str]) -> bool:
    if _basename(argv[0]) != _basename(detected[0]):
        return False
    # The model may add flags, but the recognisable verb(s) must be present.
    return detected[1:] == argv[1 : len(detected)] or len(detected) == 1


__all__ = ["is_genuine_verification", "strongest_kind", "verification_kind"]
