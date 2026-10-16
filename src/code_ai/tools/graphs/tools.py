"""The agent's hands on orchestrator graphs: read the catalog, write a graph, edit one.

The document format is the editor's (`*.flow.json`); the spec these tools take
is the short form in core.graph.authoring. Every write goes through the same
compiler the editor's Play button does, and a graph with errors is not written.
"""

from __future__ import annotations

import asyncio
import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from code_ai.core.errors import ToolArgumentError, ToolExecutionError
from code_ai.core.graph import authoring
from code_ai.core.graph.compile import compile_graph
from code_ai.core.graph.models import (
    COMMENT_MOVE_MODES,
    NODE_KINDS,
    VARIABLE_TYPES,
    catalog_payload,
)
from code_ai.core.graph.professions import graph_profile_registry, professions_payload
from code_ai.tools.base import ToolCapability, ToolContext
from code_ai.tools.filesystem.common import announce_write, line_delta, read_text_file
from code_ai.tools.locations import for_context
from code_ai.tools.pdf.common import parse_json_argument
from code_ai.tools.schema import tool_schema
from code_ai.util.fileio import RetryPolicy, atomic_write_bytes

FLOW_SUFFIX = ".flow.json"

# What the model reads before it writes a spec. Kept here, next to the tools
# that take the spec, so the two cannot drift apart unnoticed.
SPEC_GUIDE = f"""\
A graph spec is a JSON object:
  name: the graph's name.
  variables: [{{"name", "type" ({", ".join(VARIABLE_TYPES)}), "default"}}] - run-scoped knobs,
    read and written by var_get / var_set nodes through data.variable.
  nodes: [{{"id", "kind", "data", "note", "pos"}}]
    id: a short name you choose (letters, digits, - and _), used by wires and comments.
    kind: a node kind below. data: its fields; what you leave out takes its default.
    An unwired data input reads data[<pin name>] as a literal.
    note: optional text shown in a bubble above the node. pos: optional [x, y];
    leave it out and the graph is laid out for you.
  wires: ["a.pin -> b.pin", ...]. A side without a pin is the exec pin ("out"
    leaving, "in" arriving), so "start -> agent -> end" chains the flow. The edge
    kind comes from the pins. An exec output drives one wire (use a sequence node
    to fan out); a data input takes one value unless it is a list input (tools).
    Wiring out_3 of a sequence gives it four outputs.
  comments: [{{"title", "nodes": [ids], "color" ("#rrggbb"), "move_mode"
    ({" | ".join(COMMENT_MOVE_MODES)})}}] - a box drawn around those nodes that names that
    part of the graph. It never runs.
Exactly one start node. A run ends at an end node; what reaches end.result is
its answer. Agents (profession, clean_agent) take a model from a model node, or
use the session's when it is not wired."""

EDIT_GUIDE = """\
operations, applied in order, all or nothing:
  {"op": "add_node", "id", "kind", "data", "note", "pos", "wires": [...]}
  {"op": "update_node", "id", "data": {field: value, field: null removes it}, "pos"}
  {"op": "remove_node", "id"} - its wires go with it
  {"op": "connect", "wire": "a.pin -> b.pin"} - replaces a wire the pin holds only one of
  {"op": "disconnect", "wire": "a.pin -> b.pin"}
  {"op": "add_variable", "name", "type", "default"}
  {"op": "update_variable", "name", "new_name", "type", "default"} - nodes follow a rename
  {"op": "remove_variable", "name"}
  {"op": "add_comment", "title", "nodes": [ids], "color", "move_mode"}
  {"op": "set_note", "id", "note": "text" or null, "shown": true}
  {"op": "rename", "name"}
New nodes without pos are placed next to what they are wired to; nothing else
moves. Read the graph first with graph_reference(path=...) for its ids and wires."""


def _graph_path(context: ToolContext, value: Any, *, must_exist: bool) -> tuple[Path, str]:
    raw = str(value or "").strip()
    if not raw:
        raise ToolArgumentError("path is required.")
    if not raw.endswith(FLOW_SUFFIX):
        raise ToolArgumentError(f"A graph file ends in {FLOW_SUFFIX}: {raw}")
    location = for_context(context, None)
    path = location.resolve(raw, must_exist=must_exist)
    return path, location.relative(path)


def _known() -> tuple[str, ...]:
    return tuple(graph_profile_registry().names())


class _GraphWriter:
    """What write_graph and edit_graph share: validation, and the write itself."""

    def __init__(self, tools: Callable[[], tuple[str, ...]] | None = None) -> None:
        # A callable: the tools an agent may be handed change as settings do.
        self._tools = tools or (lambda: ())

    def _check(self, doc: dict[str, Any], context: ToolContext) -> list[str]:
        """The compiler's warnings; its errors stop the write, all of them listed."""

        _, issues = compile_graph(
            doc,
            professions=_known(),
            tools=self._tools(),
            max_loop_iterations=context.config.graph.max_loop_iterations,
        )
        errors = [issue for issue in issues if issue.severity == "error"]
        if errors:
            lines = "\n".join(f"- {line}" for line in authoring.format_issues(errors))
            raise ToolExecutionError(
                f"The graph was not written: {len(errors)} problem(s) the editor would "
                f"refuse to run.\n{lines}"
            )
        return authoring.format_issues(issues)

    async def _write(
        self, context: ToolContext, path: Path, doc: dict[str, Any], before: str | None
    ) -> dict[str, Any]:
        text = authoring.serialize(doc)
        policy = RetryPolicy.from_config(context.config.file_io)
        await announce_write(context, path, before)
        await asyncio.to_thread(
            atomic_write_bytes,
            path,
            text.encode("utf-8"),
            policy=policy,
            allow_non_atomic_fallback=context.config.file_io.allow_non_atomic_fallback,
            in_place_first=context.config.file_io.windows_in_place_first,
            create_parents=True,
        )
        added, removed = line_delta(before or "", text)
        return {"lines_added": added, "lines_removed": removed}


async def _read(path: Path, context: ToolContext) -> str:
    policy = RetryPolicy.from_config(context.config.file_io)
    text, _ = await asyncio.to_thread(read_text_file, path, policy=policy)
    return text


def _counts(doc: dict[str, Any]) -> dict[str, int]:
    comments = sum(1 for node in doc["nodes"] if node["kind"] == "comment")
    return {
        "nodes": len(doc["nodes"]) - comments,
        "wires": len(doc["edges"]),
        "comments": comments,
        "variables": len(doc["variables"]),
    }


class GraphReferenceTool:
    name = "graph_reference"
    description = (
        "How orchestrator graphs (*.flow.json, the Code-AI graph editor's files) are "
        "written: the spec format, the node kinds with their pins, the professions and "
        "the tools an agent node can be given. Pass kinds for their fields and options, "
        "or path to read an existing graph back as a spec."
    )
    capabilities = frozenset({ToolCapability.LOCAL_READ})
    input_schema = tool_schema(
        {
            "kinds": {
                "type": "string",
                "description": (
                    "Comma-separated node kinds to describe in full: every field, its "
                    "default and its options. Leave empty for the overview."
                ),
            },
            "path": {
                "type": "string",
                "description": "A *.flow.json in the workspace to read back as a spec.",
            },
        }
    )

    def __init__(self, tools: Callable[[], list[dict[str, Any]]] | None = None) -> None:
        self._tools = tools or (lambda: [])

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        path_value = str(arguments.get("path") or "").strip()
        if path_value:
            path, relative = _graph_path(context, path_value, must_exist=True)
            doc = authoring.parse_document(await _read(path, context))
            spec = authoring.to_spec(doc)
            return {
                "path": relative,
                "spec": json.dumps(spec, ensure_ascii=False, indent=1),
                **_counts(doc),
            }
        wanted = [item.strip() for item in str(arguments.get("kinds") or "").split(",")]
        wanted = [item for item in wanted if item]
        unknown = [item for item in wanted if item not in NODE_KINDS]
        if unknown:
            raise ToolArgumentError(
                f"Unknown kind(s) {', '.join(unknown)}. Known: {', '.join(sorted(NODE_KINDS))}."
            )
        if wanted:
            return {"reference": "\n\n".join(_kind_detail(kind) for kind in wanted)}
        return {"reference": self._overview()}

    def _overview(self) -> str:
        catalog = catalog_payload(professions_payload())
        lines = [SPEC_GUIDE, "", "Node kinds (pins in -> out; * required):"]
        for row in catalog["node_kinds"]:
            lines.append(f"- {row['kind']} ({row['title']}): {_first_sentence(row['description'])}")
            pins = _pin_line(row["pins"])
            if pins:
                lines.append(f"    {pins}")
        lines.append("")
        lines.append(
            "Pin types: exec carries the flow; data pins connect when the types match, "
            "any takes everything, int feeds float, and int, float and bool feed string."
        )
        lines.append("")
        lines.append("Professions (profession node, data.profession):")
        for row in catalog["professions"]:
            lines.append(f"- {row['name']}: {_first_sentence(row['description'])}")
        tools = self._tools()
        if tools:
            lines.append("")
            lines.append("Tools an agent can be given (tools node, data.tools):")
            groups: dict[str, list[str]] = {}
            for row in tools:
                groups.setdefault(str(row.get("group") or "other"), []).append(str(row["name"]))
            for group, names in groups.items():
                lines.append(f"- {group}: {', '.join(names)}")
        lines.append("")
        lines.append("Call graph_reference(kinds=...) for a kind's fields before filling its data.")
        return "\n".join(lines)


def _first_sentence(text: str) -> str:
    flat = " ".join(str(text).split())
    head = flat.split(". ")[0].rstrip(".")
    return head if len(head) <= 160 else head[:157].rstrip() + "..."


def _pin_line(pins: list[dict[str, Any]]) -> str:
    def side(direction: str) -> str:
        out: list[str] = []
        for pin in pins:
            if pin["direction"] != direction:
                continue
            name = pin["name"]
            if pin.get("dynamic"):
                # Declared once per copy the catalog seeds; written once as a range.
                name = name.rsplit("_", 1)[0] + "_0.."
                if any(entry.startswith(f"{name}:") for entry in out):
                    continue
            mark = "*" if pin.get("required") else ""
            suffix = "[]" if pin.get("multi") else ""
            out.append(f"{name}:{pin['type']}{suffix}{mark}")
        return ", ".join(out) or "-"

    if not pins:
        return ""
    return f"{side('in')} -> {side('out')}"


def _kind_detail(kind: str) -> str:
    spec = NODE_KINDS[kind]
    row = spec.payload()
    lines = [f"{kind} ({spec.title}): {' '.join(spec.description.split())}"]
    pins = _pin_line(row["pins"])
    if pins:
        lines.append(f"pins: {pins}")
    if spec.annotation:
        lines.append("Never runs; prefer the spec's comments list, which sizes the box for you.")
    for field in row["fields"]:
        parts = [f"  {field['name']} ({field['kind']})"]
        if field["default"] not in (None, ""):
            parts.append(f"default {json.dumps(field['default'])}")
        if field["options"]:
            parts.append("one of " + ", ".join(repr(option) for option in field["options"]))
        if field["help"]:
            parts.append(" ".join(str(field["help"]).split()))
        lines.append(" - ".join(parts))
    if any(pin.dynamic for pin in spec.pins):
        lines.append("  outputs / inputs (int): how many numbered pins; wires set it for you.")
    lines.append("  note (text), note_shown (bool): the bubble above the node.")
    return "\n".join(lines)


class WriteGraphTool(_GraphWriter):
    name = "write_graph"
    description = (
        "Create an orchestrator graph (*.flow.json) the Code-AI graph editor opens and "
        "runs, from a short spec: nodes by id, wires as 'a.pin -> b.pin', comments around "
        "groups of nodes. Laid out automatically, and checked like the editor's Run "
        "button: a graph with errors is not written. Read graph_reference first."
    )
    capabilities = frozenset({ToolCapability.LOCAL_WRITE})
    input_schema = tool_schema(
        {
            "path": {
                "type": "string",
                "description": f"Where to write it in the workspace; ends in {FLOW_SUFFIX}.",
            },
            "reason": {
                "type": "string",
                "description": "One or two sentences on what the graph is for. Shown to the user.",
            },
            "graph": {
                "type": "string",
                "description": "The graph spec as a JSON string.\n" + SPEC_GUIDE,
            },
            "replace": {
                "type": "boolean",
                "description": (
                    "Overwrite a graph already at path. Prefer edit_graph to change one: "
                    "it keeps the user's layout."
                ),
            },
        },
        required=("path", "graph"),
    )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        path, relative = _graph_path(context, arguments.get("path"), must_exist=False)
        spec = parse_json_argument(arguments.get("graph"), "graph")
        before: str | None = None
        if path.exists():
            if not arguments.get("replace"):
                raise ToolExecutionError(
                    f"{relative} already exists. Change it with edit_graph, or pass "
                    "replace=true to overwrite it."
                )
            before = await _read(path, context)
        try:
            doc = authoring.build(spec)
        except authoring.GraphSpecError as exc:
            raise ToolExecutionError(_spec_problems(exc)) from None
        if not doc.get("name"):
            doc["name"] = path.name[: -len(FLOW_SUFFIX)]
        warnings = self._check(doc, context)
        written = await self._write(context, path, doc, before)
        return {
            "path": relative,
            **_counts(doc),
            "warnings": warnings,
            **written,
            "next": "The user opens it in the graph editor to see and run it.",
        }


class EditGraphTool(_GraphWriter):
    name = "edit_graph"
    description = (
        "Change an orchestrator graph (*.flow.json) with a list of operations: add, "
        "update or remove nodes, connect and disconnect wires, variables, comments and "
        "notes. All or nothing, checked like the editor's Run button, and the user's "
        "layout is kept."
    )
    capabilities = frozenset({ToolCapability.LOCAL_WRITE})
    input_schema = tool_schema(
        {
            "path": {
                "type": "string",
                "description": f"The graph to change; ends in {FLOW_SUFFIX}.",
            },
            "reason": {
                "type": "string",
                "description": "One or two sentences on what the change does. Shown to the user.",
            },
            "operations": {
                "type": "string",
                "description": "A JSON array of operations.\n" + EDIT_GUIDE,
            },
        },
        required=("path", "operations"),
    )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        path, relative = _graph_path(context, arguments.get("path"), must_exist=True)
        operations = parse_json_argument(arguments.get("operations"), "operations")
        before = await _read(path, context)
        try:
            doc = authoring.parse_document(before)
            edited = copy.deepcopy(doc)
            done = authoring.apply_edits(edited, operations)
        except authoring.GraphSpecError as exc:
            raise ToolExecutionError(_spec_problems(exc)) from None
        warnings = self._check(edited, context)
        written = await self._write(context, path, edited, before)
        return {"path": relative, "done": done, **_counts(edited), "warnings": warnings, **written}


def _spec_problems(exc: authoring.GraphSpecError) -> str:
    lines = "\n".join(f"- {problem}" for problem in exc.problems)
    return f"The graph was not written:\n{lines}"
