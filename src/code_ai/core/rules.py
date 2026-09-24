from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from code_ai.util.frontmatter import as_list, as_text, split_frontmatter

# Mandatory instructions injected into the system prompt every session, in two
# scopes like memories: ``global`` (install-wide) and ``project`` (committed with
# the workspace). Rules come from *sources*, so files written for other agents
# are read where they are; the foreign layouts live in :mod:`code_ai.interop`.

# Origin label for rules authored through Code-AI itself (``create_rule``).
NATIVE_ORIGIN = "code-ai"

# Extensions read from a rules directory by default. Foreign sources may widen
# this (Cline treats every file in ``.clinerules/`` as a rule).
DEFAULT_RULE_EXTENSIONS = frozenset({".md"})

# Claude Code follows imports four hops deep.
_MAX_IMPORT_DEPTH = 4
_MAX_IMPORT_BYTES = 256_000

_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_CODE_SPAN_RE = re.compile(r"`[^`]*`")
_IMPORT_RE = re.compile(r"(?<![\w@`/\\])@([^\s`'\"<>()\[\]{}]+)")
_IMPORT_ONLY_LINE_RE = re.compile(r"^\s*(?:[-*+]\s+)?(?:@[^\s`'\"<>()\[\]{}]+\s*)+$")
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


@dataclass(frozen=True, slots=True)
class RuleSource:
    """A directory of rule files, or a single rule file. A missing path is skipped."""

    path: Path
    scope: str  # "global" or "project"
    origin: str = NATIVE_ORIGIN
    recursive: bool = False
    # Sub-directory names skipped while walking a recursive source: Cline keeps
    # workflows and skills inside its rules directory.
    exclude_dirs: frozenset[str] = field(default_factory=frozenset)
    extensions: frozenset[str] = DEFAULT_RULE_EXTENSIONS
    # An instruction file (``CODEAI.md``, ``AGENTS.md``...) rather than one rule
    # among many: rendered last, under a heading that says it outranks the rest.
    authoritative: bool = False
    # Names each file by where it lives ("packages/web/AGENTS.md", "~/.codex/AGENTS.md")
    # instead of by its title; "" is the repository root.
    display_dir: str | None = None
    # Read instead of ``path`` when it exists (``AGENTS.override.md``).
    override: Path | None = None
    # Read, first one found, when ``path`` is missing.
    fallbacks: tuple[Path, ...] = ()
    # Claude Code syntax: ``@path`` pulls another file in, and block HTML
    # comments are notes for maintainers, not for the model.
    expand_imports: bool = False
    strip_comments: bool = False
    # Imports must resolve under this directory; None lets them go anywhere. A
    # repository cannot import ~/.ssh into the prompt.
    import_root: Path | None = None


@dataclass(slots=True)
class RuleRecord:
    """A rule loaded from disk."""

    name: str
    scope: str  # "global" or "project"
    body: str
    path: Path
    origin: str = NATIVE_ORIGIN
    authoritative: bool = False
    # Globs from ``paths:``: the rule only governs matching files.
    paths: tuple[str, ...] = ()


def _parse_rule_markdown(text: str) -> tuple[str, str, tuple[str, ...]]:
    """``(title, body, paths)`` from a rule file and its optional frontmatter."""

    front, body = split_frontmatter(text)
    paths = as_list(front.get("paths") or front.get("globs"))
    return as_text(front.get("name")), body, tuple(paths)


class RulesService:
    """Loads mandatory rules from disk and renders them for the system prompt."""

    def __init__(
        self,
        *,
        global_dir: Path,
        project_dir: Path,
        extra_sources: Sequence[RuleSource] = (),
    ) -> None:
        # Native sources lead each scope; foreign ones follow in the order given.
        self._sources: tuple[RuleSource, ...] = (
            RuleSource(path=global_dir, scope="global"),
            RuleSource(path=project_dir, scope="project"),
            *extra_sources,
        )

    @property
    def sources(self) -> tuple[RuleSource, ...]:
        return self._sources

    def load(self) -> list[RuleRecord]:
        """Every rule, global scope first, each source sorted by filename.

        A file reached twice - through two sources, an import, or a copy with the
        same text - is loaded once, where it is first met.
        """

        records: list[RuleRecord] = []
        seen: set[Path] = set()
        bodies: set[str] = set()
        for source in sorted(self._sources, key=lambda item: 0 if item.scope == "global" else 1):
            records.extend(self._load_source(source, seen=seen, bodies=bodies))
        return records

    def _load_source(
        self, source: RuleSource, *, seen: set[Path], bodies: set[str]
    ) -> list[RuleRecord]:
        records: list[RuleRecord] = []
        for path in self._rule_files(source):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            title, body, paths = _parse_rule_markdown(text)
            if source.strip_comments:
                body = _strip_html_comments(body)
            if source.expand_imports:
                body = _expand_imports(
                    body, path.parent, seen=seen, depth=1, root=source.import_root
                )
            body = body.strip()
            digest = hashlib.sha256(" ".join(body.split()).encode("utf-8")).hexdigest()
            if not body or digest in bodies:
                continue
            bodies.add(digest)
            records.append(
                RuleRecord(
                    name=_record_name(source, path, title),
                    scope=source.scope,
                    body=body,
                    path=path,
                    origin=source.origin,
                    authoritative=source.authoritative,
                    paths=paths,
                )
            )
        return records

    def _rule_files(self, source: RuleSource) -> Iterable[Path]:
        if source.override is not None and source.override.is_file():
            return [source.override]
        path = source.path
        if path.is_file():
            return [path]
        if not path.is_dir():
            return [fallback for fallback in source.fallbacks if fallback.is_file()][:1]
        pattern = "**/*" if source.recursive else "*"
        try:
            candidates = [
                candidate
                for candidate in path.glob(pattern)
                if candidate.is_file()
                and not candidate.name.startswith(".")
                and candidate.suffix.casefold() in source.extensions
                and not self._excluded(candidate, source)
            ]
        except OSError:
            # One unreadable location must not silence every other rule.
            return []
        return sorted(candidates, key=lambda item: str(item).casefold())

    @staticmethod
    def _excluded(candidate: Path, source: RuleSource) -> bool:
        if not source.exclude_dirs:
            return False
        relative = candidate.relative_to(source.path)
        return any(part.casefold() in source.exclude_dirs for part in relative.parts[:-1])

    def render_for_prompt(self) -> str:
        """All rules as one binding section, or "" when there are none.

        Instruction files come last under their own heading: they exist to change
        how the agent works, so they must outrank both the rules and the built-in
        guidance further down, and position alone does not say that.
        """

        records = self.load()
        if not records:
            return ""

        ordinary = [record for record in records if not record.authoritative]
        instructions = [record for record in records if record.authoritative]

        lines: list[str] = []
        if ordinary:
            lines += [
                "# Rules (MANDATORY - always follow, never violate)",
                "",
                "These rules are binding for every action this session. They override "
                "your own defaults and general guidance. If a rule conflicts with a "
                "request, follow the rule and say so.",
                "",
            ]
            lines += self._render_records(ordinary)
        if instructions:
            lines += [
                "# Project instructions (HIGHEST PRIORITY)",
                "",
                "The following was written for this workspace and outranks everything "
                "else in this prompt, including any guidance further down and any rule "
                "above. Where it contradicts your usual way of working, it wins - it "
                "exists precisely to change that. Later sections outrank earlier ones. "
                "If it conflicts with what the user is asking for, follow it and say "
                "so rather than resolving the conflict silently.",
                "",
            ]
            lines += self._render_records(instructions)
        return "\n".join(lines).strip()

    @staticmethod
    def _render_records(records: list[RuleRecord]) -> list[str]:
        lines: list[str] = []
        for record in records:
            label = record.scope
            if record.origin != NATIVE_ORIGIN:
                label = f"{record.scope}, {record.origin}"
            lines.append(f"## {record.name} ({label})")
            if record.paths:
                lines.append(
                    "Applies only to files matching "
                    + ", ".join(f"`{pattern}`" for pattern in record.paths)
                    + ": follow it whenever you read or change one of them."
                )
            lines.append(record.body)
            lines.append("")
        return lines


def _record_name(source: RuleSource, path: Path, title: str) -> str:
    if source.display_dir is None:
        return title or path.stem
    return f"{source.display_dir}/{path.name}" if source.display_dir else path.name


def _outside_fences(text: str) -> list[tuple[str, bool]]:
    """``(line, fenced)`` for every line."""

    lines: list[tuple[str, bool]] = []
    fence = ""
    for line in text.split("\n"):
        match = _FENCE_RE.match(line)
        if match and (not fence or match.group(1) == fence):
            fence = "" if fence else match.group(1)
            lines.append((line, True))
            continue
        lines.append((line, bool(fence)))
    return lines


def _strip_html_comments(text: str) -> str:
    kept: list[str] = []
    chunk: list[str] = []
    for line, fenced in _outside_fences(text):
        if fenced:
            if chunk:
                kept.append(_HTML_COMMENT_RE.sub("", "\n".join(chunk)))
                chunk = []
            kept.append(line)
        else:
            chunk.append(line)
    if chunk:
        kept.append(_HTML_COMMENT_RE.sub("", "\n".join(chunk)))
    return "\n".join(kept)


def _expand_imports(
    text: str, base: Path, *, seen: set[Path], depth: int, root: Path | None
) -> str:
    """Inline every ``@path`` that names a readable file, the way Claude Code does.

    A line made only of imports is replaced by them; an import mentioned inside a
    sentence keeps the sentence and adds the file right after it. Code spans and
    fenced blocks are left alone, and a file already loaded is not repeated.
    """

    if depth > _MAX_IMPORT_DEPTH:
        return text
    out: list[str] = []
    for line, fenced in _outside_fences(text):
        if fenced or "@" not in line:
            out.append(line)
            continue
        visible = _CODE_SPAN_RE.sub(lambda match: " " * len(match.group(0)), line)
        imported: list[str] = []
        found = False
        for match in _IMPORT_RE.finditer(visible):
            target = _resolve_import(match.group(1), base, root)
            if target is None:
                continue
            found = True
            if target in seen:
                continue
            seen.add(target)
            try:
                content = target.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            _, body = split_frontmatter(content)
            imported.append(
                _expand_imports(body, target.parent, seen=seen, depth=depth + 1, root=root)
            )
        # "@someone" that names no file is a mention, not an import: keep it.
        if found and _IMPORT_ONLY_LINE_RE.match(visible):
            out.extend(imported)
        else:
            out.append(line)
            out.extend(imported)
    return "\n".join(out)


def _resolve_import(raw: str, base: Path, root: Path | None) -> Path | None:
    candidate = raw.rstrip(".,;:!?")
    if not candidate:
        return None
    if candidate.startswith("~"):
        path = Path(candidate).expanduser()
    else:
        path = Path(candidate)
        if not path.is_absolute():
            path = base / path
    try:
        resolved = path.resolve()
        if not resolved.is_file() or resolved.stat().st_size > _MAX_IMPORT_BYTES:
            return None
    except OSError:
        return None
    if root is not None and not resolved.is_relative_to(root):
        return None
    return resolved
