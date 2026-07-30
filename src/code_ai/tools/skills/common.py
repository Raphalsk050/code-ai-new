from __future__ import annotations

import os
import re
from collections.abc import Sequence
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

# Origin label for skills stored in Code-AI's own (writable) skills directory.
# Skills read from another agent's directory carry that agent's label instead, so
# the catalog stays honest about where an instruction set came from.
NATIVE_ORIGIN = "code-ai"

_TRUTHY = frozenset({"true", "yes", "1", "on"})

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)


@dataclass(frozen=True, slots=True)
class SkillSource:
    """A directory skills are read from, and how to label what it holds."""

    root: Path
    origin: str = NATIVE_ORIGIN


@dataclass(slots=True)
class SkillRecord:
    """A skill discovered on disk."""

    name: str
    description: str
    body: str
    path: Path
    origin: str = NATIVE_ORIGIN
    # ``disabled: true`` in the frontmatter switches a skill off without deleting
    # it. A disabled skill is left out of the catalog and refuses to load, so the
    # user's own on/off decision is respected instead of silently overridden.
    disabled: bool = False

    def to_summary(self) -> dict[str, str]:
        return {"name": self.name, "description": self.description, "origin": self.origin}


def skills_root() -> Path:
    """Return the directory that holds the user's skills."""

    override = os.environ.get(SKILLS_DIR_ENV)
    if override:
        return Path(override).expanduser()
    return Path.home() / DEFAULT_CONFIG_DIRNAME / SKILLS_DIRNAME


def native_skill_source() -> SkillSource:
    """The one skill directory Code-AI itself writes to (``create_skill``)."""

    return SkillSource(root=skills_root(), origin=NATIVE_ORIGIN)


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


def _record_from_file(
    path: Path, *, fallback_name: str, origin: str = NATIVE_ORIGIN
) -> SkillRecord:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ToolExecutionError(f"Could not read skill at {path}: {exc}") from exc
    front, body = parse_skill_markdown(text)
    name = front.get("name") or fallback_name
    description = front.get("description") or ""
    return SkillRecord(
        name=name,
        description=description,
        body=body,
        path=path,
        origin=origin,
        disabled=front.get("disabled", "").strip().casefold() in _TRUTHY,
    )


def discover_skills(root: Path | None = None) -> list[SkillRecord]:
    """Find every skill under ``root`` (defaults to :func:`skills_root`).

    Two layouts are supported:

    * a directory ``<name>/SKILL.md`` (standard, allows bundled resources), and
    * a flat ``<name>.md`` file.
    """

    return discover_skills_from([SkillSource(root=root or skills_root())])


def discover_skills_from(sources: Sequence[SkillSource]) -> list[SkillRecord]:
    """Find every skill across ``sources``, earlier sources winning by name.

    Several directories are searched because a user's reusable instructions are
    often already on disk in another agent's layout (see :mod:`code_ai.interop`).
    Merging them by name keeps one flat catalog for the model while letting the
    user's own Code-AI skill shadow a same-named foreign one.
    """

    records: dict[str, SkillRecord] = {}
    for source in sources:
        for name, record in _discover_in_root(source).items():
            if record.disabled:
                continue
            records.setdefault(name.casefold(), record)
    return [records[key] for key in sorted(records)]


def _discover_in_root(source: SkillSource) -> dict[str, SkillRecord]:
    base = source.root
    if not base.is_dir():
        return {}
    try:
        children = sorted(base.iterdir(), key=lambda p: p.name.casefold())
    except OSError:
        # Unreadable directory (permissions, stale mount): treat it as empty so
        # one bad location cannot hide the skills in every other one.
        return {}
    records: dict[str, SkillRecord] = {}
    # Directory-form skills take precedence over a flat file of the same name.
    for child in children:
        if child.is_dir():
            entry = child / SKILL_ENTRYPOINT
            if entry.is_file():
                records[child.name] = _record_from_file(
                    entry, fallback_name=child.name, origin=source.origin
                )
        elif child.is_file() and child.suffix.lower() == ".md":
            stem = child.stem
            if stem.casefold() == "skill":
                continue
            records.setdefault(
                stem, _record_from_file(child, fallback_name=stem, origin=source.origin)
            )
    return records


def render_skills_catalog(sources: Sequence[SkillSource] | None = None) -> str:
    """Render the available skills as a compact catalog for the system prompt.

    Injecting the catalog (name + one-line description) means the model always
    knows which skills exist and can load the fitting one on its own initiative,
    instead of having to remember to run a discovery call first - the reason
    skills were ignored whenever the user did not name them explicitly. Kept
    cheap on purpose: only names and short descriptions travel in the prompt;
    full skill bodies stay on disk and load on demand via ``use_skill``.
    Returns ``""`` when there are no skills, so the prompt stays clean.
    """

    skills = discover_skills_from(sources or [native_skill_source()])
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
        label = record.name
        if record.origin != NATIVE_ORIGIN:
            label = f"{record.name} ({record.origin})"
        lines.append(f"- {label}: {description}" if description else f"- {label}")
    return "\n".join(lines).strip()


def load_skill(name: str, *, root: Path | None = None) -> SkillRecord:
    """Load a single skill by name, raising if it does not exist."""

    return load_skill_from(name, sources=[SkillSource(root=root or skills_root())])


def load_skill_from(name: str, *, sources: Sequence[SkillSource]) -> SkillRecord:
    """Load a skill by name from the first source that defines it."""

    slug = sanitize_skill_name(name)
    for source in sources:
        entry = source.root / slug / SKILL_ENTRYPOINT
        if entry.is_file():
            return _enabled(_record_from_file(entry, fallback_name=slug, origin=source.origin))
        flat = source.root / f"{slug}.md"
        if flat.is_file():
            return _enabled(_record_from_file(flat, fallback_name=slug, origin=source.origin))

    # Fall back to the declared names in the catalog: a skill authored elsewhere
    # can declare a frontmatter name that does not match its filename, and the
    # model only ever sees the declared name.
    discovered = discover_skills_from(sources)
    wanted = {slug, str(name or "").strip().casefold()}
    for record in discovered:
        if record.name.casefold() in wanted:
            return record

    available = ", ".join(record.name for record in discovered) or "(none)"
    raise ToolExecutionError(f"Skill not found: {slug}. Available skills: {available}.")


def _enabled(record: SkillRecord) -> SkillRecord:
    """Return the record, or refuse when its author switched it off."""

    if record.disabled:
        raise ToolExecutionError(
            f"Skill '{record.name}' is disabled in its frontmatter ({record.path}). "
            "Remove 'disabled: true' there to use it again."
        )
    return record
