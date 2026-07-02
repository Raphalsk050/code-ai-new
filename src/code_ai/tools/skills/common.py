from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from code_ai.config.defaults import DEFAULT_CONFIG_DIRNAME
from code_ai.core.errors import ToolArgumentError, ToolExecutionError

# Skills live under ``~/.code-ai/skills`` by default. The location is overridable
# via an environment variable so tests (and alternate setups) never touch the
# real home directory.
SKILLS_DIR_ENV = "CODE_AI_SKILLS_DIR"
SKILLS_DIRNAME = "skills"

# Canonical entrypoint filename for a directory-form skill (the Anthropic skill
# convention). Flat ``<name>.md`` files are also discovered as skills.
SKILL_ENTRYPOINT = "SKILL.md"

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)


@dataclass(slots=True)
class SkillRecord:
    """A skill discovered on disk."""

    name: str
    description: str
    body: str
    path: Path

    def to_summary(self) -> dict[str, str]:
        return {"name": self.name, "description": self.description}


def skills_root() -> Path:
    """Return the directory that holds the user's skills."""

    override = os.environ.get(SKILLS_DIR_ENV)
    if override:
        return Path(override).expanduser()
    return Path.home() / DEFAULT_CONFIG_DIRNAME / SKILLS_DIRNAME


def sanitize_skill_name(value: str) -> str:
    """Normalise a skill name to a safe slug or raise ``ToolArgumentError``.

    Lowercases, turns whitespace into hyphens, and rejects anything that could
    escape the skills directory (separators, dots, traversal).
    """

    raw = str(value or "").strip().lower()
    if not raw:
        raise ToolArgumentError("name is required.")
    slug = re.sub(r"\s+", "-", raw)
    if not _NAME_RE.match(slug):
        raise ToolArgumentError(
            "name must contain only lowercase letters, digits, '-' or '_' "
            "and start with a letter or digit."
        )
    return slug


def parse_skill_markdown(text: str) -> tuple[dict[str, str], str]:
    """Split a skill markdown file into ``(frontmatter, body)``.

    Only the minimal ``key: value`` single-line frontmatter that skills need is
    parsed (no external YAML dependency). Files without frontmatter yield an
    empty mapping and the whole text as the body.
    """

    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text.strip()
    raw_front, body = match.group(1), match.group(2)
    front: dict[str, str] = {}
    for line in raw_front.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, _, raw_value = stripped.partition(":")
        front[key.strip().lower()] = raw_value.strip().strip("'\"")
    return front, body.strip()


def render_skill_markdown(*, name: str, description: str, instructions: str) -> str:
    """Render a skill markdown file with standard frontmatter."""

    body = instructions.strip()
    return (
        "---\n"
        f"name: {name}\n"
        f"description: {description.strip()}\n"
        "---\n\n"
        f"{body}\n"
    )


def _record_from_file(path: Path, *, fallback_name: str) -> SkillRecord:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ToolExecutionError(f"Could not read skill at {path}: {exc}") from exc
    front, body = parse_skill_markdown(text)
    name = front.get("name") or fallback_name
    description = front.get("description") or ""
    return SkillRecord(name=name, description=description, body=body, path=path)


def discover_skills(root: Path | None = None) -> list[SkillRecord]:
    """Find every skill under ``root`` (defaults to :func:`skills_root`).

    Two layouts are supported:

    * a directory ``<name>/SKILL.md`` (standard, allows bundled resources), and
    * a flat ``<name>.md`` file.
    """

    base = root or skills_root()
    if not base.is_dir():
        return []
    records: dict[str, SkillRecord] = {}
    # Directory-form skills take precedence over a flat file of the same name.
    for child in sorted(base.iterdir(), key=lambda p: p.name.casefold()):
        if child.is_dir():
            entry = child / SKILL_ENTRYPOINT
            if entry.is_file():
                records[child.name] = _record_from_file(entry, fallback_name=child.name)
        elif child.is_file() and child.suffix.lower() == ".md":
            stem = child.stem
            if stem.casefold() == "skill":
                continue
            records.setdefault(stem, _record_from_file(child, fallback_name=stem))
    return [records[key] for key in sorted(records, key=str.casefold)]


def render_skills_catalog(root: Path | None = None) -> str:
    """Render the available skills as a compact catalog for the system prompt.

    Injecting the catalog (name + one-line description) means the model always
    knows which skills exist and can load the fitting one on its own initiative,
    instead of having to remember to run a discovery call first - the reason
    skills were ignored whenever the user did not name them explicitly. Kept
    cheap on purpose: only names and short descriptions travel in the prompt;
    full skill bodies stay on disk and load on demand via ``use_skill``.
    Returns ``""`` when there are no skills, so the prompt stays clean.
    """

    skills = discover_skills(root)
    if not skills:
        return ""
    lines = [
        "# Available skills",
        "",
        'These skills are available this session. When the current task matches one, '
        'immediately load it with use_skill("<name>") and follow its instructions '
        "before proceeding - do this on your own initiative, even if the user did not "
        "mention the skill. Only skip a matching skill for a trivial one-shot answer.",
        "",
    ]
    for record in skills:
        description = " ".join(record.description.split())
        if len(description) > 200:
            description = description[:197].rstrip() + "..."
        lines.append(f"- {record.name}: {description}" if description else f"- {record.name}")
    return "\n".join(lines).strip()


def load_skill(name: str, *, root: Path | None = None) -> SkillRecord:
    """Load a single skill by name, raising if it does not exist."""

    slug = sanitize_skill_name(name)
    base = root or skills_root()
    entry = base / slug / SKILL_ENTRYPOINT
    if entry.is_file():
        return _record_from_file(entry, fallback_name=slug)
    flat = base / f"{slug}.md"
    if flat.is_file():
        return _record_from_file(flat, fallback_name=slug)
    available = ", ".join(record.name for record in discover_skills(base)) or "(none)"
    raise ToolExecutionError(f"Skill not found: {slug}. Available skills: {available}.")
