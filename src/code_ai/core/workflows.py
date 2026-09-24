from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from code_ai.config.defaults import global_workflows_dir, project_workflows_dir
from code_ai.util import jsonc
from code_ai.util.arguments import OPENCODE, fill_arguments
from code_ai.util.frontmatter import as_list, as_text, is_true, split_frontmatter

# Workflows are saved, named procedures run on demand ("/deploy"): between rules
# (always on) and skills (loaded when the task matches). One markdown file per
# workflow - the shape Cline's workflows, Claude Code's commands, Codex's prompts
# and OpenCode's commands all share, so theirs run here with no migration.

NATIVE_ORIGIN = "code-ai"

# Cline invokes a workflow by its filename (``/deploy.md``), so both the bare
# name and the name with a markdown suffix must resolve to the same workflow.
WORKFLOW_EXTENSIONS = (".md", ".markdown")

# Codex runs its prompts as ``/prompts:<name>``.
_INVOCATION_PREFIXES = ("prompts:",)

KIND_MARKDOWN = "markdown"
# The ``command`` map of an ``opencode.json``.
KIND_OPENCODE_JSON = "opencode-json"

_MAX_DESCRIPTION_CHARS = 200

# A bullet or numbered step ("- do x", "1. do x"), i.e. a line that is part of the
# procedure rather than a description of it.
_STEP_RE = re.compile(r"^(?:[-*+]\s|\d+[.)]\s)")
_SHELL_RE = re.compile(r"!`[^`]+`|^\s*```!", re.MULTILINE)
_FILE_REFERENCE_RE = re.compile(r"(?<![\w@`])@[\w.~/\\-]*[\w/\\]")


@dataclass(frozen=True, slots=True)
class WorkflowSource:
    """Where workflows are read from."""

    root: Path
    scope: str  # "global" or "project"
    origin: str = NATIVE_ORIGIN
    # Placeholder syntax of the files (see :mod:`code_ai.util.arguments`); "" has none.
    dialect: str = ""
    recursive: bool = False
    # A nested file runs as ``/dir:name`` (Claude Code's commands).
    namespaced: bool = False
    kind: str = KIND_MARKDOWN


@dataclass(slots=True)
class WorkflowRecord:
    """A workflow discovered on disk."""

    name: str
    description: str
    body: str
    path: Path
    scope: str
    origin: str = NATIVE_ORIGIN
    dialect: str = ""
    # Names for ``$name`` placeholders, in argument order.
    arguments: tuple[str, ...] = ()
    # ``disable-model-invocation: true``: only the user starts it.
    model_invocable: bool = True

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
    """Code-AI's own workflow directories: ``~/.code-ai/workflows`` and the workspace's."""

    return [
        WorkflowSource(root=global_workflows_dir(), scope="global"),
        WorkflowSource(root=project_workflows_dir(workspace), scope="project"),
    ]


def normalize_workflow_name(value: str) -> str:
    """``deploy``, ``/deploy``, ``deploy.md`` or ``/prompts:deploy``, any casing."""

    text = str(value or "").strip()
    if text.startswith("/"):
        text = text[1:]
    lowered = text.casefold()
    for prefix in _INVOCATION_PREFIXES:
        if lowered.startswith(prefix):
            text = text[len(prefix) :]
            lowered = text.casefold()
            break
    for suffix in WORKFLOW_EXTENSIONS:
        if lowered.endswith(suffix):
            text = text[: -len(suffix)]
            break
    return text.strip().casefold()


def render_workflow_invocation(record: WorkflowRecord, argument: str = "") -> str:
    """The message that runs a workflow: its steps as the task, plus the user's input.

    Placeholders take the input where the workflow has them; otherwise it travels
    along as extra input, so ``/release 1.4.0`` still carries the version.
    """

    extra = argument.strip()
    body, used = fill_arguments(
        record.body.strip(), extra, dialect=record.dialect, names=record.arguments
    )
    lines = [
        f'Run the "{record.name}" workflow saved at {record.path}.',
        "",
        "Follow its steps below in order, exactly as written: they are the "
        "specification for this task, not a suggestion. If a step cannot be "
        "carried out, say which one and why instead of silently improvising a "
        "different procedure.",
    ]
    if record.dialect:
        lines.extend(_syntax_notes(body))
    lines.extend(["", body])
    if extra and not used:
        lines.extend(["", f"Additional input for this run: {extra}"])
    return "\n".join(lines)


def _syntax_notes(body: str) -> list[str]:
    """What the other agent would have done before handing the text over."""

    notes: list[str] = []
    if _SHELL_RE.search(body):
        notes.append(
            "Text written as !`command` (or a ```! block) stands for that command's "
            "output: run it first and use what it prints."
        )
    if _FILE_REFERENCE_RE.search(body):
        notes.append("An @path names a file to read before you start.")
    return ["", *notes] if notes else []


def _derive_description(front: dict[str, Any], body: str) -> str:
    """Best available one-liner for the catalog.

    Workflows written for other agents often have no frontmatter. A summary
    sentence beats a title; steps ("1. Build") describe nothing and are skipped.
    """

    described = as_text(front.get("description"))
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
        """Every workflow, sorted by name; the first source to define a name wins."""

        records: dict[str, WorkflowRecord] = {}
        for source in self._sources:
            for record in self._load_source(source):
                records.setdefault(record.name.casefold(), record)
        return [records[key] for key in sorted(records)]

    def find(self, name: str) -> WorkflowRecord | None:
        """Resolve ``/name``, ``name.md`` or ``/prompts:name``.

        A namespaced workflow (``frontend:component``) also answers to its bare
        name while no other workflow claims it.
        """

        wanted = normalize_workflow_name(name)
        if not wanted:
            return None
        records = self.load()
        for record in records:
            if record.name.casefold() == wanted:
                return record
        if ":" in wanted:
            return None
        nested = [record for record in records if record.name.casefold().endswith(":" + wanted)]
        return nested[0] if len(nested) == 1 else None

    def _load_source(self, source: WorkflowSource) -> list[WorkflowRecord]:
        if source.kind == KIND_OPENCODE_JSON:
            return self._load_opencode_commands(source)
        root = source.root
        if not root.is_dir():
            return []
        try:
            entries = sorted(
                root.rglob("*") if source.recursive else root.iterdir(),
                key=lambda item: str(item).casefold(),
            )
        except OSError:
            # An unreadable directory must not break discovery of the others.
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
            front, body = split_frontmatter(text)
            if not body:
                continue
            name = as_text(front.get("name")) or path.stem
            if source.namespaced:
                parents = path.relative_to(root).parts[:-1]
                name = ":".join([*parents, path.stem])
            records.append(
                WorkflowRecord(
                    name=name,
                    description=_derive_description(front, body),
                    body=body,
                    path=path,
                    scope=source.scope,
                    origin=source.origin,
                    dialect=source.dialect,
                    arguments=tuple(as_list(front.get("arguments"))),
                    model_invocable=not is_true(front.get("disable-model-invocation")),
                )
            )
        return records

    def _load_opencode_commands(self, source: WorkflowSource) -> list[WorkflowRecord]:
        commands = jsonc.load_file(source.root).get("command")
        if not isinstance(commands, dict):
            return []
        records: list[WorkflowRecord] = []
        for name, entry in sorted(commands.items()):
            if not isinstance(entry, dict):
                continue
            body = str(entry.get("template") or "").strip()
            if not body:
                continue
            records.append(
                WorkflowRecord(
                    name=str(name),
                    description=str(entry.get("description") or "") or _derive_description(
                        {}, body
                    ),
                    body=body,
                    path=source.root,
                    scope=source.scope,
                    origin=source.origin,
                    dialect=source.dialect or OPENCODE,
                )
            )
        return records

    def render_for_prompt(self) -> str:
        """The available workflows as a compact catalog, or "" if none.

        Only names and one-liners travel in the prompt; the steps load on demand
        via ``use_workflow``. Without the catalog a "/deploy" gets an improvised
        procedure instead of the one the user wrote down.
        """

        records = [record for record in self.load() if record.model_invocable]
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
