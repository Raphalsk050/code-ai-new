from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

# Mandatory instructions the agent must always follow, injected into the system
# prompt every session. Two scopes, mirroring how memories split global vs
# project: ``global`` rules apply install-wide, ``project`` rules live inside the
# workspace and travel with the repository (like Cline's ``.clinerules``).
#
# Rules are read from *sources* rather than from two fixed directories, so a
# workspace that already carries rules authored for another agent (Cline's
# ``.clinerules``) is honoured without the user moving or duplicating anything.
# The foreign layouts themselves live in :mod:`code_ai.interop`; this module only
# knows how to read a directory or a single file of markdown rules.

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)

# Origin label for rules authored through Code-AI itself (``create_rule``).
NATIVE_ORIGIN = "code-ai"

# Extensions read from a rules directory by default. Foreign sources may widen
# this (Cline treats every file in ``.clinerules/`` as a rule).
DEFAULT_RULE_EXTENSIONS = frozenset({".md"})


@dataclass(frozen=True, slots=True)
class RuleSource:
    """A place rules are read from.

    ``path`` is either a directory of rule files or a single rule file, so both
    of Cline's layouts (a bare ``.clinerules`` file and a ``.clinerules/``
    directory) map onto one source. A missing path is not an error: the loader
    skips it, which is what makes third-party support free for users who never
    installed that other agent.
    """

    path: Path
    scope: str  # "global" or "project"
    origin: str = NATIVE_ORIGIN
    recursive: bool = False
    # Sub-directory names ignored while walking a recursive source. Cline keeps
    # workflows and skills inside its rules directory, and those are loaded as
    # workflows/skills, not as always-on rules.
    exclude_dirs: frozenset[str] = field(default_factory=frozenset)
    extensions: frozenset[str] = DEFAULT_RULE_EXTENSIONS


@dataclass(slots=True)
class RuleRecord:
    """A rule loaded from disk."""

    name: str
    scope: str  # "global" or "project"
    body: str
    path: Path
    origin: str = NATIVE_ORIGIN


def _parse_rule_markdown(text: str) -> tuple[str, str]:
    """Return ``(title, body)`` from a rule file's optional frontmatter.

    Only the minimal single-line ``key: value`` frontmatter rules need is read,
    with no external YAML dependency (the same shape skill files use). The title
    is the ``name`` field when present; the body is everything after the
    frontmatter (or the whole file when there is none).
    """

    match = _FRONTMATTER_RE.match(text)
    if not match:
        return "", text.strip()
    raw_front, body = match.group(1), match.group(2)
    title = ""
    for line in raw_front.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("name:"):
            title = stripped.partition(":")[2].strip().strip("'\"")
            break
    return title, body.strip()


class RulesService:
    """Loads mandatory rules from disk and renders them for the system prompt."""

    def __init__(
        self,
        *,
        global_dir: Path,
        project_dir: Path,
        extra_sources: Sequence[RuleSource] = (),
    ) -> None:
        # Native sources first so the user's own Code-AI rules lead each scope;
        # extras (rules authored for other agents) follow in the order given.
        self._sources: tuple[RuleSource, ...] = (
            RuleSource(path=global_dir, scope="global"),
            RuleSource(path=project_dir, scope="project"),
            *extra_sources,
        )

    @property
    def sources(self) -> tuple[RuleSource, ...]:
        return self._sources

    def load(self) -> list[RuleRecord]:
        """Return every rule, global scope first, each source sorted by filename.

        A file reachable from two sources is loaded once (first source wins), so
        overlapping conventions cannot make the model read the same rule twice.
        """

        records: list[RuleRecord] = []
        seen: set[Path] = set()
        # Stable sort: globals before project rules, native before foreign.
        for source in sorted(self._sources, key=lambda item: 0 if item.scope == "global" else 1):
            records.extend(self._load_source(source, seen=seen))
        return records

    def _load_source(self, source: RuleSource, *, seen: set[Path]) -> list[RuleRecord]:
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
            title, body = _parse_rule_markdown(text)
            if not body:
                continue
            records.append(
                RuleRecord(
                    name=title or path.stem,
                    scope=source.scope,
                    body=body,
                    path=path,
                    origin=source.origin,
                )
            )
        return records

    def _rule_files(self, source: RuleSource) -> Iterable[Path]:
        path = source.path
        if path.is_file():
            return [path]
        if not path.is_dir():
            return []
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
            # An unreadable directory yields no rules rather than failing the
            # prompt build: one bad location must not silence every other rule.
            return []
        return sorted(candidates, key=lambda item: str(item).casefold())

    @staticmethod
    def _excluded(candidate: Path, source: RuleSource) -> bool:
        if not source.exclude_dirs:
            return False
        relative = candidate.relative_to(source.path)
        return any(part.casefold() in source.exclude_dirs for part in relative.parts[:-1])

    def render_for_prompt(self) -> str:
        """Render all rules as a single binding section, or "" when there are none.

        The heading is deliberately forceful: rules are not advisory context, and
        the model must treat them as overriding its own defaults. Each rule keeps
        its title so a rule can be referenced by name, and its origin so a rule
        the user wrote for another agent is recognisable as theirs.
        """

        records = self.load()
        if not records:
            return ""

        lines = [
            "# Rules (MANDATORY - always follow, never violate)",
            "",
            "These rules are binding for every action this session. They override your "
            "own defaults and general guidance. If a rule conflicts with a request, "
            "follow the rule and say so.",
            "",
        ]
        for record in records:
            label = record.scope
            if record.origin != NATIVE_ORIGIN:
                label = f"{record.scope}, {record.origin}"
            lines.append(f"## {record.name} ({label})")
            lines.append(record.body)
            lines.append("")
        return "\n".join(lines).strip()
