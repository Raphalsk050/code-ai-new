"""Writing a `*.flow.json` from a compact spec: what the agent's graph tools stand on.

A model writing the document by hand has to invent node ids, edge ids, edge
kinds, dynamic pin counts and coordinates, and gets one of them wrong. The spec
it writes instead names nodes by short ids of its choosing and wires them as
text - ``"start -> agent -> end"``, ``"agent.report -> end.result"`` - and
everything else is derived here: edge kinds from the pins, dynamic pin counts
from the wires, positions from the flow, comment boxes from the nodes they hold.

The compiler stays the only judge of legality. What this module refuses is
only what it cannot turn into a document at all: an unknown kind, a pin the
node does not have, a wire to a node that is not there.
"""

from __future__ import annotations

import copy
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from code_ai.core.graph.compile import ValidationIssue
from code_ai.core.graph.models import (
    COMMENT_COLOR,
    COMMENT_MAX_SIZE,
    COMMENT_MIN_SIZE,
    COMMENT_MOVE_MODES,
    MAX_DYNAMIC_PINS,
    NODE_KINDS,
    SCHEMA_VERSION,
    VARIABLE_TYPES,
    NodeKind,
    NodeKindSpec,
    PinType,
    expanded_pins,
)

# Geometry, as the editor draws it (webview/flow/model.ts).
NODE_W = 216
_HEADER_H = 40
_ROW_H = 20
_BODY_PAD = 8
_FOOTER_H = 24
_FOOTER_KINDS = frozenset({"profession", "clean_agent", "custom_profession", "tools"})
# A column every 320 px: the node's width and a gap, on the editor's grid.
_COL_GAP = 104
_ROW_GAP = 60
_GRID = 10
_COMMENT_PAD = 30
_COMMENT_HEADER = 38

_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,47}$")
_ENDPOINT_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_-]*)(?:\.([A-Za-z0-9_]+))?$")
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


class GraphSpecError(ValueError):
    """The spec cannot be turned into a document; ``problems`` says each reason."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass(frozen=True, slots=True)
class Wire:
    source: str
    out_pin: str
    target: str
    in_pin: str

    def text(self) -> str:
        return f"{self.source}.{self.out_pin} -> {self.target}.{self.in_pin}"


# -- the document -------------------------------------------------------------


def empty_document(name: str = "") -> dict[str, Any]:
    return {
        "version": SCHEMA_VERSION,
        "name": name,
        "variables": [],
        "nodes": [],
        "edges": [],
        "view": {"pan": [60, 60], "zoom": 0.8},
    }


def serialize(doc: dict[str, Any]) -> str:
    """The document as the editor writes it, so a save there changes nothing."""

    def number(value: Any) -> int | float:
        rounded = round(float(value), 3)
        return int(rounded) if rounded == int(rounded) else rounded

    ordered = {
        "version": doc.get("version") or SCHEMA_VERSION,
        "name": doc.get("name") or "",
        "variables": [
            {
                "id": item["id"],
                "name": item["name"],
                "type": item.get("type", "string"),
                "default": item.get("default"),
            }
            for item in doc.get("variables", [])
        ],
        "nodes": [
            {
                "id": node["id"],
                "kind": node["kind"],
                "pos": [round(node["pos"][0]), round(node["pos"][1])],
                "data": {key: node["data"][key] for key in sorted(node.get("data") or {})},
            }
            for node in doc.get("nodes", [])
        ],
        "edges": [
            {
                "id": edge["id"],
                "kind": edge["kind"],
                "from": list(edge["from"]),
                "to": list(edge["to"]),
            }
            for edge in doc.get("edges", [])
        ],
        "view": {
            "pan": [number(value) for value in doc.get("view", {}).get("pan", [0, 0])],
            "zoom": number(doc.get("view", {}).get("zoom", 1)),
        },
    }
    return json.dumps(ordered, indent=2, ensure_ascii=False) + "\n"


def parse_document(text: str) -> dict[str, Any]:
    try:
        doc = json.loads(text) if text.strip() else empty_document()
    except json.JSONDecodeError as exc:
        raise GraphSpecError([f"The file is not valid JSON: {exc}"]) from None
    if not isinstance(doc, dict):
        raise GraphSpecError(["A graph file holds a JSON object."])
    doc.setdefault("version", SCHEMA_VERSION)
    for key in ("variables", "nodes", "edges"):
        if not isinstance(doc.get(key), list):
            doc[key] = []
    if not isinstance(doc.get("view"), dict):
        doc["view"] = {"pan": [0, 0], "zoom": 1}
    for node in doc["nodes"]:
        if isinstance(node, dict):
            node.setdefault("data", {})
            node.setdefault("pos", [0, 0])
    return doc


# -- building from a spec -----------------------------------------------------


def build(spec: dict[str, Any]) -> dict[str, Any]:
    """A whole document from a spec (see the graph_reference tool for its shape)."""

    if not isinstance(spec, dict):
        raise GraphSpecError(["The graph spec is a JSON object."])
    doc = empty_document(str(spec.get("name") or ""))
    problems: list[str] = []
    placed: set[str] = set()

    for item in _list(spec, "variables", problems):
        try:
            _add_variable(doc, item)
        except GraphSpecError as exc:
            problems.extend(exc.problems)

    for item in _list(spec, "nodes", problems):
        try:
            node = _new_node(doc, item)
        except GraphSpecError as exc:
            problems.extend(exc.problems)
            continue
        if "pos" in item:
            placed.add(node["id"])

    for item in _list(spec, "wires", problems):
        try:
            for wire in parse_wires(item):
                _connect(doc, wire, replace=False)
        except GraphSpecError as exc:
            problems.extend(exc.problems)

    if problems:
        raise GraphSpecError(problems)

    layout(doc, keep=placed)

    for item in _list(spec, "comments", problems):
        try:
            _add_comment(doc, item)
        except GraphSpecError as exc:
            problems.extend(exc.problems)
    if problems:
        raise GraphSpecError(problems)
    return doc


def _list(spec: dict[str, Any], key: str, problems: list[str]) -> list[Any]:
    value = spec.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        problems.append(f"'{key}' is a list.")
        return []
    return value


def _node_ids(doc: dict[str, Any]) -> set[str]:
    return {str(node.get("id")) for node in doc["nodes"]}


def _free_id(prefix: str, used: set[str]) -> str:
    index = 1
    while f"{prefix}{index}" in used:
        index += 1
    return f"{prefix}{index}"


def _new_node(doc: dict[str, Any], item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise GraphSpecError(["Each node is an object with at least a 'kind'."])
    kind = str(item.get("kind") or "").strip()
    spec = NODE_KINDS.get(kind)
    if spec is None:
        raise GraphSpecError(
            [f"Unknown node kind {kind!r}. Known kinds: {', '.join(sorted(NODE_KINDS))}."]
        )
    used = _node_ids(doc)
    node_id = str(item.get("id") or "").strip() or _free_id("n", used)
    if not _ID_RE.match(node_id):
        raise GraphSpecError(
            [f"Node id {node_id!r}: a letter, then letters, digits, - or _ (48 at most)."]
        )
    if node_id in used:
        raise GraphSpecError([f"Two nodes are called {node_id!r}."])
    data = _defaults(spec)
    given = item.get("data") or {}
    if not isinstance(given, dict):
        raise GraphSpecError([f"Node {node_id}: 'data' is an object."])
    data.update(copy.deepcopy(given))
    for key in ("note", "note_shown"):
        if key in item:
            data[key] = item[key]
    if isinstance(data.get("note"), str) and data["note"].strip():
        data.setdefault("note_shown", True)
    node = {"id": node_id, "kind": kind, "pos": _pos(item.get("pos"), node_id), "data": data}
    doc["nodes"].append(node)
    return node


def _defaults(spec: NodeKindSpec) -> dict[str, Any]:
    """What the editor fills in when it adds a node of this kind."""

    data: dict[str, Any] = {
        field.name: copy.deepcopy(field.default)
        for field in spec.fields
        if field.default is not None
    }
    for pin in spec.pins:
        if pin.dynamic:
            data["outputs" if pin.direction == "out" else "inputs"] = 2
    return data


def _pos(value: Any, node_id: str) -> list[int]:
    if value is None:
        return [0, 0]
    if (
        isinstance(value, list | tuple)
        and len(value) == 2
        and all(isinstance(item, int | float) and not isinstance(item, bool) for item in value)
    ):
        return [_snap(value[0]), _snap(value[1])]
    raise GraphSpecError([f"Node {node_id}: 'pos' is [x, y]."])


def _snap(value: float) -> int:
    return int(round(float(value) / _GRID) * _GRID)


def _add_variable(doc: dict[str, Any], item: Any) -> dict[str, Any]:
    if not isinstance(item, dict) or not str(item.get("name") or "").strip():
        raise GraphSpecError(["Each variable is an object with a 'name'."])
    name = str(item["name"]).strip()
    if any(existing["name"] == name for existing in doc["variables"]):
        raise GraphSpecError([f"Two variables are called {name!r}."])
    kind = str(item.get("type") or PinType.STRING)
    if kind not in VARIABLE_TYPES:
        raise GraphSpecError([f"Variable {name}: the type is one of {', '.join(VARIABLE_TYPES)}."])
    variable = {
        "id": _free_id("v", {str(existing["id"]) for existing in doc["variables"]}),
        "name": name,
        "type": kind,
        "default": item.get("default", _variable_default(kind)),
    }
    doc["variables"].append(variable)
    return variable


def _variable_default(kind: str) -> Any:
    return {"int": 0, "float": 0.0, "bool": False}.get(kind, "")


# -- wires --------------------------------------------------------------------


def parse_wires(item: Any) -> list[Wire]:
    """``"a.pin -> b.pin"``, a chain ``"a -> b -> c"``, or ``{"from": "a.pin", "to": "b.pin"}``.

    A side without a pin is the exec pin: ``out`` leaving, ``in`` arriving.
    """

    if isinstance(item, dict):
        source = str(item.get("from") or "")
        target = str(item.get("to") or "")
        if not source or not target:
            raise GraphSpecError([f"A wire object needs 'from' and 'to': {item!r}."])
        parts = [source, target]
    elif isinstance(item, str):
        parts = [part.strip() for part in item.split("->")]
    else:
        raise GraphSpecError([f"A wire is a string like 'a.out -> b.in', not {item!r}."])
    if len(parts) < 2 or any(not part for part in parts):
        raise GraphSpecError([f"Wire {item!r}: write it as 'a.pin -> b.pin'."])
    endpoints: list[tuple[str, str | None]] = []
    for part in parts:
        match = _ENDPOINT_RE.match(part)
        if not match:
            raise GraphSpecError([f"Wire {item!r}: {part!r} is not 'node' or 'node.pin'."])
        endpoints.append((match.group(1), match.group(2)))
    for node_id, pin in endpoints[1:-1]:
        if pin:
            raise GraphSpecError(
                [f"Wire {item!r}: a node inside a chain takes no pin ({node_id}.{pin})."]
            )
    return [
        Wire(left[0], left[1] or "out", right[0], right[1] or "in")
        for left, right in zip(endpoints, endpoints[1:], strict=False)
    ]


def _pin_names(node: dict[str, Any], direction: str) -> list[str]:
    spec = NODE_KINDS[node["kind"]]
    return [pin.name for pin in expanded_pins(spec, node["data"]) if pin.direction == direction]


def _grow_dynamic(node: dict[str, Any], pin_name: str, direction: str) -> None:
    """A wire to ``out_3`` asks for four outputs: the count follows the wires."""

    spec = NODE_KINDS[node["kind"]]
    declared = spec.pin(pin_name, direction)
    if declared is None or not declared.dynamic:
        return
    suffix = pin_name.rpartition("_")[2]
    if not suffix.isdigit():
        return
    key = "outputs" if direction == "out" else "inputs"
    wanted = min(MAX_DYNAMIC_PINS, int(suffix) + 1)
    if int(node["data"].get(key) or 2) < wanted:
        node["data"][key] = wanted


def _connect(doc: dict[str, Any], wire: Wire, *, replace: bool = True) -> dict[str, Any]:
    """Add ``wire``. With ``replace``, it takes the place of a wire the pin can only hold one of."""

    nodes = {node["id"]: node for node in doc["nodes"]}
    problems: list[str] = []
    for node_id in (wire.source, wire.target):
        if node_id not in nodes:
            problems.append(f"Wire {wire.text()}: there is no node {node_id!r}.")
    if problems:
        raise GraphSpecError(problems)
    source, target = nodes[wire.source], nodes[wire.target]
    out_spec = NODE_KINDS[source["kind"]].pin(wire.out_pin, "out")
    in_spec = NODE_KINDS[target["kind"]].pin(wire.in_pin, "in")
    if out_spec is None:
        problems.append(
            f"Wire {wire.text()}: {source['kind']} {wire.source} has no output "
            f"{wire.out_pin!r}; it has {', '.join(_pin_names(source, 'out')) or 'none'}."
        )
    if in_spec is None:
        problems.append(
            f"Wire {wire.text()}: {target['kind']} {wire.target} has no input "
            f"{wire.in_pin!r}; it has {', '.join(_pin_names(target, 'in')) or 'none'}."
        )
    if problems or out_spec is None or in_spec is None:
        raise GraphSpecError(problems)
    _grow_dynamic(source, wire.out_pin, "out")
    _grow_dynamic(target, wire.in_pin, "in")
    kind = "exec" if out_spec.type == PinType.EXEC else "data"
    edges = doc["edges"]
    for edge in edges:
        if _from(edge) == (wire.source, wire.out_pin) and _to(edge) == (wire.target, wire.in_pin):
            return edge
    if not replace:
        clash = next(
            (
                e
                for e in edges
                if (
                    kind == "exec"
                    and e["kind"] == "exec"
                    and _from(e) == (wire.source, wire.out_pin)
                )
                or (
                    kind == "data"
                    and not in_spec.multi
                    and e["kind"] == "data"
                    and _to(e) == (wire.target, wire.in_pin)
                )
            ),
            None,
        )
        if clash is not None:
            if kind == "exec":
                raise GraphSpecError(
                    [
                        f"Wire {wire.text()}: {wire.source}.{wire.out_pin} already leads to "
                        f"{clash['to'][0]}. An exec output drives one wire; fan out with a "
                        "sequence node."
                    ]
                )
            raise GraphSpecError(
                [
                    f"Wire {wire.text()}: {wire.target}.{wire.in_pin} already takes "
                    f"{clash['from'][0]}.{clash['from'][1]}, and takes one value."
                ]
            )
    # The editor's rules: an exec output drives one wire, and a single-value
    # input takes one; a new wire replaces the old one there.
    if kind == "exec":
        edges[:] = [
            e
            for e in edges
            if not (e["kind"] == "exec" and _from(e) == (wire.source, wire.out_pin))
        ]
    elif not in_spec.multi:
        edges[:] = [
            e for e in edges if not (e["kind"] == "data" and _to(e) == (wire.target, wire.in_pin))
        ]
    edge = {
        "id": _free_id("e", {str(e["id"]) for e in edges}),
        "kind": kind,
        "from": [wire.source, wire.out_pin],
        "to": [wire.target, wire.in_pin],
    }
    edges.append(edge)
    return edge


def _from(edge: dict[str, Any]) -> tuple[str, str]:
    return (str(edge["from"][0]), str(edge["from"][1]))


def _to(edge: dict[str, Any]) -> tuple[str, str]:
    return (str(edge["to"][0]), str(edge["to"][1]))


# -- comments -----------------------------------------------------------------


def _add_comment(doc: dict[str, Any], item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise GraphSpecError(["Each comment is an object with a 'title'."])
    used = _node_ids(doc)
    node_id = str(item.get("id") or "").strip() or _free_id("c", used)
    if not _ID_RE.match(node_id) or node_id in used:
        raise GraphSpecError([f"Comment id {node_id!r} is taken or not a valid id."])
    data: dict[str, Any] = {
        "title": str(item.get("title") or "Comment"),
        "color": str(item.get("color") or COMMENT_COLOR),
        "font_size": item.get("font_size", 18),
        "move_mode": str(item.get("move_mode") or "group"),
    }
    if not _COLOR_RE.match(data["color"]):
        raise GraphSpecError([f"Comment {node_id}: the colour is written #rrggbb."])
    if data["move_mode"] not in COMMENT_MOVE_MODES:
        raise GraphSpecError(
            [f"Comment {node_id}: move_mode is one of {', '.join(COMMENT_MOVE_MODES)}."]
        )
    held = item.get("nodes")
    if held:
        if not isinstance(held, list):
            raise GraphSpecError([f"Comment {node_id}: 'nodes' is a list of node ids."])
        nodes = {node["id"]: node for node in doc["nodes"]}
        missing = [str(name) for name in held if str(name) not in nodes]
        if missing:
            raise GraphSpecError([f"Comment {node_id}: no node {', '.join(missing)}."])
        boxes = [_box(nodes[str(name)], with_note=True) for name in held]
        left = min(box[0] for box in boxes) - _COMMENT_PAD
        top = min(box[1] for box in boxes) - _COMMENT_PAD - _COMMENT_HEADER
        right = max(box[2] for box in boxes) + _COMMENT_PAD
        bottom = max(box[3] for box in boxes) + _COMMENT_PAD
        pos = [_snap(left) - _GRID, _snap(top) - _GRID]
        data["width"] = _size(right - pos[0])
        data["height"] = _size(bottom - pos[1])
    elif "pos" in item:
        pos = _pos(item["pos"], node_id)
        data["width"] = _size(item.get("width", 400))
        data["height"] = _size(item.get("height", 240))
    else:
        raise GraphSpecError([f"Comment {node_id}: give the 'nodes' it holds, or a 'pos'."])
    comment = {"id": node_id, "kind": str(NodeKind.COMMENT), "pos": pos, "data": data}
    # First in the file, behind what it holds, the way the editor adds one.
    doc["nodes"].insert(0, comment)
    return comment


def _size(value: Any) -> int:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 240
    return int(min(COMMENT_MAX_SIZE, max(COMMENT_MIN_SIZE, _snap(number))))


# -- layout -------------------------------------------------------------------


def node_height(node: dict[str, Any]) -> int:
    spec = NODE_KINDS.get(node["kind"])
    if spec is None:
        return 100
    if spec.annotation:
        return int(node["data"].get("height") or 240)
    pins = expanded_pins(spec, node["data"])
    rows = max(
        sum(1 for pin in pins if pin.direction == "in"),
        sum(1 for pin in pins if pin.direction == "out"),
    )
    footer = _FOOTER_H if node["kind"] in _FOOTER_KINDS else 0
    return _HEADER_H + _BODY_PAD + rows * _ROW_H + _BODY_PAD + footer


def _box(node: dict[str, Any], *, with_note: bool = False) -> tuple[int, int, int, int]:
    """The node's rectangle; ``with_note`` takes in the bubble drawn above it."""

    x, y = node["pos"]
    width = int(node["data"].get("width") or NODE_W) if _is_comment(node) else NODE_W
    top = y - note_height(node) if with_note else y
    return (x, top, x + width, y + node_height(node))


def note_height(node: dict[str, Any]) -> int:
    """How far a node's open note bubble reaches above it (webview/flow/notes.tsx)."""

    note = node["data"].get("note")
    if not isinstance(note, str) or not note.strip() or node["data"].get("note_shown") is False:
        return 0
    per_line = 35
    lines = sum(max(1, -(-len(part) // per_line)) for part in note.split("\n"))
    return min(12, lines) * 15 + 14 + 9 + 4


def _is_comment(node: dict[str, Any]) -> bool:
    spec = NODE_KINDS.get(node["kind"])
    return bool(spec and spec.annotation)


def layout(doc: dict[str, Any], *, keep: set[str] | frozenset[str] = frozenset()) -> None:
    """Place every node not in ``keep``, Blueprint-style.

    The exec flow runs left to right from Start, a branch one row below the
    one before; what feeds a node data sits in a band under the flow, below
    the node it feeds, and what feeds that sits to its left.
    """

    nodes = {n["id"]: n for n in doc["nodes"] if not _is_comment(n)}
    if not nodes:
        return
    exec_out: dict[str, list[str]] = defaultdict(list)
    exec_in: dict[str, list[str]] = defaultdict(list)
    readers: dict[str, list[str]] = defaultdict(list)
    for edge in doc["edges"]:
        source, target = edge["from"][0], edge["to"][0]
        if source not in nodes or target not in nodes:
            continue
        if edge["kind"] == "exec":
            exec_out[source].append(target)
            exec_in[target].append(source)
        else:
            readers[source].append(target)

    # What has exec pins walks the flow; what has none is pulled, and sits below.
    flowing = [node_id for node_id, node in nodes.items() if not NODE_KINDS[node["kind"]].pure]
    in_flow = set(flowing)
    roots = [n for n in flowing if nodes[n]["kind"] == NodeKind.START] + [
        n for n in flowing if not exec_in[n] and nodes[n]["kind"] != NodeKind.START
    ]

    # Columns: the longest path from a root, back edges (a loop's return) left out.
    order: list[str] = []
    state: dict[str, int] = {}

    def visit(node_id: str) -> None:
        state[node_id] = 1
        for nxt in exec_out[node_id]:
            if state.get(nxt) is None:
                visit(nxt)
        state[node_id] = 2
        order.append(node_id)

    for root in roots:
        if state.get(root) is None:
            visit(root)
    for node_id in flowing:
        if state.get(node_id) is None:
            visit(node_id)
    order.reverse()
    rank = {node_id: index for index, node_id in enumerate(order)}
    column: dict[str, int] = {}
    for node_id in order:
        before = [p for p in exec_in[node_id] if p in rank and rank[p] < rank[node_id]]
        column[node_id] = max((column[p] + 1 for p in before), default=0)

    taken: dict[int, set[int]] = defaultdict(set)
    row: dict[str, int] = {}
    next_root = 0
    for node_id in order:
        before = [p for p in exec_in[node_id] if p in row]
        if before:
            parent = before[0]
            wanted = row[parent] + exec_out[parent].index(node_id)
        else:
            wanted = next_root
        slot = wanted
        while slot in taken[column[node_id]]:
            slot += 1
        taken[column[node_id]].add(slot)
        row[node_id] = slot
        if not before:
            next_root = max(next_root, slot + 1)

    flow_rows = max((slot for slot in row.values()), default=-1) + 1
    tallest = max(node_height(node) for node in nodes.values())
    # Room for the tallest note bubble too, so none reaches into the row above.
    row_h = tallest + _ROW_GAP + max(note_height(node) for node in nodes.values())
    row_h = -(-row_h // (_GRID * 2)) * _GRID * 2

    # The data band: providers under what they feed, chains growing leftwards.
    # Each column's band starts under that column's own flow, so a model node
    # sits right below the agent it feeds.
    band: dict[int, int] = defaultdict(int)
    for node_id in flowing:
        band[column[node_id]] = max(band[column[node_id]], row[node_id] + 1)
    pending = [n for n in nodes if n not in column]
    progress = True
    while pending and progress:
        progress = False
        for node_id in list(pending):
            consumers = [c for c in readers[node_id] if c in column]
            if not consumers:
                continue
            consumer = consumers[0]
            col = column[consumer] - (0 if consumer in in_flow else 1)
            column[node_id] = col
            row[node_id] = band[col]
            band[col] += 1
            pending.remove(node_id)
            progress = True
    # Whatever reads nothing and is read by nothing: a last row of its own.
    last = max([flow_rows, *band.values()])
    for index, node_id in enumerate(pending):
        column[node_id] = index
        row[node_id] = last

    for node_id, node in nodes.items():
        if node_id in keep:
            continue
        node["pos"] = [column[node_id] * (NODE_W + _COL_GAP), row[node_id] * row_h]
    _clear_overlaps(doc, moved=[n for n in nodes if n not in keep])


def place_new(doc: dict[str, Any], new_ids: list[str]) -> None:
    """Put nodes added to an existing graph next to what they are wired to."""

    nodes = {n["id"]: n for n in doc["nodes"]}
    placed = {n for n in nodes if n not in new_ids}
    for node_id in new_ids:
        node = nodes.get(node_id)
        if node is None:
            continue
        anchor = _anchor(doc, nodes, node_id, placed)
        if anchor is None:
            boxes = [_box(nodes[n]) for n in placed] or [(0, 0, 0, 0)]
            node["pos"] = [_snap(min(b[0] for b in boxes)), _snap(max(b[3] for b in boxes) + 80)]
        else:
            node["pos"] = [_snap(anchor[0]), _snap(anchor[1])]
        placed.add(node_id)
        _clear_overlaps(doc, moved=[node_id], among=placed)


def _anchor(
    doc: dict[str, Any], nodes: dict[str, dict[str, Any]], node_id: str, placed: set[str]
) -> tuple[float, float] | None:
    step = NODE_W + _COL_GAP
    for edge in doc["edges"]:
        source, target = edge["from"][0], edge["to"][0]
        if edge["kind"] == "exec" and target == node_id and source in placed:
            x, y = nodes[source]["pos"]
            return (x + step, y)
        if edge["kind"] == "exec" and source == node_id and target in placed:
            x, y = nodes[target]["pos"]
            return (x - step, y)
    for edge in doc["edges"]:
        source, target = edge["from"][0], edge["to"][0]
        if edge["kind"] == "data" and source == node_id and target in placed:
            x, y = nodes[target]["pos"]
            return (x, y + node_height(nodes[target]) + 40)
        if edge["kind"] == "data" and target == node_id and source in placed:
            x, y = nodes[source]["pos"]
            return (x + step, y)
    return None


def _clear_overlaps(
    doc: dict[str, Any], *, moved: list[str], among: set[str] | None = None
) -> None:
    """Push each moved node down until it covers no other node."""

    nodes = {n["id"]: n for n in doc["nodes"] if not _is_comment(n)}
    others = set(nodes) if among is None else {n for n in among if n in nodes}
    for node_id in moved:
        node = nodes.get(node_id)
        if node is None:
            continue
        for _ in range(200):
            box = _box(node)
            hit = next(
                (
                    other
                    for other in others
                    if other != node_id and _overlaps(box, _box(nodes[other]), margin=20)
                ),
                None,
            )
            if hit is None:
                break
            node["pos"] = [node["pos"][0], _snap(_box(nodes[hit])[3] + _ROW_GAP)]


def _overlaps(a: tuple[int, ...], b: tuple[int, ...], *, margin: int) -> bool:
    return (
        a[0] < b[2] + margin
        and b[0] < a[2] + margin
        and a[1] < b[3] + margin
        and b[1] < a[3] + margin
    )


# -- editing ------------------------------------------------------------------

EDIT_OPS = (
    "add_node",
    "update_node",
    "remove_node",
    "connect",
    "disconnect",
    "add_variable",
    "update_variable",
    "remove_variable",
    "add_comment",
    "set_note",
    "rename",
)


def apply_edits(doc: dict[str, Any], operations: Any) -> list[str]:
    """Apply ``operations`` in order to ``doc``; returns what each did, in a line.

    Nothing is half-applied: the caller works on a copy and keeps it only when
    every operation went through and the result compiles.
    """

    if not isinstance(operations, list) or not operations:
        raise GraphSpecError(["'operations' is a non-empty list."])
    done: list[str] = []
    added: list[str] = []
    for index, op in enumerate(operations, start=1):
        if not isinstance(op, dict) or op.get("op") not in EDIT_OPS:
            raise GraphSpecError([f"Operation {index}: 'op' is one of {', '.join(EDIT_OPS)}."])
        try:
            done.append(_apply(doc, op, added))
        except GraphSpecError as exc:
            raise GraphSpecError(
                [f"Operation {index} ({op['op']}): {p}" for p in exc.problems]
            ) from None
    place_new(doc, [node_id for node_id in added if node_id in _node_ids(doc)])
    return done


def _apply(doc: dict[str, Any], op: dict[str, Any], added: list[str]) -> str:
    kind = op["op"]
    nodes = {node["id"]: node for node in doc["nodes"]}
    if kind == "add_node":
        node = _new_node(doc, op)
        if "pos" not in op:
            added.append(node["id"])
        for wire_text in op.get("wires") or []:
            for wire in parse_wires(wire_text):
                _connect(doc, wire)
        return f"added {node['kind']} {node['id']}"
    if kind == "add_comment":
        comment = _add_comment(doc, op)
        return f"added comment {comment['id']}"
    if kind == "rename":
        doc["name"] = str(op.get("name") or "")
        return f"renamed the graph {doc['name']!r}"
    if kind in ("connect", "disconnect"):
        wires = parse_wires(op.get("wire"))
        for wire in wires:
            if kind == "connect":
                _connect(doc, wire)
            else:
                _disconnect(doc, wire)
        return f"{kind}ed {', '.join(wire.text() for wire in wires)}"
    if kind in ("add_variable", "update_variable", "remove_variable"):
        return _variable_op(doc, op)

    node_id = str(op.get("id") or "")
    node = nodes.get(node_id)
    if node is None:
        raise GraphSpecError([f"There is no node {node_id!r}."])
    if kind == "remove_node":
        doc["nodes"] = [item for item in doc["nodes"] if item["id"] != node_id]
        doc["edges"] = [e for e in doc["edges"] if node_id not in (e["from"][0], e["to"][0])]
        return f"removed {node_id}"
    if kind == "set_note":
        note = op.get("note")
        if note is None or (isinstance(note, str) and not note.strip()):
            node["data"].pop("note", None)
            node["data"].pop("note_shown", None)
            return f"removed the note on {node_id}"
        node["data"]["note"] = str(note)
        node["data"]["note_shown"] = bool(op.get("shown", True))
        return f"set the note on {node_id}"
    # update_node
    patch = op.get("data") or {}
    if not isinstance(patch, dict):
        raise GraphSpecError([f"Node {node_id}: 'data' is an object."])
    for key, value in patch.items():
        if value is None:
            node["data"].pop(key, None)
        else:
            node["data"][key] = copy.deepcopy(value)
    if "pos" in op:
        node["pos"] = _pos(op["pos"], node_id)
    if any(key in patch for key in ("outputs", "inputs")):
        # A pin count that shrank drops the wires on the pins that went.
        live_out = set(_pin_names(node, "out"))
        live_in = set(_pin_names(node, "in"))
        doc["edges"] = [
            e
            for e in doc["edges"]
            if not (e["from"][0] == node_id and e["from"][1] not in live_out)
            and not (e["to"][0] == node_id and e["to"][1] not in live_in)
        ]
    return f"updated {node_id}"


def _disconnect(doc: dict[str, Any], wire: Wire) -> None:
    before = len(doc["edges"])
    doc["edges"] = [
        e
        for e in doc["edges"]
        if not (_from(e) == (wire.source, wire.out_pin) and _to(e) == (wire.target, wire.in_pin))
    ]
    if len(doc["edges"]) == before:
        raise GraphSpecError([f"There is no wire {wire.text()}."])


def _variable_op(doc: dict[str, Any], op: dict[str, Any]) -> str:
    kind = op["op"]
    if kind == "add_variable":
        variable = _add_variable(doc, op)
        return f"added variable {variable['name']}"
    name = str(op.get("name") or "")
    variable = next((item for item in doc["variables"] if item["name"] == name), None)
    if variable is None:
        raise GraphSpecError([f"There is no variable {name!r}."])
    readers = [
        node
        for node in doc["nodes"]
        if node["kind"] in (NodeKind.VAR_GET, NodeKind.VAR_SET)
        and node["data"].get("variable") == name
    ]
    if kind == "remove_variable":
        doc["variables"] = [item for item in doc["variables"] if item is not variable]
        for node in readers:
            node["data"]["variable"] = ""
        return f"removed variable {name}"
    new_name = str(op.get("new_name") or "").strip()
    if new_name and new_name != name:
        if any(item["name"] == new_name for item in doc["variables"]):
            raise GraphSpecError([f"A variable is already called {new_name!r}."])
        variable["name"] = new_name
        # The nodes that read it follow the rename, as they do in the editor.
        for node in readers:
            node["data"]["variable"] = new_name
    if "type" in op:
        if op["type"] not in VARIABLE_TYPES:
            raise GraphSpecError([f"The type is one of {', '.join(VARIABLE_TYPES)}."])
        variable["type"] = op["type"]
    if "default" in op:
        variable["default"] = op["default"]
    return f"updated variable {variable['name']}"


# -- reading back -------------------------------------------------------------


def to_spec(doc: dict[str, Any]) -> dict[str, Any]:
    """A document in the spec's own terms: what write_graph takes and edit_graph edits."""

    nodes: list[dict[str, Any]] = []
    comments: list[dict[str, Any]] = []
    plain = [node for node in doc.get("nodes", []) if not _is_comment(node)]
    for node in doc.get("nodes", []):
        if _is_comment(node):
            held = [other["id"] for other in plain if _inside(_box(other), _box(node))]
            comments.append(
                {
                    "id": node["id"],
                    "title": node["data"].get("title", ""),
                    "color": node["data"].get("color", COMMENT_COLOR),
                    "nodes": held,
                }
            )
            continue
        spec = NODE_KINDS.get(node["kind"])
        defaults = _defaults(spec) if spec else {}
        data = {
            key: value for key, value in node["data"].items() if defaults.get(key, _UNSET) != value
        }
        entry: dict[str, Any] = {"id": node["id"], "kind": node["kind"]}
        note = data.pop("note", None)
        shown = data.pop("note_shown", None)
        if data:
            entry["data"] = data
        if note:
            entry["note"] = note
            if shown is False:
                entry["note_shown"] = False
        entry["pos"] = node["pos"]
        nodes.append(entry)
    return {
        "name": doc.get("name", ""),
        "variables": [
            {"name": item["name"], "type": item.get("type"), "default": item.get("default")}
            for item in doc.get("variables", [])
        ],
        "nodes": nodes,
        "wires": [
            f"{edge['from'][0]}.{edge['from'][1]} -> {edge['to'][0]}.{edge['to'][1]}"
            for edge in doc.get("edges", [])
        ],
        "comments": comments,
    }


_UNSET = object()


def _inside(inner: tuple[int, ...], outer: tuple[int, ...]) -> bool:
    return (
        inner[0] >= outer[0]
        and inner[1] >= outer[1]
        and inner[2] <= outer[2]
        and inner[3] <= outer[3]
    )


def format_issues(issues: list[ValidationIssue]) -> list[str]:
    lines = []
    for issue in issues:
        where = issue.node_id or "graph"
        if issue.pin:
            where += f".{issue.pin}"
        if issue.field:
            where += f" ({issue.field})"
        lines.append(f"{issue.severity} at {where}: {issue.message}")
    return lines
