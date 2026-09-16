"""Which parts of a workspace are the project, and which are its exhaust.

Build trees, dependency checkouts, virtualenvs and caches hold more text than
the source they came from, and none of it answers a question about the
project. The rules here are deliberately language-agnostic: a directory is
recognised by what it contains or by what sits next to it, not only by its
name, so a CMake tree called ``bld`` or a virtualenv called ``env310`` is
skipped without anybody listing it.

Signals, cheapest first:

- hidden names (``.git``, ``.venv``, ``.idea`` ...)
- names that mean build output or third-party code in every ecosystem
- ``.gitignore`` and ``.ignore`` at every level, plus ``.git/info/exclude``
- files only a generator leaves behind (``CMakeCache.txt``, ``pyvenv.cfg``,
  ``CACHEDIR.TAG`` ...)
- output directories beside the project file that produced them (``bin`` and
  ``obj`` next to a ``.csproj``, ``Binaries`` next to a ``.uproject``)
- a nested git checkout inside a git workspace: a submodule or vendored clone

Every directory is listed once and the answers are memoised, so the walk costs
the same as a plain ``os.walk``.
"""

from __future__ import annotations

import fnmatch
import os
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

# Directory names that hold build output or downloaded code in some ecosystem
# and are never the project's own source under that name. Compared
# case-insensitively: Windows tooling capitalises them.
GENERATED_DIR_NAMES: frozenset[str] = frozenset(
    {
        # build output
        "build",
        "builds",
        "_build",
        "dist",
        "out",
        "output",
        "target",
        "cmakefiles",
        "_deps",
        "bazel-out",
        "bazel-bin",
        "bazel-testlogs",
        "zig-cache",
        "zig-out",
        "dist-newstyle",
        "deriveddata",
        "xcuserdata",
        # caches, coverage, interpreters
        "__pycache__",
        "site-packages",
        "coverage",
        "htmlcov",
        "venv",
        # fetched by a package manager
        "node_modules",
        "bower_components",
        "jspm_packages",
        "vendor",
        "vendors",
        "pods",
        "carthage",
        "deps",
        # vendored third-party sources
        "third_party",
        "thirdparty",
        "third-party",
        "3rdparty",
        "external",
        "externals",
        "extern",
    }
)
# Hidden names the tools must still refuse when asked to list hidden entries.
HIDDEN_GENERATED_DIR_NAMES: frozenset[str] = frozenset(
    {
        ".cache",
        ".git",
        ".hg",
        ".svn",
        ".mypy_cache",
        ".nox",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        ".gradle",
        ".idea",
        ".vs",
        ".next",
        ".nuxt",
        ".svelte-kit",
        ".turbo",
        ".parcel-cache",
        ".dart_tool",
        ".stack-work",
        ".zig-cache",
        ".terraform",
        ".eggs",
        ".yarn",
    }
)
# What list_files and search_code refuse by name. The index goes further.
DEFAULT_EXCLUDES: frozenset[str] = GENERATED_DIR_NAMES | HIDDEN_GENERATED_DIR_NAMES
GENERATED_DIR_PREFIXES = ("cmake-build-",)
GENERATED_DIR_SUFFIXES = (".egg-info", ".dist-info")
# A file with one of these names is left only by a generator, whatever the
# directory is called.
GENERATED_DIR_MARKERS: frozenset[str] = frozenset(
    name.casefold()
    for name in (
        "CACHEDIR.TAG",  # cargo, pip, pytest, ruff, uv: bford.info/cachedir
        "CMakeCache.txt",  # any CMake build tree
        "build.ninja",
        ".ninja_log",
        ".ninja_deps",
        "pyvenv.cfg",  # any virtualenv
        "conaninfo.txt",
        "conanbuildinfo.txt",
        "project.assets.json",  # dotnet obj/
        "ArtifactDB",  # Unity Library/
        "LibraryFormatVersion.txt",
    )
)
# Output directories recognised by the project file beside them. An entry that
# starts with a dot is a suffix, anything else is an exact sibling name.
SIBLING_RULES: tuple[tuple[frozenset[str], tuple[str, ...]], ...] = (
    (
        frozenset(
            {
                "bin",
                "obj",
                "debug",
                "release",
                "x64",
                "x86",
                "win32",
                "arm64",
                "packages",
                "testresults",
            }
        ),
        (".csproj", ".fsproj", ".vbproj", ".vcxproj", ".sln", ".slnx"),
    ),
    (
        frozenset({"binaries", "intermediate", "saved", "deriveddatacache"}),
        (".uproject", ".uplugin"),
    ),
    (
        frozenset({"library", "temp", "logs", "obj", "usersettings"}),
        ("ProjectSettings",),
    ),
)
IGNORE_FILES = (".gitignore", ".ignore")


def is_generated_dir_name(name: str) -> bool:
    folded = name.casefold()
    return (
        folded in DEFAULT_EXCLUDES
        or folded.startswith(GENERATED_DIR_PREFIXES)
        or folded.endswith(GENERATED_DIR_SUFFIXES)
    )


# --------------------------------------------------------------------------- #
# gitignore
# --------------------------------------------------------------------------- #
@dataclass(slots=True, frozen=True)
class IgnoreRule:
    regex: re.Pattern[str]
    negate: bool
    dir_only: bool

    def matches(self, relative: str, *, is_dir: bool) -> bool:
        if self.dir_only and not is_dir:
            return False
        return self.regex.search(relative) is not None


def parse_ignore_file(text: str) -> list[IgnoreRule]:
    """gitignore(5) syntax: comments, negation, anchoring, ``**`` and classes."""

    rules: list[IgnoreRule] = []
    for raw in text.splitlines():
        rule = _parse_line(raw)
        if rule is not None:
            rules.append(rule)
    return rules


def _parse_line(raw: str) -> IgnoreRule | None:
    line = raw.rstrip("\r")
    if not line or line.lstrip().startswith("#"):
        return None
    # Trailing blanks are ignored unless escaped.
    line = re.sub(r"(?<!\\)\s+$", "", line)
    negate = False
    if line.startswith("!"):
        negate = True
        line = line[1:]
    elif line.startswith(("\\!", "\\#")):
        line = line[1:]
    dir_only = line.endswith("/")
    if dir_only:
        line = line.rstrip("/")
    anchored = "/" in line
    line = line.lstrip("/")
    if not line:
        return None
    return IgnoreRule(re.compile(_glob_regex(line, anchored)), negate, dir_only)


def _glob_regex(pattern: str, anchored: bool) -> str:
    out: list[str] = []
    i, n = 0, len(pattern)
    while i < n:
        char = pattern[i]
        if char == "*":
            if pattern.startswith("**", i):
                if pattern.startswith("**/", i):
                    out.append("(?:.*/)?")
                    i += 3
                else:
                    out.append(".*")
                    i += 2
                continue
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        elif char == "[":
            end = _class_end(pattern, i)
            if end is None:
                out.append("\\[")
            else:
                body = pattern[i + 1 : end]
                if body.startswith("!"):
                    body = "^" + body[1:]
                out.append("[" + body.replace("\\", "\\\\") + "]")
                i = end + 1
                continue
        elif char == "\\" and i + 1 < n:
            out.append(re.escape(pattern[i + 1]))
            i += 2
            continue
        else:
            out.append(re.escape(char))
        i += 1
    return ("^" if anchored else "(?:^|/)") + "".join(out) + "$"


def _class_end(pattern: str, start: int) -> int | None:
    j = start + 1
    if j < len(pattern) and pattern[j] in "!^":
        j += 1
    if j < len(pattern) and pattern[j] == "]":
        j += 1
    while j < len(pattern) and pattern[j] != "]":
        j += 1
    return j if j < len(pattern) else None


# --------------------------------------------------------------------------- #
# Workspace rules
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class _Listing:
    names: frozenset[str] = frozenset()
    folded: frozenset[str] = frozenset()
    dirs: tuple[str, ...] = ()
    files: tuple[str, ...] = ()
    symlinks: frozenset[str] = frozenset()


@dataclass(slots=True)
class WorkspaceIgnore:
    """Answers "is this path part of the project?" for one workspace.

    Directory listings are memoised, so build one per walk and throw it away:
    a stale instance would not see a directory created after it was made.
    """

    workspace: Path
    exclude_globs: tuple[str, ...] = ()
    include_globs: tuple[str, ...] = ()
    use_ignore_files: bool = True
    # Directories the walk refused, workspace-relative, for the report.
    pruned: list[str] = field(default_factory=list)
    _listings: dict[str, _Listing] = field(default_factory=dict)
    _dir_verdicts: dict[str, bool] = field(default_factory=dict)
    _rules: dict[str, tuple[IgnoreRule, ...]] = field(default_factory=dict)
    _nested_repos: bool = False

    def __post_init__(self) -> None:
        self.workspace = Path(self.workspace)
        self.exclude_globs = tuple(self.exclude_globs)
        self.include_globs = tuple(self.include_globs)
        self._nested_repos = (self.workspace / ".git").exists()

    # -- public ----------------------------------------------------------- #
    def relative(self, path: Path) -> str | None:
        try:
            return Path(path).relative_to(self.workspace).as_posix()
        except ValueError:
            return None

    def dir_allowed(self, relative: str) -> bool:
        relative = relative.strip("/")
        if relative in ("", "."):
            return True
        verdict = self._dir_verdicts.get(relative)
        if verdict is None:
            parent, _, name = relative.rpartition("/")
            verdict = self.dir_allowed(parent) and self._check_dir(parent, name, relative)
            self._dir_verdicts[relative] = verdict
        return verdict

    def file_allowed(self, relative: str) -> bool:
        relative = relative.strip("/")
        parent, _, name = relative.rpartition("/")
        if not name or name.startswith("."):
            return False
        if not self.dir_allowed(parent):
            return False
        if name in self._listing(parent).symlinks:
            return False
        if self.use_ignore_files and self._ignored(relative, is_dir=False):
            return False
        if self.exclude_globs and _matches_any(relative, self.exclude_globs):
            return False
        return not self.include_globs or _matches_any(relative, self.include_globs)

    def walk(self, root: Path | None = None) -> Iterator[Path]:
        """Files under ``root`` (a directory or a single file) that belong to the project."""

        root = Path(root) if root is not None else self.workspace
        relative = self.relative(root)
        if relative is None:
            return
        if relative == ".":
            relative = ""
        if root.is_file():
            if self.file_allowed(relative):
                yield root
            return
        if not root.is_dir() or not self.dir_allowed(relative):
            return
        yield from self._walk_dir(root, relative)

    # -- internals -------------------------------------------------------- #
    def _walk_dir(self, directory: Path, relative: str) -> Iterator[Path]:
        listing = self._listing(relative)
        for name in listing.files:
            child = f"{relative}/{name}" if relative else name
            if self.file_allowed(child):
                yield directory / name
        for name in listing.dirs:
            child = f"{relative}/{name}" if relative else name
            if self.dir_allowed(child):
                yield from self._walk_dir(directory / name, child)
            elif not name.startswith("."):
                # Hidden directories are always skipped; only the judgement
                # calls are worth reporting.
                self.pruned.append(child)

    def _check_dir(self, parent: str, name: str, relative: str) -> bool:
        if name.startswith(".") or is_generated_dir_name(name):
            return False
        siblings = self._listing(parent)
        if name in siblings.symlinks:
            return False
        if _sibling_rule_hits(name, siblings):
            return False
        listing = self._listing(relative)
        if listing.folded & GENERATED_DIR_MARKERS:
            return False
        if self._nested_repos and ".git" in listing.names:
            return False
        if self.use_ignore_files and self._ignored(relative, is_dir=True):
            return False
        return not (self.exclude_globs and _matches_any(relative, self.exclude_globs))

    def _ignored(self, relative: str, *, is_dir: bool) -> bool:
        # Deeper ignore files override shallower ones; within a file the last
        # matching line wins. Each file sees paths relative to its own directory.
        parts = relative.split("/")
        ignored = False
        for depth in range(len(parts)):
            rules = self._rules_for("/".join(parts[:depth]))
            if not rules:
                continue
            sub = "/".join(parts[depth:])
            for rule in rules:
                if rule.matches(sub, is_dir=is_dir):
                    ignored = not rule.negate
        return ignored

    def _rules_for(self, relative: str) -> tuple[IgnoreRule, ...]:
        rules = self._rules.get(relative)
        if rules is None:
            listing = self._listing(relative)
            base = self.workspace / relative if relative else self.workspace
            sources: list[Path] = []
            if not relative and ".git" in listing.names:
                sources.append(base / ".git" / "info" / "exclude")
            sources.extend(base / name for name in IGNORE_FILES if name in listing.names)
            collected: list[IgnoreRule] = []
            for source in sources:
                try:
                    text = source.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                collected.extend(parse_ignore_file(text))
            rules = tuple(collected)
            self._rules[relative] = rules
        return rules

    def _listing(self, relative: str) -> _Listing:
        listing = self._listings.get(relative)
        if listing is None:
            listing = _scan(self.workspace / relative if relative else self.workspace)
            self._listings[relative] = listing
        return listing


def _scan(directory: Path) -> _Listing:
    names: list[str] = []
    dirs: list[str] = []
    files: list[str] = []
    symlinks: set[str] = set()
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                names.append(entry.name)
                try:
                    if entry.is_symlink():
                        symlinks.add(entry.name)
                        continue
                    if entry.is_dir():
                        dirs.append(entry.name)
                    elif entry.is_file():
                        files.append(entry.name)
                except OSError:
                    continue
    except OSError:
        return _Listing()
    return _Listing(
        names=frozenset(names),
        folded=frozenset(name.casefold() for name in names),
        dirs=tuple(sorted(dirs)),
        files=tuple(sorted(files)),
        symlinks=frozenset(symlinks),
    )


def _sibling_rule_hits(name: str, siblings: _Listing) -> bool:
    folded = name.casefold()
    for dir_names, markers in SIBLING_RULES:
        if folded not in dir_names:
            continue
        for marker in markers:
            if marker.startswith("."):
                if any(sibling.endswith(marker) for sibling in siblings.folded):
                    return True
            elif marker in siblings.names:
                return True
    return False


def _matches_any(relative: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatch(relative, pattern) for pattern in patterns)


__all__ = [
    "DEFAULT_EXCLUDES",
    "GENERATED_DIR_MARKERS",
    "GENERATED_DIR_NAMES",
    "IgnoreRule",
    "WorkspaceIgnore",
    "is_generated_dir_name",
    "parse_ignore_file",
]
