from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# Mandatory instructions the agent must always follow, injected into the system
# prompt every session. Two scopes, mirroring how memories split global vs
# project: ``global`` rules apply install-wide, ``project`` rules live inside the
# workspace and travel with the repository (like Cline's ``.clinerules``).

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)


@dataclass(slots=True)
class RuleRecord:
    """A rule loaded from disk."""

    name: str
    scope: str  # "global" or "project"
    body: str
    path: Path


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

    def __init__(self, *, global_dir: Path, project_dir: Path) -> None:
        self._global_dir = global_dir
        self._project_dir = project_dir

    def load(self) -> list[RuleRecord]:
        """Return every rule, global first then project, each sorted by filename."""

        return [
            *self._load_dir(self._global_dir, scope="global"),
            *self._load_dir(self._project_dir, scope="project"),
        ]

    def _load_dir(self, directory: Path, *, scope: str) -> list[RuleRecord]:
        if not directory.is_dir():
            return []
        records: list[RuleRecord] = []
        for path in sorted(directory.glob("*.md"), key=lambda p: p.name.casefold()):
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            title, body = _parse_rule_markdown(text)
            if not body:
                continue
            records.append(
                RuleRecord(name=title or path.stem, scope=scope, body=body, path=path)
            )
        return records

    def render_for_prompt(self) -> str:
        """Render all rules as a single binding section, or "" when there are none.

        The heading is deliberately forceful: rules are not advisory context, and
        the model must treat them as overriding its own defaults. Each rule keeps
        its title so a rule can be referenced by name.
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
            lines.append(f"## {record.name} ({record.scope})")
            lines.append(record.body)
            lines.append("")
        return "\n".join(lines).strip()
