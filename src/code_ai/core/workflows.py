from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from code_ai.config.defaults import global_workflows_dir, project_workflows_dir

# Workflows are saved, named procedures the user (or the agent) can run on
# demand: "release a version", "triage a bug report", "open a PR". They sit
# between rules (always on) and skills (loaded when the task matches): a workflow
# runs when it is invoked by name, and its body is the script for that run.
#
# The format is a plain markdown file per workflow, which is exactly what Cline
# writes into ``.clinerules/workflows``. Reading those directly means a user who
# already authored workflows for Cline can invoke them here with no migration.

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)

NATIVE_ORIGIN = "code-ai"

# Cline invokes a workflow by its filename (``/deploy.md``), so both the bare
# name and the name with a markdown suffix must resolve to the same workflow.
WORKFLOW_EXTENSIONS = (".md", ".markdown")

_MAX_DESCRIPTION_CHARS = 200

# A bullet or numbered step ("- do x", "1. do x"), i.e. a line that is part of the
# procedure rather than a description of it.
_STEP_RE = re.compile(r"^(?:[-*+]\s|\d+[.)]\s)")


@dataclass(frozen=True, slots=True)
class WorkflowSource:
    """A directory workflows are read from."""

    root: Path
    scope: str  # "global" or "project"
    origin: str = NATIVE_ORIGIN


@dataclass(slots=True)
class WorkflowRecord:
    """A workflow discovered on disk."""

    name: str
    description: str
    body: str
    path: Path
    scope: str
    origin: str = NATIVE_ORIGIN

    @property
    def command(self) -> str:
        """The slash command that runs this workflow."""

        return f"/{self.name}"

    def to_summary(self) -> dict[str, str]:
        return {
            "name": self.name,
            "description": self.description,
            "scope": self.scope,
            "origin": self.origin,
        }


def native_workflow_sources(workspace: Path | str) -> list[WorkflowSource]:
    """Workflow directories owned by Code-AI itself.

    Global workflows live beside the config (``~/.code-ai/workflows``); project
    workflows live in the workspace (``<workspace>/.code-ai/workflows``) so they
    can be committed and shared with the team, exactly like project rules.
    """

    return [
        WorkflowSource(root=global_workflows_dir(), scope="global"),
        WorkflowSource(root=project_workflows_dir(workspace), scope="project"),
    ]


def normalize_workflow_name(value: str) -> str:
    """Reduce a user-typed workflow reference to a comparable name.

    Accepts ``deploy``, ``/deploy``, ``deploy.md`` and any casing, so a workflow
    can be invoked the way Cline spells it or the way a slash command reads.
    """

    text = str(value or "").strip()
    if text.startswith("/"):
        text = text[1:]
    lowered = text.casefold()
    for suffix in WORKFLOW_EXTENSIONS:
        if lowered.endswith(suffix):
            text = text[: -len(suffix)]
            break
    return text.strip().casefold()


def render_workflow_invocation(record: WorkflowRecord, argument: str = "") -> str:
    """Turn a workflow into the message that runs it.

    Invoking a workflow means handing the model the saved steps as the task, the
    way Cline injects the file's content into the conversation. Anything the user
    typed after the command name travels along as extra input, so
    ``/release 1.4.0`` still carries the version.
    """

    lines = [
        f'Run the "{record.name}" workflow saved at {record.path}.',
        "",
        "Follow its steps below in order, exactly as written: they are the "
        "specification for this task, not a suggestion. If a step cannot be "
        "carried out, say which one and why instead of silently improvising a "
        "different procedure.",
        "",
        record.body.strip(),
    ]
    extra = argument.strip()
    if extra:
        lines.extend(["", f"Additional input for this run: {extra}"])
    return "\n".join(lines)


def _parse_workflow_markdown(text: str) -> tuple[dict[str, str], str]:
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


def _derive_description(front: dict[str, str], body: str) -> str:
    """Best available one-liner for the catalog.

    Workflows written for other agents carry no frontmatter, so the body has to
    speak for itself. A summary sentence is the most informative label, a title
    the next best; the steps themselves are skipped, because "1. Build" describes
    nothing to a reader choosing between workflows.
    """

    described = front.get("description", "").strip()
    if described:
        return described
    heading = ""
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line or set(line) <= {"-", "=", "*", "_", "`"}:
            continue
        if line.startswith("#"):
            heading = heading or line.lstrip("#").strip()
            continue
        if _STEP_RE.match(line):
            continue
        return line
    return heading


class WorkflowService:
    """Discovers workflows on disk and renders them for the system prompt."""

    def __init__(self, *, sources: Sequence[WorkflowSource]) -> None:
        self._sources = tuple(sources)

    @property
    def sources(self) -> tuple[WorkflowSource, ...]:
        return self._sources

    def load(self) -> list[WorkflowRecord]:
        """Every workflow, sorted by name; the first source to define a name wins.

        Precedence follows source order, so a project workflow can shadow a
        global one of the same name and Code-AI's own directory takes priority
        over a third-party layout.
        """

        records: dict[str, WorkflowRecord] = {}
        for source in self._sources:
            for record in self._load_source(source):
                records.setdefault(record.name.casefold(), record)
        return [records[key] for key in sorted(records)]

    def find(self, name: str) -> WorkflowRecord | None:
        """Resolve a workflow by name, tolerating ``/name`` and ``name.md``."""

        wanted = normalize_workflow_name(name)
        if not wanted:
            return None
        for record in self.load():
            if record.name.casefold() == wanted:
                return record
        return None

    def _load_source(self, source: WorkflowSource) -> list[WorkflowRecord]:
        root = source.root
        if not root.is_dir():
            return []
        try:
            entries = sorted(root.iterdir(), key=lambda item: item.name.casefold())
        except OSError:
            # An unreadable directory (permissions, a stale mount) is treated as
            # empty: a third-party location the user cannot read must not break
            # discovery of the ones they can.
            return []
        records: list[WorkflowRecord] = []
        for path in entries:
            if not path.is_file() or path.name.startswith("."):
                continue
            if path.suffix.casefold() not in WORKFLOW_EXTENSIONS:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            front, body = _parse_workflow_markdown(text)
            if not body:
                continue
            records.append(
                WorkflowRecord(
                    name=front.get("name") or path.stem,
                    description=_derive_description(front, body),
                    body=body,
                    path=path,
                    scope=source.scope,
                    origin=source.origin,
                )
            )
        return records

    def render_for_prompt(self) -> str:
        """Render the available workflows as a compact catalog, or "" if none.

        Only names and one-line descriptions travel in the prompt; a workflow's
        steps load on demand via ``use_workflow``. The model needs the catalog
        because the user invokes a workflow by name ("/deploy", "run the deploy
        workflow") and would otherwise get an improvised procedure instead of the
        one they wrote down.
        """

        records = self.load()
        if not records:
            return ""
        lines = [
            "# Available workflows",
            "",
            "Saved procedures for this session. When the user invokes one by name "
            '(for example "/deploy" or "run the deploy workflow"), or the request '
            "plainly matches one, load it with use_workflow(\"<name>\") and follow its "
            "steps in order instead of improvising your own procedure.",
            "",
        ]
        for record in records:
            description = " ".join(record.description.split())
            if len(description) > _MAX_DESCRIPTION_CHARS:
                description = description[: _MAX_DESCRIPTION_CHARS - 3].rstrip() + "..."
            lines.append(f"- {record.name}: {description}" if description else f"- {record.name}")
        return "\n".join(lines).strip()
