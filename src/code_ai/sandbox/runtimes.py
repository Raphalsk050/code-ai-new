from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from code_ai.sandbox.layout import SandboxLayout


@dataclass(frozen=True, slots=True)
class RuntimeScratch:
    """Where one toolchain is told to put the things it writes on its own.

    ``variables`` are environment entries handed to every command the agent
    runs; ``directories`` are the paths those entries name, which the sandbox
    creates up front so a toolchain that refuses to create its own cache still
    works.
    """

    variables: dict[str, str]
    directories: tuple[Path, ...] = ()


class LanguageRuntime(Protocol):
    """Redirection rules for one language toolchain.

    Adding a language means adding one implementation and listing it in
    :data:`DEFAULT_RUNTIMES` - no existing runtime, tool, or call site changes.
    """

    name: str

    def scratch(self, layout: SandboxLayout, base: Mapping[str, str]) -> RuntimeScratch: ...


def _merge_options(base: Mapping[str, str], name: str, addition: str) -> str:
    """Append to an options-style variable instead of overwriting it.

    A user who exported ``PYTEST_ADDOPTS`` meant it; silently replacing their
    value would change how their suite runs, which is exactly the kind of
    surprise a sandbox is supposed to prevent.
    """

    existing = base.get(name, "").strip()
    if not existing:
        return addition
    if _contains_tokens(existing.split(), addition.split()):
        return existing
    return f"{existing} {addition}"


def _contains_tokens(haystack: list[str], needle: list[str]) -> bool:
    """Whether ``needle`` already appears as a consecutive run in ``haystack``.

    An option is often several tokens ("-p no:cacheprovider"), so membership has
    to be checked as a sequence; testing the whole string would also match a
    value that merely embeds it.
    """

    if not needle:
        return True
    span = len(needle)
    windows = range(len(haystack) - span + 1)
    return any(haystack[index : index + span] == needle for index in windows)


class GenericRuntime:
    """Temp and cache redirection that applies to every command, whatever it runs."""

    name = "generic"

    def scratch(self, layout: SandboxLayout, base: Mapping[str, str]) -> RuntimeScratch:
        xdg_cache = layout.cache / "xdg"
        return RuntimeScratch(
            variables={
                # POSIX and Windows spellings both matter: Python's own
                # tempfile module reads TMPDIR, TEMP and TMP in that order.
                "TMPDIR": str(layout.tmp),
                "TEMP": str(layout.tmp),
                "TMP": str(layout.tmp),
                "XDG_CACHE_HOME": str(xdg_cache),
            },
            directories=(layout.tmp, xdg_cache),
        )


class PythonRuntime:
    """Keeps ``__pycache__``, pip/mypy/ruff caches and pytest temp dirs out of the tree."""

    name = "python"

    def scratch(self, layout: SandboxLayout, base: Mapping[str, str]) -> RuntimeScratch:
        root = layout.cache / "python"
        bytecode = root / "bytecode"
        pip = root / "pip"
        mypy = root / "mypy"
        ruff = root / "ruff"
        pytest_tmp = layout.tmp / "pytest"
        return RuntimeScratch(
            variables={
                "PYTHONPYCACHEPREFIX": str(bytecode),
                "PIP_CACHE_DIR": str(pip),
                "MYPY_CACHE_DIR": str(mypy),
                "RUFF_CACHE_DIR": str(ruff),
                "PYTEST_DEBUG_TEMPROOT": str(pytest_tmp),
                # ``.pytest_cache`` is the one pytest artifact that lands in the
                # rootdir rather than a temp dir, so it has to be turned off
                # rather than relocated.
                "PYTEST_ADDOPTS": _merge_options(base, "PYTEST_ADDOPTS", "-p no:cacheprovider"),
            },
            directories=(bytecode, pip, mypy, ruff, pytest_tmp),
        )


class NodeRuntime:
    """Redirects the npm and yarn caches away from the user's home and project."""

    name = "node"

    def scratch(self, layout: SandboxLayout, base: Mapping[str, str]) -> RuntimeScratch:
        root = layout.cache / "node"
        npm = root / "npm"
        yarn = root / "yarn"
        return RuntimeScratch(
            variables={
                # npm reads the lowercase form; the uppercase one is what other
                # tooling (and older npm) looks for.
                "npm_config_cache": str(npm),
                "NPM_CONFIG_CACHE": str(npm),
                "YARN_CACHE_FOLDER": str(yarn),
            },
            directories=(npm, yarn),
        )


class RustRuntime:
    """Sends ``target/`` into the sandbox, which is the bulkiest build output there is."""

    name = "rust"

    def scratch(self, layout: SandboxLayout, base: Mapping[str, str]) -> RuntimeScratch:
        target = layout.cache / "rust" / "target"
        return RuntimeScratch(
            variables={"CARGO_TARGET_DIR": str(target)},
            directories=(target,),
        )


class GoRuntime:
    """Redirects the Go build cache and temp dir.

    ``GOMODCACHE`` is deliberately left alone: it holds downloaded modules, and
    relocating it per session would re-download the world on every run for no
    isolation benefit - the module cache never receives project output.
    """

    name = "go"

    def scratch(self, layout: SandboxLayout, base: Mapping[str, str]) -> RuntimeScratch:
        build = layout.cache / "go" / "build"
        tmp = layout.tmp / "go"
        return RuntimeScratch(
            variables={"GOCACHE": str(build), "GOTMPDIR": str(tmp)},
            directories=(build, tmp),
        )


DEFAULT_RUNTIMES: tuple[LanguageRuntime, ...] = (
    GenericRuntime(),
    PythonRuntime(),
    NodeRuntime(),
    RustRuntime(),
    GoRuntime(),
)


def build_runtime_scratch(
    layout: SandboxLayout,
    base: Mapping[str, str],
    runtimes: Iterable[LanguageRuntime] = DEFAULT_RUNTIMES,
) -> RuntimeScratch:
    """Fold every runtime's redirection into one environment and directory set.

    Later runtimes win on a shared name, so :class:`GenericRuntime` is listed
    first and language-specific rules refine it rather than fight it.
    """

    variables: dict[str, str] = {}
    directories: list[Path] = []
    for runtime in runtimes:
        scratch = runtime.scratch(layout, base)
        variables.update(scratch.variables)
        directories.extend(scratch.directories)
    # Deduplicate while keeping creation order: parents before their children.
    ordered = list(dict.fromkeys(directories))
    return RuntimeScratch(variables=variables, directories=tuple(ordered))
