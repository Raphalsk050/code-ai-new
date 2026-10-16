"""Turn a `*.flow.json` document into a runnable program, or say exactly why not.

Validation is the editor's feedback loop, so every problem is reported as a
:class:`ValidationIssue` carrying the node and pin it belongs to - the editor
paints a marker on that node and lists the message. Errors block Play; warnings
do not (an orphan node is a work in progress, not a mistake).

The one rule worth stating out loud: this is the *only* authority on whether a
graph is legal. The editor has a mirror of the schema for ergonomics, never as a
second opinion.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from code_ai.core.graph.functions import is_function
from code_ai.core.graph.models import (
    COMMENT_FONT_SIZES,
    COMMENT_MAX_SIZE,
    COMMENT_MIN_SIZE,
    COMMENT_MOVE_MODES,
    MAX_DYNAMIC_PINS,
    MAX_EDGES,
    MAX_NODES,
    MAX_VARIABLES,
    NODE_KINDS,
    NOTE_FIELDS,
    PERMISSION_FIELDS,
    SCHEMA_VERSION,
    EdgeSpec,
    GraphDoc,
    NodeKind,
    NodeSpec,
    PinType,
    can_connect,
    expanded_pins,
)
from code_ai.core.graph.program import GraphProgram, NodeProgram

# An api_key_ref the runtime knows how to resolve. Anything else that looks like
# a secret is refused: this file is committed, and a leaked key is not a problem
# a later validation pass can fix.
_KEY_REF_RE = re.compile(r"^(session|env:[A-Za-z_][A-Za-z0-9_]*|secret:[\w.-]+)$")
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    message: str
    node_id: str = ""
    pin: str = ""
    field: str = ""
    severity: str = SEVERITY_ERROR

    def payload(self) -> dict[str, Any]:
        return {
            "message": self.message,
            "node_id": self.node_id,
            "pin": self.pin,
            "field": self.field,
            "severity": self.severity,
        }


class _Collector:
    def __init__(self) -> None:
        self.issues: list[ValidationIssue] = []

    def error(self, message: str, **where: str) -> None:
        self.issues.append(ValidationIssue(message=message, severity=SEVERITY_ERROR, **where))

    def warn(self, message: str, **where: str) -> None:
        self.issues.append(ValidationIssue(message=message, severity=SEVERITY_WARNING, **where))

    @property
    def failed(self) -> bool:
        return any(item.severity == SEVERITY_ERROR for item in self.issues)


def compile_graph(
    raw: dict[str, Any],
    *,
    professions: tuple[str, ...] = (),
    tools: tuple[str, ...] = (),
    max_loop_iterations: int = 50,
) -> tuple[GraphProgram | None, list[ValidationIssue]]:
    """Validate ``raw`` and, if it holds up, return the program to run.

    ``tools`` names what this build can hand an agent; empty skips the check.
    """

    collector = _Collector()
    doc = _parse(raw, collector)
    if doc is None:
        return None, collector.issues

    _check_limits(doc, collector)
    nodes = _index_nodes(doc, collector)
    variables = _index_variables(doc, collector)
    edges = _check_edges(doc, nodes, collector)
    start_id = _find_start(doc, collector)

    pin_types = _resolve_pin_types(nodes, variables)
    wired = {(edge.target[0], edge.target[1]) for edge in edges if edge.kind == "data"}
    _check_edge_types(edges, nodes, pin_types, collector)
    _check_node_data(
        nodes, variables, professions, tools, max_loop_iterations, wired, collector
    )
    _check_required_inputs(nodes, wired, collector)
    _check_data_cycles(edges, collector)

    if collector.failed:
        return None, collector.issues

    program = _build_program(doc, nodes, variables, edges, start_id, pin_types)
    _warn_unreachable(program, collector)
    return program, collector.issues


# -- parsing --------------------------------------------------------------


def _parse(raw: Any, collector: _Collector) -> GraphDoc | None:
    if not isinstance(raw, dict):
        collector.error("A graph must be a JSON object.")
        return None
    version = raw.get("version", SCHEMA_VERSION)
    if not isinstance(version, int) or version > SCHEMA_VERSION:
        collector.error(
            f"This graph was written for schema version {version}; this build reads "
            f"up to {SCHEMA_VERSION}. Update Code-AI."
        )
        return None
    try:
        return GraphDoc.model_validate(raw)
    except ValidationError as exc:
        for error in exc.errors()[:20]:
            location = ".".join(str(part) for part in error.get("loc", ()))
            collector.error(f"{location or 'graph'}: {error.get('msg', 'invalid value')}")
        return None


def _check_limits(doc: GraphDoc, collector: _Collector) -> None:
    if len(doc.nodes) > MAX_NODES:
        collector.error(f"A graph may hold at most {MAX_NODES} nodes ({len(doc.nodes)} found).")
    if len(doc.edges) > MAX_EDGES:
        collector.error(f"A graph may hold at most {MAX_EDGES} edges ({len(doc.edges)} found).")
    if len(doc.variables) > MAX_VARIABLES:
        collector.error(f"A graph may declare at most {MAX_VARIABLES} variables.")


def _index_nodes(doc: GraphDoc, collector: _Collector) -> dict[str, NodeSpec]:
    nodes: dict[str, NodeSpec] = {}
    counts: dict[str, int] = {}
    for node in doc.nodes:
        if not node.id:
            collector.error("A node is missing its id.")
            continue
        if node.id in nodes:
            collector.error(f"Duplicate node id {node.id!r}.", node_id=node.id)
            continue
        spec = NODE_KINDS.get(node.kind)
        if spec is None:
            collector.error(
                f"Unknown node kind {node.kind!r}. Known kinds: "
                f"{', '.join(sorted(NODE_KINDS))}.",
                node_id=node.id,
            )
            continue
        counts[node.kind] = counts.get(node.kind, 0) + 1
        if spec.max_instances and counts[node.kind] > spec.max_instances:
            collector.error(
                f"A graph may hold only {spec.max_instances} {spec.title} node(s).",
                node_id=node.id,
            )
        nodes[node.id] = node
    return nodes


def _index_variables(doc: GraphDoc, collector: _Collector) -> dict[str, Any]:
    variables: dict[str, Any] = {}
    for variable in doc.variables:
        name = variable.name.strip()
        if not name:
            collector.error("A variable is missing its name.")
            continue
        if name in variables:
            collector.error(f"Duplicate variable name {name!r}.")
            continue
        variables[name] = variable
    return variables


def _find_start(doc: GraphDoc, collector: _Collector) -> str:
    starts = [node.id for node in doc.nodes if node.kind == NodeKind.START]
    if not starts:
        collector.error("The graph has no Start node, so there is nothing to run.")
        return ""
    return starts[0]


# -- edges ----------------------------------------------------------------


def _check_edges(
    doc: GraphDoc, nodes: dict[str, NodeSpec], collector: _Collector
) -> list[EdgeSpec]:
    kept: list[EdgeSpec] = []
    seen_ids: set[str] = set()
    exec_sources: dict[tuple[str, str], str] = {}
    data_targets: dict[tuple[str, str], str] = {}
    data_pairs: set[tuple[str, str, str, str]] = set()

    for edge in doc.edges:
        if edge.id in seen_ids:
            collector.error(f"Duplicate edge id {edge.id!r}.")
            continue
        seen_ids.add(edge.id)

        source_node, source_pin = edge.source
        target_node, target_pin = edge.target
        if source_node not in nodes:
            collector.error(f"Edge {edge.id} starts at an unknown node {source_node!r}.")
            continue
        if target_node not in nodes:
            collector.error(f"Edge {edge.id} ends at an unknown node {target_node!r}.")
            continue

        source_spec = NODE_KINDS[nodes[source_node].kind]
        target_spec = NODE_KINDS[nodes[target_node].kind]
        out_pin = source_spec.pin(source_pin, "out")
        in_pin = target_spec.pin(target_pin, "in")
        if out_pin is None:
            collector.error(
                f"{source_spec.title} has no output pin {source_pin!r}.",
                node_id=source_node,
                pin=source_pin,
            )
            continue
        if in_pin is None:
            collector.error(
                f"{target_spec.title} has no input pin {target_pin!r}.",
                node_id=target_node,
                pin=target_pin,
            )
            continue

        missing = _missing_copy(nodes[source_node], out_pin, source_pin, "out") or _missing_copy(
            nodes[target_node], in_pin, target_pin, "in"
        )
        if missing:
            node_id, pin_name = missing
            collector.error(
                f"Pin {pin_name!r} is not on this node any more; remove the wire.",
                node_id=node_id,
                pin=pin_name,
            )
            continue

        is_exec = out_pin.type == PinType.EXEC
        if (in_pin.type == PinType.EXEC) != is_exec:
            collector.error(
                "An exec pin cannot be wired to a data pin.",
                node_id=source_node,
                pin=source_pin,
            )
            continue
        declared = PinType.EXEC if is_exec else "data"
        if edge.kind != declared:
            collector.error(
                f"Edge {edge.id} says {edge.kind!r} but connects {declared} pins.",
                node_id=source_node,
                pin=source_pin,
            )
            continue

        if is_exec:
            key = (source_node, source_pin)
            if key in exec_sources:
                collector.error(
                    "An exec output drives one wire. Use a Sequence node to fan out.",
                    node_id=source_node,
                    pin=source_pin,
                )
                continue
            exec_sources[key] = edge.id
        else:
            key = (target_node, target_pin)
            if key in data_targets and not in_pin.multi:
                collector.error(
                    "A data input takes one value.",
                    node_id=target_node,
                    pin=target_pin,
                )
                continue
            pair = (source_node, source_pin, target_node, target_pin)
            if pair in data_pairs:
                collector.error(
                    "That connection is drawn twice.", node_id=target_node, pin=target_pin
                )
                continue
            data_pairs.add(pair)
            data_targets[key] = edge.id
            if source_node == target_node:
                collector.error(
                    "A node cannot feed its own input.", node_id=source_node, pin=source_pin
                )
                continue

        kept.append(edge)
    return kept


def _missing_copy(
    node: NodeSpec, declared: Any, pin_name: str, direction: str
) -> tuple[str, str] | None:
    """A numbered copy of a dynamic pin beyond the count the node asked for."""

    if not declared.dynamic:
        return None
    names = {pin.name for pin in expanded_pins(NODE_KINDS[node.kind], node.data)}
    return None if pin_name in names else (node.id, pin_name)


def _resolve_pin_types(
    nodes: dict[str, NodeSpec], variables: dict[str, Any]
) -> dict[tuple[str, str], str]:
    """Pin types after per-node refinement.

    ``var_get`` is declared ``any`` in the catalog because its real type is the
    variable the user picked, and an int Math node is float in the catalog.
    """

    resolved: dict[tuple[str, str], str] = {}
    for node_id, node in nodes.items():
        spec = NODE_KINDS[node.kind]
        for pin in expanded_pins(spec, node.data):
            resolved[(node_id, pin.name)] = str(pin.type)
        if node.kind == NodeKind.MATH and str(node.data.get("type") or "float") == "int":
            for pin_name in ("a", "b", "result"):
                resolved[(node_id, pin_name)] = PinType.INT
        if node.kind in (NodeKind.VAR_GET, NodeKind.VAR_SET):
            declared = variables.get(str(node.data.get("variable") or "").strip())
            if declared is not None:
                pin_name = "value"
                resolved[(node_id, pin_name)] = str(declared.type)
    return resolved


def _check_edge_types(
    edges: list[EdgeSpec],
    nodes: dict[str, NodeSpec],
    pin_types: dict[tuple[str, str], str],
    collector: _Collector,
) -> None:
    for edge in edges:
        if edge.kind != "data":
            continue
        source_node, source_pin = edge.source
        target_node, target_pin = edge.target
        source_type = pin_types.get((source_node, source_pin), PinType.ANY)
        target_type = pin_types.get((target_node, target_pin), PinType.ANY)
        if not can_connect(source_type, target_type):
            collector.error(
                f"A {source_type} value cannot feed a {target_type} pin.",
                node_id=target_node,
                pin=target_pin,
            )


def _check_required_inputs(
    nodes: dict[str, NodeSpec], wired: set[tuple[str, str]], collector: _Collector
) -> None:
    for node_id, node in nodes.items():
        spec = NODE_KINDS[node.kind]
        for pin in expanded_pins(spec, node.data):
            if pin.direction != "in" or not pin.required:
                continue
            if (node_id, pin.name) in wired:
                continue
            literal = node.data.get(pin.name)
            if literal is None or (isinstance(literal, str) and not literal.strip()):
                collector.error(
                    f"{spec.title} needs {pin.label or pin.name}: wire it or fill it in.",
                    node_id=node_id,
                    pin=pin.name,
                )


def _check_data_cycles(edges: list[EdgeSpec], collector: _Collector) -> None:
    """A data pull that depends on itself would recurse forever at run time."""

    graph: dict[str, set[str]] = {}
    for edge in edges:
        if edge.kind != "data":
            continue
        graph.setdefault(edge.target[0], set()).add(edge.source[0])

    visiting: set[str] = set()
    done: set[str] = set()

    def walk(node_id: str, trail: list[str]) -> bool:
        if node_id in visiting:
            cycle = " -> ".join([*trail, node_id])
            collector.error(f"The data wires form a cycle: {cycle}.", node_id=node_id)
            return True
        if node_id in done:
            return False
        visiting.add(node_id)
        for parent in sorted(graph.get(node_id, ())):
            if walk(parent, [*trail, node_id]):
                visiting.discard(node_id)
                return True
        visiting.discard(node_id)
        done.add(node_id)
        return False

    for node_id in sorted(graph):
        if walk(node_id, []):
            return


# -- per-kind data --------------------------------------------------------


def _check_node_data(
    nodes: dict[str, NodeSpec],
    variables: dict[str, Any],
    professions: tuple[str, ...],
    tools: tuple[str, ...],
    max_loop_iterations: int,
    wired: set[tuple[str, str]],
    collector: _Collector,
) -> None:
    for node_id, node in nodes.items():
        spec = NODE_KINDS[node.kind]
        known = {item.name for item in spec.fields} | {
            pin.name for pin in expanded_pins(spec, node.data) if pin.direction == "in"
        }
        # A node with "+ Add pin" stores its counts; every other unknown key is
        # a typo the user wants to hear about now rather than mid-run.
        for pin in spec.pins:
            if pin.dynamic:
                known.add("outputs" if pin.direction == "out" else "inputs")
        known.update(NOTE_FIELDS)
        _check_note(node_id, node, collector)
        for key in node.data:
            if key not in known:
                collector.warn(
                    f"{spec.title} has no field {key!r}; it will be ignored.",
                    node_id=node_id,
                    field=key,
                )

        match node.kind:
            case NodeKind.MODEL:
                _check_model_node(node_id, node, collector)
            case NodeKind.PROFESSION:
                _check_profession_node(node_id, node, professions, tools, wired, collector)
            case NodeKind.CUSTOM_PROFESSION:
                _check_custom_profession_node(node_id, node, tools, collector)
            case NodeKind.TOOLS:
                _check_tools_node(node_id, node, tools, collector)
            case NodeKind.CLEAN_AGENT:
                _check_budgets(node_id, node, collector)
            case NodeKind.DECISION:
                _check_decision_node(node_id, node, wired, collector)
            case NodeKind.LOOP:
                _check_loop_node(node_id, node, max_loop_iterations, collector)
            case NodeKind.VAR_GET | NodeKind.VAR_SET:
                _check_variable_node(node_id, node, variables, collector)
            case NodeKind.SEQUENCE:
                _check_sequence_node(node_id, node, collector)
            case NodeKind.COMMENT:
                _check_comment_node(node_id, node, collector)
        if node.kind in (NodeKind.APPEND, NodeKind.FORMAT):
            _check_pin_count(node_id, node, "inputs", collector)
        if is_function(node.kind):
            _check_choices(node_id, node, spec, collector)


def _check_note(node_id: str, node: NodeSpec, collector: _Collector) -> None:
    """A node's bubble. Only ever a warning: a note cannot break a run."""

    note = node.data.get("note")
    if note is not None and not isinstance(note, str):
        collector.warn("A node's note must be text.", node_id=node_id, field="note")
    shown = node.data.get("note_shown")
    if shown is not None and not isinstance(shown, bool):
        collector.warn("note_shown must be true or false.", node_id=node_id, field="note_shown")


def _check_comment_node(node_id: str, node: NodeSpec, collector: _Collector) -> None:
    """A comment never runs, so nothing about it is worth refusing Play over."""

    data = node.data
    for key in ("width", "height"):
        value = data.get(key)
        if value is None:
            continue
        if not _is_number(value) or not COMMENT_MIN_SIZE <= value <= COMMENT_MAX_SIZE:
            collector.warn(
                f"A comment's {key} is a number from {COMMENT_MIN_SIZE} to {COMMENT_MAX_SIZE}.",
                node_id=node_id,
                field=key,
            )
    size = data.get("font_size")
    low, high = COMMENT_FONT_SIZES
    if size is not None and (not _is_number(size) or not low <= size <= high):
        collector.warn(
            f"A comment's font size is from {low} to {high}.", node_id=node_id, field="font_size"
        )
    color = data.get("color")
    if color is not None and not (isinstance(color, str) and _COLOR_RE.match(color)):
        collector.warn("A comment's color is written #rrggbb.", node_id=node_id, field="color")
    mode = data.get("move_mode")
    if mode is not None and mode not in COMMENT_MOVE_MODES:
        collector.warn(
            f"A comment moves as one of: {', '.join(COMMENT_MOVE_MODES)}.",
            node_id=node_id,
            field="move_mode",
        )


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _check_model_node(node_id: str, node: NodeSpec, collector: _Collector) -> None:
    key_ref = str(node.data.get("api_key_ref") or "session").strip()
    if not _KEY_REF_RE.match(key_ref):
        collector.error(
            "An API key must not be stored in a graph file - it goes into git. Use "
            "'session', 'env:NAME', or 'secret:NAME'.",
            node_id=node_id,
            field="api_key_ref",
        )
    base_url = str(node.data.get("base_url") or "").strip()
    if base_url and not base_url.startswith(("http://", "https://")):
        collector.error(
            "Base URL must start with http:// or https://.", node_id=node_id, field="base_url"
        )
    effort = str(node.data.get("reasoning_effort") or "").strip()
    if effort and effort not in ("none", "minimal", "low", "medium", "high", "xhigh"):
        collector.error(
            f"Unknown reasoning effort {effort!r}.", node_id=node_id, field="reasoning_effort"
        )
    api_mode = str(node.data.get("api_mode") or "").strip()
    if api_mode and api_mode not in ("responses", "completions", "ollama"):
        collector.error(
            f"Unknown API mode {api_mode!r}.", node_id=node_id, field="api_mode"
        )
    for name, low, high in (("temperature", 0.0, 2.0), ("top_p", 0.0, 1.0)):
        value = node.data.get(name)
        if value is None or value == "":
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            collector.error(f"{name} must be a number.", node_id=node_id, field=name)
            continue
        if not low <= number <= high:
            collector.error(
                f"{name} must be between {low} and {high}.", node_id=node_id, field=name
            )
    if not str(node.data.get("model") or "").strip():
        collector.warn(
            "No model chosen; agents wired to this node fall back to the session model.",
            node_id=node_id,
            field="model",
        )


def _check_profession_node(
    node_id: str,
    node: NodeSpec,
    professions: tuple[str, ...],
    tools: tuple[str, ...],
    wired: set[tuple[str, str]],
    collector: _Collector,
) -> None:
    profession = str(node.data.get("profession") or "").strip()
    # A wired Profession node answers the dropdown, and an empty one hires a
    # custom agent, so only a name nobody knows is wrong.
    if (node_id, "profession") not in wired and profession:
        if professions and profession not in professions:
            collector.error(
                f"Unknown profession {profession!r}. Available: {', '.join(professions)}.",
                node_id=node_id,
                field="profession",
            )
    _check_permissions(node_id, node, collector)
    _check_tool_list(node_id, node, "blocked_tools", tools, collector)
    _check_budgets(node_id, node, collector)


def _check_custom_profession_node(
    node_id: str, node: NodeSpec, tools: tuple[str, ...], collector: _Collector
) -> None:
    if not str(node.data.get("role") or "").strip():
        collector.error(
            "Write what this profession is and how it works; the role text is the "
            "agent's whole brief.",
            node_id=node_id,
            field="role",
        )
    if not str(node.data.get("name") or "").strip():
        collector.warn(
            "Unnamed profession; the run log will call it 'custom'.",
            node_id=node_id,
            field="name",
        )
    _check_permissions(node_id, node, collector)
    if not any(
        _as_flag(node.data.get(name), default=(name == "can_read")) for name in PERMISSION_FIELDS
    ):
        collector.warn(
            "Every switch is off, so its agents can only talk - fine for a "
            "conversation, not for work on the files.",
            node_id=node_id,
            field="can_read",
        )
    _check_tool_list(node_id, node, "blocked_tools", tools, collector)
    _check_budgets(node_id, node, collector)


def _check_tools_node(
    node_id: str, node: NodeSpec, tools: tuple[str, ...], collector: _Collector
) -> None:
    picked = _check_tool_list(node_id, node, "tools", tools, collector)
    if picked is not None and not picked:
        collector.warn(
            "No tools picked: an agent wired to this gets none from it.",
            node_id=node_id,
            field="tools",
        )


def _check_permissions(node_id: str, node: NodeSpec, collector: _Collector) -> None:
    for name in PERMISSION_FIELDS:
        value = node.data.get(name)
        if value is None or isinstance(value, bool):
            continue
        if isinstance(value, str) and value.strip().lower() in ("true", "false", ""):
            continue
        collector.error(f"{name} must be on or off.", node_id=node_id, field=name)


def _check_tool_list(
    node_id: str,
    node: NodeSpec,
    field: str,
    tools: tuple[str, ...],
    collector: _Collector,
) -> list[str] | None:
    """The tool names in ``field``, or ``None`` when it is malformed."""

    value = node.data.get(field)
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        collector.error("This must be a list of tool names.", node_id=node_id, field=field)
        return None
    names = [item.strip() for item in value if item.strip()]
    unknown = [name for name in names if tools and name not in tools]
    if unknown:
        collector.warn(
            f"Not a tool this build can hand an agent, so it is ignored: {', '.join(unknown)}.",
            node_id=node_id,
            field=field,
        )
    return names


def _check_budgets(node_id: str, node: NodeSpec, collector: _Collector) -> None:
    for name in ("max_steps", "timeout_s"):
        value = node.data.get(name)
        if value in (None, ""):
            continue
        try:
            number = int(value)
        except (TypeError, ValueError):
            collector.error(f"{name} must be a whole number.", node_id=node_id, field=name)
            continue
        if number < 0:
            collector.error(f"{name} cannot be negative.", node_id=node_id, field=name)


def _as_flag(value: object, *, default: bool) -> bool:
    """A checkbox as stored by the editor, tolerating the string forms."""

    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "1")
    return bool(value)


def _check_decision_node(
    node_id: str, node: NodeSpec, wired: set[tuple[str, str]], collector: _Collector
) -> None:
    mode = str(node.data.get("mode") or "bool").strip()
    if mode not in ("bool", "judge"):
        collector.error(
            f"Decision mode must be 'bool' or 'judge', not {mode!r}.",
            node_id=node_id,
            field="mode",
        )
        return
    if mode == "judge":
        has_prompt = bool(str(node.data.get("prompt") or "").strip())
        if not has_prompt and (node_id, "prompt") not in wired:
            collector.error(
                "A judge decision needs a question - fill it in or wire `prompt`.",
                node_id=node_id,
                field="prompt",
            )
    elif (node_id, "condition") not in wired and "condition" not in node.data:
        collector.warn(
            "Nothing feeds `condition`, so this decision always takes `false`.",
            node_id=node_id,
            pin="condition",
        )


def _check_loop_node(
    node_id: str, node: NodeSpec, max_loop_iterations: int, collector: _Collector
) -> None:
    kind = str(node.data.get("kind") or "count").strip()
    if kind not in ("count", "while", "foreach"):
        collector.error(
            f"Loop kind must be 'count', 'while' or 'foreach', not {kind!r}.",
            node_id=node_id,
            field="kind",
        )
        return
    if kind == "count":
        try:
            count = int(node.data.get("count") or 0)
        except (TypeError, ValueError):
            collector.error("Count must be a whole number.", node_id=node_id, field="count")
            return
        if count <= 0:
            collector.warn(
                "A count of zero never enters the body.", node_id=node_id, field="count"
            )
    cap = node.data.get("max_iterations")
    if cap in (None, "", 0, "0"):
        return
    try:
        number = int(cap)
    except (TypeError, ValueError):
        collector.error(
            "Max iterations must be a whole number.", node_id=node_id, field="max_iterations"
        )
        return
    if number > max_loop_iterations:
        collector.error(
            f"Max iterations is capped at {max_loop_iterations} by the configuration.",
            node_id=node_id,
            field="max_iterations",
        )


def _check_variable_node(
    node_id: str, node: NodeSpec, variables: dict[str, Any], collector: _Collector
) -> None:
    name = str(node.data.get("variable") or "").strip()
    if not name:
        collector.error("Choose a variable.", node_id=node_id, field="variable")
        return
    if name not in variables:
        collector.error(
            f"No variable named {name!r} is declared on this graph.",
            node_id=node_id,
            field="variable",
        )


def _check_sequence_node(node_id: str, node: NodeSpec, collector: _Collector) -> None:
    _check_pin_count(node_id, node, "outputs", collector)


def _check_pin_count(node_id: str, node: NodeSpec, key: str, collector: _Collector) -> None:
    title = NODE_KINDS[node.kind].title
    try:
        count = int(node.data.get(key, 2))
    except (TypeError, ValueError):
        collector.error(f"{title} {key} must be a whole number.", node_id=node_id)
        return
    if not 1 <= count <= MAX_DYNAMIC_PINS:
        collector.error(
            f"A {title} holds between 1 and {MAX_DYNAMIC_PINS} {key}.", node_id=node_id
        )


def _check_choices(node_id: str, node: NodeSpec, spec: Any, collector: _Collector) -> None:
    """A dropdown's value must be one of its options, or the node would guess."""

    for field in spec.fields:
        if field.kind != "select" or not field.options:
            continue
        value = node.data.get(field.name)
        if value in (None, ""):
            continue
        if str(value) not in field.options:
            collector.error(
                f"{field.label} must be one of {', '.join(field.options)}, not {value!r}.",
                node_id=node_id,
                field=field.name,
            )


# -- assembly -------------------------------------------------------------


def _build_program(
    doc: GraphDoc,
    nodes: dict[str, NodeSpec],
    variables: dict[str, Any],
    edges: list[EdgeSpec],
    start_id: str,
    pin_types: dict[tuple[str, str], str],
) -> GraphProgram:
    exec_out: dict[str, dict[str, tuple[str, str]]] = {node_id: {} for node_id in nodes}
    data_in: dict[str, dict[str, tuple[str, str, str]]] = {node_id: {} for node_id in nodes}
    multi_in: dict[str, dict[str, list[tuple[str, str, str]]]] = {node_id: {} for node_id in nodes}
    for edge in edges:
        source_node, source_pin = edge.source
        target_node, target_pin = edge.target
        if edge.kind == "exec":
            exec_out[source_node][source_pin] = (target_node, edge.id)
            continue
        wire = (source_node, source_pin, edge.id)
        in_pin = NODE_KINDS[nodes[target_node].kind].pin(target_pin, "in")
        if in_pin is not None and in_pin.multi:
            multi_in[target_node].setdefault(target_pin, []).append(wire)
        else:
            data_in[target_node][target_pin] = wire

    built: dict[str, NodeProgram] = {}
    for node_id, node in nodes.items():
        spec = NODE_KINDS[node.kind]
        if spec.annotation:
            continue
        ordered = tuple(
            pin.name
            for pin in expanded_pins(spec, node.data)
            if pin.direction == "out" and pin.type == PinType.EXEC
        )
        built[node_id] = NodeProgram(
            id=node_id,
            kind=str(node.kind),
            spec=spec,
            data=dict(node.data),
            label=_label_for(node, spec.title),
            exec_out=exec_out[node_id],
            data_in=data_in[node_id],
            multi_in={pin: tuple(wires) for pin, wires in multi_in[node_id].items()},
            ordered_exec=ordered,
        )

    return GraphProgram(
        name=doc.name or "flow",
        start_id=start_id,
        nodes=built,
        variables=dict(variables),
        pin_types=pin_types,
    )


def _label_for(node: NodeSpec, fallback: str) -> str:
    """A short human label for logs and node headers."""

    for key in ("label", "title", "name", "profession", "variable", "model", "kind"):
        value = node.data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:60]
    return fallback


def _warn_unreachable(program: GraphProgram, collector: _Collector) -> None:
    reachable: set[str] = set()
    stack = [program.start_id]
    while stack:
        node_id = stack.pop()
        if node_id in reachable or node_id not in program.nodes:
            continue
        reachable.add(node_id)
        stack.extend(target for target, _ in program.nodes[node_id].exec_out.values())

    # A pure node is pulled rather than walked into, so "unreachable" means
    # something different for it: nobody reads it.
    consumed: set[str] = set()
    for node in program.nodes.values():
        for source, _, _ in node.sources():
            consumed.add(source)

    for node_id, node in program.nodes.items():
        if node.spec.pure:
            if node_id not in consumed:
                collector.warn("Nothing reads this node.", node_id=node_id)
        elif node_id not in reachable:
            collector.warn("Nothing leads here, so this node never runs.", node_id=node_id)
