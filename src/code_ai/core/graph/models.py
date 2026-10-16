"""The `*.flow.json` contract: node kinds, pins, and the document itself.

This module is the single source of truth for what a graph may contain. The
editor does not hardcode the catalog - it asks for it (``catalog_payload``) and
builds its palette, its pin layout and its inspector from what comes back. That
is what lets a new node kind or a new field ship without a new VSIX.

Pin conventions worth knowing before reading the rest:

- **exec** pins carry the flow. Exactly one token is live at a time, which is
  what makes the editor's travelling indicator unambiguous.
- **data** pins are pull-based: a node about to run pulls its inputs, and a pure
  node (no exec pins) is evaluated on demand.
- An unconnected data input falls back to ``node.data[pin_name]`` as a literal,
  the way a Blueprint pin shows an inline default. Small graphs need no wires
  for constants.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Bumped only on a breaking change to the document shape. A reader that sees a
# higher version refuses the file instead of guessing.
SCHEMA_VERSION = 1

# Ceilings that keep a hand-edited (or generated) file from becoming a denial of
# service on the editor and the interpreter alike.
MAX_NODES = 400
MAX_EDGES = 1200
MAX_VARIABLES = 100
# A Sequence's outputs, an Append's parts: how far "+ Add pin" goes.
MAX_DYNAMIC_PINS = 12


class NodeKind(StrEnum):
    START = "start"
    END = "end"
    MODEL = "model"
    PROFESSION = "profession"
    CUSTOM_PROFESSION = "custom_profession"
    CLEAN_AGENT = "clean_agent"
    TOOLS = "tools"
    SEQUENCE = "sequence"
    DECISION = "decision"
    LOOP = "loop"
    USER_CHAT = "user_chat"
    VAR_SET = "var_set"
    VAR_GET = "var_get"
    PRINT = "print"
    # Pure functions, Blueprint-style: computed whenever something reads them.
    MATH = "math"
    COMPARE = "compare"
    LOGIC = "logic"
    SELECT = "select"
    APPEND = "append"
    FORMAT = "format"
    TEXT_CASE = "text_case"
    TEXT_REPLACE = "text_replace"
    TEXT_CHECK = "text_check"
    TEXT_LENGTH = "text_length"
    SUBSTRING = "substring"
    SPLIT = "split"
    TO_INT = "to_int"
    TO_FLOAT = "to_float"
    TO_TEXT = "to_text"
    TO_BOOL = "to_bool"
    # An annotation, Unreal-style: never runs, only names and groups.
    COMMENT = "comment"


class PinType(StrEnum):
    EXEC = "exec"
    STRING = "string"
    INT = "int"
    FLOAT = "float"
    BOOL = "bool"
    MODEL = "model"
    PROFESSION = "profession"
    TOOLS = "tools"
    ANY = "any"


# Blueprint-adjacent, tuned to stay legible over both VSCode themes. Kept beside
# the types so a new type cannot ship without a colour.
PIN_COLORS: dict[str, str] = {
    PinType.EXEC: "#FFFFFF",
    PinType.BOOL: "#8C1B1B",
    PinType.INT: "#1F9E8C",
    PinType.FLOAT: "#A4E22E",
    PinType.STRING: "#E040FB",
    PinType.MODEL: "#3794FF",
    PinType.PROFESSION: "#F5A623",
    PinType.TOOLS: "#9D7BFF",
    PinType.ANY: "#A0A0A0",
}

# The types a user-declared variable may have. Deliberately scalar: a graph
# variable is a knob, not a data structure.
VARIABLE_TYPES: tuple[str, ...] = (PinType.STRING, PinType.INT, PinType.FLOAT, PinType.BOOL)

# The switches that say what an agent may touch: node field, the capability it
# grants, and its label. The interpreter only sees field names; the executor
# turns them into capabilities.
PERMISSIONS: tuple[tuple[str, str, str], ...] = (
    ("can_read", "local_read", "Read files"),
    ("can_write", "local_write", "Edit files"),
    ("can_run", "process", "Run commands"),
    ("can_browse", "web", "Use the web"),
    ("can_review", "review", "Run reviews"),
    ("can_mcp", "mcp", "Use MCP tools"),
)
PERMISSION_FIELDS: tuple[str, ...] = tuple(field for field, _, _ in PERMISSIONS)

# A note any node may carry, shown as a bubble above it (Unreal's node
# comment): the text, and whether the bubble is open. Never read by a run.
NOTE_FIELDS: tuple[str, ...] = ("note", "note_shown")

# A comment box's geometry and look. Bounds keep a hand-edited file from
# drawing a box the editor cannot handle.
COMMENT_COLOR = "#64748b"
COMMENT_MIN_SIZE = 80
COMMENT_MAX_SIZE = 20000
COMMENT_FONT_SIZES = (10, 48)
COMMENT_MOVE_MODES = ("group", "comment")

# How the editor groups the tool list, in display order. The first five follow
# the capability a tool needs; the rest are the on-demand groups by name.
TOOL_GROUPS: tuple[tuple[str, str], ...] = (
    ("read", "Read"),
    ("edit", "Edit"),
    ("commands", "Commands"),
    ("web", "Web"),
    ("review", "Review"),
    ("browser", "Browser"),
    ("documents", "Documents"),
    ("pdf", "PDF"),
    ("slides", "Slides"),
    ("design", "Design"),
    ("latex", "LaTeX"),
    ("graphs", "Graphs"),
    ("android", "Android"),
)


def can_connect(source: str, target: str) -> bool:
    """Whether a data pin of type ``source`` may feed one of type ``target``.

    Exec never mixes with data. ``any`` is the escape hatch in both directions
    (a print node takes whatever it is given). Widening int -> float and
    stringifying scalars are allowed because refusing them would only push the
    user into writing a conversion node for no gain.
    """

    if source == PinType.EXEC or target == PinType.EXEC:
        return source == target
    if source == target:
        return True
    if PinType.ANY in (source, target):
        return True
    if source == PinType.INT and target == PinType.FLOAT:
        return True
    if target == PinType.STRING and source in (PinType.INT, PinType.FLOAT, PinType.BOOL):
        return True
    return False


@dataclass(frozen=True, slots=True)
class PinSpec:
    """One pin on a node kind."""

    name: str
    type: str
    direction: str  # "in" | "out"
    label: str = ""
    # A data input the compiler insists on: either wired, or given a literal in
    # ``node.data``. Exec inputs are never required (an orphan node is legal,
    # it simply never runs).
    required: bool = False
    # "+ Add pin": the editor may add/remove numbered copies. Outputs count in
    # node.data["outputs"], inputs in node.data["inputs"]; a "#" in the label
    # becomes the copy's number.
    dynamic: bool = False
    # A data input that takes any number of wires and reads them as a list.
    multi: bool = False

    def payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": str(self.type),
            "direction": self.direction,
            "label": self.label or self.name,
            "required": self.required,
            "dynamic": self.dynamic,
            "multi": self.multi,
            "color": PIN_COLORS.get(self.type, PIN_COLORS[PinType.ANY]),
        }


@dataclass(frozen=True, slots=True)
class FieldSpec:
    """One inspector control for a node kind.

    ``kind`` names the widget rather than the storage type, because that is what
    the editor needs to render it; validation of the stored value is the
    compiler's job either way.
    """

    name: str
    # text | textarea | number | select | bool | variable | profession | model
    # | permission (a switch that may be left unset) | tools (a list of tool names)
    kind: str
    label: str
    default: Any = None
    options: tuple[str, ...] = ()
    help: str = ""
    # The inspector section it sits in; empty for kinds with too few to group.
    group: str = ""

    def payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "label": self.label,
            "default": self.default,
            "options": list(self.options),
            "help": self.help,
            "group": self.group,
        }


@dataclass(frozen=True, slots=True)
class NodeKindSpec:
    """Everything the editor and the compiler need to know about one node kind."""

    kind: str
    title: str
    description: str
    # Header tint in the editor, by role: flow control, agent work, data, debug.
    category: str
    pins: tuple[PinSpec, ...]
    fields: tuple[FieldSpec, ...] = ()
    # A node with no exec pins is pure: it is pulled, never walked into.
    max_instances: int = 0  # 0 = unlimited
    # Drawn and saved but never compiled into the program: a comment box.
    annotation: bool = False

    @property
    def pure(self) -> bool:
        return not self.annotation and not any(pin.type == PinType.EXEC for pin in self.pins)

    def pin(self, name: str, direction: str) -> PinSpec | None:
        for spec in self.pins:
            if spec.name == name and spec.direction == direction:
                return spec
        # A dynamic pin is declared once ("out_0") and instantiated as out_0,
        # out_1, ... so accept any numbered sibling of a dynamic declaration.
        base, _, suffix = name.rpartition("_")
        if suffix.isdigit():
            for spec in self.pins:
                if spec.dynamic and spec.direction == direction and spec.name.startswith(base):
                    return spec
        return None

    def payload(self) -> dict[str, Any]:
        return {
            "kind": str(self.kind),
            "title": self.title,
            "description": self.description,
            "category": self.category,
            "pure": self.pure,
            "max_instances": self.max_instances,
            "annotation": self.annotation,
            "pins": [pin.payload() for pin in self.pins],
            "fields": [item.payload() for item in self.fields],
        }


def _exec_in(label: str = "") -> PinSpec:
    return PinSpec(name="in", type=PinType.EXEC, direction="in", label=label)


def _exec_out(name: str = "out", label: str = "", dynamic: bool = False) -> PinSpec:
    return PinSpec(name=name, type=PinType.EXEC, direction="out", label=label, dynamic=dynamic)


def _permission_fields(*, inherit: bool) -> tuple[FieldSpec, ...]:
    """The permission switches. On an Agent they start unset and follow its profession."""

    return tuple(
        FieldSpec(
            name=field,
            kind="permission",
            label=label,
            default=None if inherit else field == "can_read",
        )
        for field, _, label in PERMISSIONS
    )


_BLOCKED_TOOLS = FieldSpec(
    name="blocked_tools",
    kind="tools",
    label="Never use",
    default=None,
    help="Taken away whatever the switches or the wired Tools nodes allow.",
)


def _grouped(group: str, *fields: FieldSpec) -> tuple[FieldSpec, ...]:
    return tuple(dataclasses.replace(item, group=group) for item in fields)


def _in(name: str, pin_type: str, label: str = "") -> PinSpec:
    return PinSpec(name=name, type=pin_type, direction="in", label=label)


def _out(name: str, pin_type: str) -> PinSpec:
    return PinSpec(name=name, type=pin_type, direction="out")


def _op(options: tuple[str, ...]) -> FieldSpec:
    return FieldSpec(
        name="op", kind="select", label="Operation", default=options[0], options=options
    )


def _text(name: str, label: str, *, area: bool = False, help: str = "") -> FieldSpec:
    return FieldSpec(
        name=name, kind="textarea" if area else "text", label=label, default="", help=help
    )


def dynamic_count(data: dict[str, Any], direction: str) -> int:
    """How many copies of a dynamic pin a node asked for."""

    raw = data.get("outputs" if direction == "out" else "inputs", 2)
    try:
        count = int(raw)
    except (TypeError, ValueError):
        count = 2
    return max(1, min(MAX_DYNAMIC_PINS, count))


def expanded_pins(spec: NodeKindSpec, data: dict[str, Any]) -> list[PinSpec]:
    """The pins a node really has, each dynamic declaration expanded to its copies."""

    pins: list[PinSpec] = []
    for pin in spec.pins:
        if not pin.dynamic:
            pins.append(pin)
            continue
        if not pin.name.endswith("_0"):
            continue
        base = pin.name[:-2]
        for index in range(dynamic_count(data, pin.direction)):
            label = pin.label.replace("#", str(index)) if "#" in pin.label else str(index)
            pins.append(dataclasses.replace(pin, name=f"{base}_{index}", label=label))
    return pins


_KIND_LIST: tuple[NodeKindSpec, ...] = (
    NodeKindSpec(
        kind=NodeKind.START,
        title="Start",
        description="Where the main line begins. Exactly one per graph.",
        category="flow",
        pins=(_exec_out(),),
        max_instances=1,
    ),
    NodeKindSpec(
        kind=NodeKind.END,
        title="End",
        description="Ends the run. Whatever reaches `result` is the run's answer.",
        category="flow",
        pins=(
            _exec_in(),
            PinSpec(name="result", type=PinType.ANY, direction="in"),
        ),
    ),
    NodeKindSpec(
        kind=NodeKind.MODEL,
        title="Model",
        description=(
            "An endpoint plus a model name. Feed it into the agents that should run "
            "on it; agents with no model wired run on the session's own."
        ),
        category="data",
        pins=(PinSpec(name="model", type=PinType.MODEL, direction="out"),),
        fields=(
            *_grouped(
                "Endpoint",
                FieldSpec(
                    name="api_mode",
                    kind="select",
                    label="API mode",
                    default="",
                    options=("", "responses", "completions", "ollama"),
                    help="Empty follows the session setting.",
                ),
                FieldSpec(
                    name="base_url",
                    kind="text",
                    label="Base URL",
                    default="",
                    help="Empty follows the session setting.",
                ),
                FieldSpec(
                    name="api_key_ref",
                    kind="text",
                    label="API key",
                    default="session",
                    help=(
                        "session (use the session key), env:NAME, or secret:NAME. "
                        "A literal key is refused - this file goes into git."
                    ),
                ),
                FieldSpec(name="model", kind="model", label="Model", default=""),
            ),
            *_grouped(
                "Sampling",
                FieldSpec(
                    name="reasoning_effort",
                    kind="select",
                    label="Reasoning effort",
                    default="",
                    options=("", "none", "minimal", "low", "medium", "high", "xhigh"),
                    help="Empty follows the session; none turns thinking off.",
                ),
                FieldSpec(name="temperature", kind="number", label="Temperature", default=None),
                FieldSpec(name="top_p", kind="number", label="Top P", default=None),
                FieldSpec(name="top_k", kind="number", label="Top K", default=None),
            ),
        ),
    ),
    NodeKindSpec(
        kind=NodeKind.PROFESSION,
        title="Agent",
        description=(
            "One isolated agent running one task to a report. You decide who it is "
            "(a profession, or your own instructions) and what it may touch (the "
            "switches, or Tools nodes wired into `tools`)."
        ),
        category="agent",
        pins=(
            _exec_in(),
            _exec_out(),
            PinSpec(name="model", type=PinType.MODEL, direction="in"),
            PinSpec(name="profession", type=PinType.PROFESSION, direction="in"),
            PinSpec(name="tools", type=PinType.TOOLS, direction="in", multi=True),
            PinSpec(name="task", type=PinType.STRING, direction="in", required=True),
            PinSpec(name="report", type=PinType.STRING, direction="out"),
        ),
        fields=(
            *_grouped(
                "Who",
                FieldSpec(
                    name="name",
                    kind="text",
                    label="Name",
                    default="",
                    help="What to call it on the node and in the log. Empty uses the profession.",
                ),
                FieldSpec(
                    name="profession",
                    kind="profession",
                    label="Profession",
                    default="",
                    help=(
                        "A ready-made role and toolbox. Custom means you write the role "
                        "under Instructions. Ignored while a Profession node is wired in."
                    ),
                ),
                FieldSpec(
                    name="role_override",
                    kind="textarea",
                    label="Instructions",
                    default="",
                    help=(
                        "Custom: who this agent is and how it works. With a profession: "
                        "added to its role."
                    ),
                ),
            ),
            *_grouped("Task", FieldSpec(name="task", kind="textarea", label="Task", default="")),
            *_grouped("Access", *_permission_fields(inherit=True), _BLOCKED_TOOLS),
            *_grouped(
                "Limits",
                FieldSpec(
                    name="max_steps",
                    kind="number",
                    label="Max steps",
                    default=0,
                    help="0 keeps the profession's budget.",
                ),
                FieldSpec(
                    name="timeout_s",
                    kind="number",
                    label="Timeout (s)",
                    default=0,
                    help="0 keeps the profession's budget.",
                ),
            ),
        ),
    ),
    NodeKindSpec(
        kind=NodeKind.CUSTOM_PROFESSION,
        title="Profession",
        description=(
            "A job description you write once: the role prompt is the job, the "
            "switches are what it may touch. Wire it into the profession pin of "
            "every Agent that shares the job."
        ),
        category="agent",
        pins=(PinSpec(name="profession", type=PinType.PROFESSION, direction="out"),),
        fields=(
            *_grouped(
                "Who",
                FieldSpec(
                    name="name",
                    kind="text",
                    label="Name",
                    default="",
                    help="What to call it. Shows on the node and in the run log.",
                ),
                FieldSpec(
                    name="role",
                    kind="textarea",
                    label="Role",
                    default="",
                    help=(
                        "Who this agent is and how it works - the same voice as the "
                        "built-in professions. It is the whole job description."
                    ),
                ),
                FieldSpec(
                    name="framing",
                    kind="bool",
                    label="Add workspace framing",
                    default=False,
                    help=(
                        "Off, the role text is the entire system prompt. On, the usual "
                        "sub-agent preamble is added: workspace path, rules, report-back "
                        "instructions. Leave it off for agents that talk to each other."
                    ),
                ),
            ),
            *_grouped("Access", *_permission_fields(inherit=False), _BLOCKED_TOOLS),
            *_grouped(
                "Limits",
                FieldSpec(
                    name="max_steps",
                    kind="number",
                    label="Max steps",
                    default=40,
                    help="How many tool round-trips it gets before it must report.",
                ),
                FieldSpec(
                    name="timeout_s",
                    kind="number",
                    label="Timeout (s)",
                    default=0,
                    help="0 keeps the worker budget from settings.",
                ),
            ),
        ),
    ),
    NodeKindSpec(
        kind=NodeKind.CLEAN_AGENT,
        title="Clean agent",
        description=(
            "Sends the task to the model as it is: no system prompt unless you write "
            "one, no framing, no tools, nothing added. The reply is the report."
        ),
        category="agent",
        pins=(
            _exec_in(),
            _exec_out(),
            PinSpec(name="model", type=PinType.MODEL, direction="in"),
            PinSpec(name="system", type=PinType.STRING, direction="in"),
            PinSpec(name="task", type=PinType.STRING, direction="in", required=True),
            PinSpec(name="report", type=PinType.STRING, direction="out"),
        ),
        fields=(
            *_grouped(
                "Who",
                FieldSpec(
                    name="name",
                    kind="text",
                    label="Name",
                    default="",
                    help="What to call it on the node and in the log.",
                ),
                FieldSpec(
                    name="system",
                    kind="textarea",
                    label="System prompt",
                    default="",
                    help="Sent exactly as written. Empty sends no system message at all.",
                ),
            ),
            *_grouped("Task", FieldSpec(name="task", kind="textarea", label="Task", default="")),
            *_grouped(
                "Limits",
                FieldSpec(
                    name="timeout_s",
                    kind="number",
                    label="Timeout (s)",
                    default=0,
                    help="0 keeps the worker budget from settings.",
                ),
            ),
        ),
    ),
    NodeKindSpec(
        kind=NodeKind.TOOLS,
        title="Tools",
        description=(
            "A hand-picked set of tools. Wire it into an Agent's tools pin and the "
            "agent gets exactly these; wire several and they add up."
        ),
        category="agent",
        pins=(PinSpec(name="tools", type=PinType.TOOLS, direction="out"),),
        fields=(
            FieldSpec(
                name="name",
                kind="text",
                label="Name",
                default="",
                help="Shows on the node, e.g. 'read-only' or 'git'.",
            ),
            FieldSpec(name="tools", kind="tools", label="Tools", default=None),
        ),
    ),
    NodeKindSpec(
        kind=NodeKind.SEQUENCE,
        title="Sequence",
        description="Runs each output in order, waiting for one branch to finish before the next.",
        category="flow",
        pins=(
            _exec_in(),
            _exec_out("out_0", label="0", dynamic=True),
            _exec_out("out_1", label="1", dynamic=True),
        ),
    ),
    NodeKindSpec(
        kind=NodeKind.DECISION,
        title="Decision",
        description=(
            "Two ways forward. `bool` reads a wired condition; `judge` asks a model "
            "a yes/no question about the run so far."
        ),
        category="flow",
        pins=(
            _exec_in(),
            _exec_out("true", label="true"),
            _exec_out("false", label="false"),
            PinSpec(name="condition", type=PinType.BOOL, direction="in"),
            PinSpec(name="prompt", type=PinType.STRING, direction="in"),
            PinSpec(name="model", type=PinType.MODEL, direction="in"),
        ),
        fields=(
            FieldSpec(
                name="mode",
                kind="select",
                label="Mode",
                default="bool",
                options=("bool", "judge"),
            ),
            FieldSpec(name="condition", kind="bool", label="Condition", default=False),
            FieldSpec(
                name="prompt",
                kind="textarea",
                label="Question",
                default="",
                help="Answerable with yes or no.",
            ),
        ),
    ),
    NodeKindSpec(
        kind=NodeKind.LOOP,
        title="Loop",
        description=(
            "Repeats `body` and then continues from `completed`. Always bounded: "
            "the iteration cap applies whatever the kind."
        ),
        category="flow",
        pins=(
            _exec_in(),
            _exec_out("body", label="body"),
            _exec_out("completed", label="completed"),
            PinSpec(name="count", type=PinType.INT, direction="in"),
            PinSpec(name="condition", type=PinType.BOOL, direction="in"),
            PinSpec(name="items", type=PinType.STRING, direction="in"),
            PinSpec(name="index", type=PinType.INT, direction="out"),
            PinSpec(name="item", type=PinType.STRING, direction="out"),
        ),
        fields=(
            FieldSpec(
                name="kind",
                kind="select",
                label="Kind",
                default="count",
                options=("count", "while", "foreach"),
                help="foreach walks one line of `items` per lap.",
            ),
            FieldSpec(name="count", kind="number", label="Count", default=3),
            FieldSpec(
                name="max_iterations",
                kind="number",
                label="Max iterations",
                default=0,
                help="0 uses the configured ceiling.",
            ),
        ),
    ),
    NodeKindSpec(
        kind=NodeKind.USER_CHAT,
        title="Ask the user",
        description="Pauses the run until the user types an answer on the node.",
        category="agent",
        pins=(
            _exec_in(),
            _exec_out(),
            PinSpec(name="prompt", type=PinType.STRING, direction="in"),
            PinSpec(name="answer", type=PinType.STRING, direction="out"),
        ),
        fields=(
            FieldSpec(name="title", kind="text", label="Title", default=""),
            FieldSpec(name="prompt", kind="textarea", label="Question", default=""),
        ),
    ),
    NodeKindSpec(
        kind=NodeKind.VAR_SET,
        title="Set variable",
        description="Writes a graph variable.",
        category="data",
        pins=(
            _exec_in(),
            _exec_out(),
            PinSpec(name="value", type=PinType.ANY, direction="in", required=True),
        ),
        fields=(
            FieldSpec(name="variable", kind="variable", label="Variable", default=""),
            FieldSpec(name="value", kind="text", label="Value", default=""),
            FieldSpec(
                name="mode",
                kind="select",
                label="Mode",
                default="set",
                options=("set", "append"),
                help=(
                    "append joins onto what is already there, separated by a blank "
                    "line - how a transcript grows across a loop."
                ),
            ),
            FieldSpec(
                name="prefix",
                kind="text",
                label="Prefix",
                default="",
                help="Put in front of the appended text, e.g. a speaker's name.",
            ),
        ),
    ),
    NodeKindSpec(
        kind=NodeKind.VAR_GET,
        title="Get variable",
        description="Reads a graph variable.",
        category="data",
        pins=(PinSpec(name="value", type=PinType.ANY, direction="out"),),
        fields=(FieldSpec(name="variable", kind="variable", label="Variable", default=""),),
    ),
    NodeKindSpec(
        kind=NodeKind.PRINT,
        title="Print",
        description="Writes a value to the run log. The debugger's best friend.",
        category="debug",
        pins=(
            _exec_in(),
            _exec_out(),
            PinSpec(name="value", type=PinType.ANY, direction="in"),
        ),
        fields=(
            FieldSpec(name="label", kind="text", label="Label", default=""),
            FieldSpec(name="value", kind="textarea", label="Value", default=""),
        ),
    ),
    # -- pure functions -----------------------------------------------------
    NodeKindSpec(
        kind=NodeKind.MATH,
        title="Math",
        description=(
            "Arithmetic on two numbers. Int works in whole numbers, float keeps the "
            "fraction; text on a pin gives up its first number."
        ),
        category="math",
        pins=(_in("a", PinType.FLOAT), _in("b", PinType.FLOAT), _out("result", PinType.FLOAT)),
        fields=(
            _op(("add", "subtract", "multiply", "divide", "modulo", "power", "min", "max")),
            FieldSpec(
                name="type",
                kind="select",
                label="Type",
                default="float",
                options=("float", "int"),
                help="Int turns every pin into a whole number.",
            ),
            FieldSpec(name="a", kind="number", label="A", default=0),
            FieldSpec(name="b", kind="number", label="B", default=0),
        ),
    ),
    NodeKindSpec(
        kind=NodeKind.COMPARE,
        title="Compare",
        description="Compares two values: numbers as numbers, anything else as text.",
        category="logic",
        pins=(_in("a", PinType.ANY), _in("b", PinType.ANY), _out("result", PinType.BOOL)),
        fields=(
            _op(("equal", "not_equal", "less", "less_equal", "greater", "greater_equal")),
            _text("a", "A"),
            _text("b", "B"),
        ),
    ),
    NodeKindSpec(
        kind=NodeKind.LOGIC,
        title="Logic",
        description="AND, OR or XOR of two flags, or NOT of the first one.",
        category="logic",
        pins=(_in("a", PinType.BOOL), _in("b", PinType.BOOL), _out("result", PinType.BOOL)),
        fields=(
            _op(("and", "or", "xor", "not")),
            FieldSpec(name="a", kind="bool", label="A", default=False),
            FieldSpec(name="b", kind="bool", label="B", default=False),
        ),
    ),
    NodeKindSpec(
        kind=NodeKind.SELECT,
        title="Select",
        description="One of two values, picked by a condition.",
        category="logic",
        pins=(
            _in("condition", PinType.BOOL),
            _in("if_true", PinType.ANY, "if true"),
            _in("if_false", PinType.ANY, "if false"),
            _out("value", PinType.ANY),
        ),
        fields=(
            FieldSpec(name="condition", kind="bool", label="Condition", default=False),
            _text("if_true", "If true"),
            _text("if_false", "If false"),
        ),
    ),
    NodeKindSpec(
        kind=NodeKind.APPEND,
        title="Append",
        description="Joins texts end to end. Add a pin for every extra part.",
        category="text",
        pins=(
            PinSpec(name="in_0", type=PinType.STRING, direction="in", dynamic=True),
            PinSpec(name="in_1", type=PinType.STRING, direction="in", dynamic=True),
            _out("result", PinType.STRING),
        ),
        fields=(_text("separator", "Separator", help="Put between the parts. \\n is a new line."),),
    ),
    NodeKindSpec(
        kind=NodeKind.FORMAT,
        title="Format",
        description="Fills a template: {0}, {1}... become the inputs with that number.",
        category="text",
        pins=(
            _in("template", PinType.STRING),
            PinSpec(name="arg_0", type=PinType.STRING, direction="in", label="{#}", dynamic=True),
            PinSpec(name="arg_1", type=PinType.STRING, direction="in", label="{#}", dynamic=True),
            _out("result", PinType.STRING),
        ),
        fields=(
            _text(
                "template",
                "Template",
                area=True,
                help="Write {0}, {1}... where each input should go.",
            ),
        ),
    ),
    NodeKindSpec(
        kind=NodeKind.TEXT_CASE,
        title="Text case",
        description="Upper case, lower case, trimmed or capitalized text.",
        category="text",
        pins=(_in("text", PinType.STRING), _out("result", PinType.STRING)),
        fields=(
            _op(("upper", "lower", "trim", "capitalize", "title")),
            _text("text", "Text", area=True),
        ),
    ),
    NodeKindSpec(
        kind=NodeKind.TEXT_REPLACE,
        title="Replace",
        description="Replaces every occurrence of one piece of text with another.",
        category="text",
        pins=(
            _in("text", PinType.STRING),
            _in("find", PinType.STRING),
            _in("replace_with", PinType.STRING, "with"),
            _out("result", PinType.STRING),
        ),
        fields=(
            _text("text", "Text", area=True),
            _text("find", "Find"),
            _text("replace_with", "Replace with"),
            FieldSpec(name="case_sensitive", kind="bool", label="Case sensitive", default=True),
        ),
    ),
    NodeKindSpec(
        kind=NodeKind.TEXT_CHECK,
        title="Text check",
        description="Whether text contains, starts or ends with another piece, or is empty.",
        category="text",
        pins=(
            _in("text", PinType.STRING),
            _in("part", PinType.STRING),
            _out("result", PinType.BOOL),
        ),
        fields=(
            _op(("contains", "starts_with", "ends_with", "equals", "is_empty")),
            _text("text", "Text", area=True),
            _text("part", "Part"),
            FieldSpec(name="case_sensitive", kind="bool", label="Case sensitive", default=False),
        ),
    ),
    NodeKindSpec(
        kind=NodeKind.TEXT_LENGTH,
        title="Length",
        description="How many characters a text has.",
        category="text",
        pins=(_in("text", PinType.STRING), _out("length", PinType.INT)),
        fields=(_text("text", "Text", area=True),),
    ),
    NodeKindSpec(
        kind=NodeKind.SUBSTRING,
        title="Substring",
        description="A piece of text, from a start position and for a length.",
        category="text",
        pins=(
            _in("text", PinType.STRING),
            _in("start", PinType.INT),
            _in("length", PinType.INT),
            _out("result", PinType.STRING),
        ),
        fields=(
            _text("text", "Text", area=True),
            FieldSpec(
                name="start",
                kind="number",
                label="Start",
                default=0,
                help="Negative counts from the end.",
            ),
            FieldSpec(
                name="length", kind="number", label="Length", default=0, help="0 takes the rest."
            ),
        ),
    ),
    NodeKindSpec(
        kind=NodeKind.SPLIT,
        title="Split",
        description=(
            "Cuts text at a separator into one piece per line - what a Loop's foreach "
            "walks - and counts the pieces."
        ),
        category="text",
        pins=(
            _in("text", PinType.STRING),
            _in("separator", PinType.STRING),
            _out("lines", PinType.STRING),
            _out("count", PinType.INT),
        ),
        fields=(
            _text("text", "Text", area=True),
            FieldSpec(
                name="separator",
                kind="text",
                label="Separator",
                default=",",
                help="Empty splits at spaces and new lines. \\n is a new line.",
            ),
        ),
    ),
    NodeKindSpec(
        kind=NodeKind.TO_INT,
        title="To Int",
        description="A whole number from anything; text gives up the first number in it.",
        category="convert",
        pins=(_in("value", PinType.ANY), _out("result", PinType.INT)),
        fields=(
            FieldSpec(
                name="mode",
                kind="select",
                label="Rounding",
                default="truncate",
                options=("truncate", "round", "floor", "ceil"),
            ),
            _text("value", "Value"),
        ),
    ),
    NodeKindSpec(
        kind=NodeKind.TO_FLOAT,
        title="To Float",
        description="A number from anything; text gives up the first number in it.",
        category="convert",
        pins=(_in("value", PinType.ANY), _out("result", PinType.FLOAT)),
        fields=(_text("value", "Value"),),
    ),
    NodeKindSpec(
        kind=NodeKind.TO_TEXT,
        title="To Text",
        description="Any value as text.",
        category="convert",
        pins=(_in("value", PinType.ANY), _out("result", PinType.STRING)),
        fields=(_text("value", "Value"),),
    ),
    NodeKindSpec(
        kind=NodeKind.TO_BOOL,
        title="To Bool",
        description="true, yes, sim, on and anything but zero read as true.",
        category="convert",
        pins=(_in("value", PinType.ANY), _out("result", PinType.BOOL)),
        fields=(_text("value", "Value"),),
    ),
    # -- annotations --------------------------------------------------------
    NodeKindSpec(
        kind=NodeKind.COMMENT,
        title="Comment",
        description=(
            "A box that names a part of the graph. It never runs; moving it moves "
            "the nodes inside it."
        ),
        category="note",
        pins=(),
        annotation=True,
        fields=(
            FieldSpec(name="title", kind="textarea", label="Text", default="Comment"),
            FieldSpec(name="color", kind="color", label="Color", default=COMMENT_COLOR),
            FieldSpec(
                name="font_size",
                kind="number",
                label="Font size",
                default=18,
                help=f"{COMMENT_FONT_SIZES[0]} to {COMMENT_FONT_SIZES[1]}.",
            ),
            FieldSpec(
                name="move_mode",
                kind="select",
                label="Moving it",
                default="group",
                options=COMMENT_MOVE_MODES,
                help="group carries the nodes inside along; comment moves the box alone.",
            ),
            FieldSpec(name="width", kind="number", label="Width", default=400),
            FieldSpec(name="height", kind="number", label="Height", default=240),
        ),
    ),
)

NODE_KINDS: dict[str, NodeKindSpec] = {str(spec.kind): spec for spec in _KIND_LIST}


def catalog_payload(
    professions: list[dict[str, Any]] | None = None,
    *,
    tools: list[dict[str, Any]] | None = None,
    language: str = "en",
) -> dict[str, Any]:
    """What ``graphCatalog`` serves: everything the editor needs to draw a graph.

    ``language`` travels with it because the panel has no other source for it:
    English is what the source carries, and anything else is a translation the
    editor applies once it knows what the user picked.
    """

    return {
        "schema_version": SCHEMA_VERSION,
        "language": language or "en",
        "node_kinds": [spec.payload() for spec in _KIND_LIST],
        "pin_colors": {str(key): value for key, value in PIN_COLORS.items()},
        "variable_types": [str(item) for item in VARIABLE_TYPES],
        "professions": professions or [],
        "permissions": [
            {"field": field, "capability": capability, "label": label}
            for field, capability, label in PERMISSIONS
        ],
        "tools": tools or [],
        "tool_groups": [{"name": name, "title": title} for name, title in TOOL_GROUPS],
        "limits": {"nodes": MAX_NODES, "edges": MAX_EDGES, "variables": MAX_VARIABLES},
    }


# -- the document ---------------------------------------------------------


class VariableSpec(BaseModel):
    """One user-declared variable, scoped to a run."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    type: str = PinType.STRING
    default: Any = None

    @field_validator("type")
    @classmethod
    def _known_type(cls, value: str) -> str:
        if value not in VARIABLE_TYPES:
            raise ValueError(f"variable type must be one of {', '.join(VARIABLE_TYPES)}")
        return value


class NodeSpec(BaseModel):
    """One node, with its kind-specific fields left as an open mapping.

    ``data`` is validated by the compiler rather than here: the compiler can
    report *which* field of *which* node is wrong, which is what the editor
    needs to paint a marker, and a pydantic union would only tell it that the
    node did not match any variant.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: str
    pos: tuple[int, int] = (0, 0)
    data: dict[str, Any] = Field(default_factory=dict)


class EdgeSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str
    kind: str = PinType.EXEC
    # ``from`` is a keyword, so the field is ``source`` on this side of the wire
    # and ``from`` on the JSON side.
    source: tuple[str, str] = Field(alias="from")
    target: tuple[str, str] = Field(alias="to")

    @field_validator("kind")
    @classmethod
    def _known_kind(cls, value: str) -> str:
        if value not in ("exec", "data"):
            raise ValueError("edge kind must be 'exec' or 'data'")
        return value


class ViewSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pan: tuple[float, float] = (0.0, 0.0)
    zoom: float = 1.0


class GraphDoc(BaseModel):
    """A `*.flow.json` file, parsed but not yet validated as a program."""

    model_config = ConfigDict(extra="forbid")

    version: int = SCHEMA_VERSION
    name: str = ""
    variables: list[VariableSpec] = Field(default_factory=list)
    nodes: list[NodeSpec] = Field(default_factory=list)
    edges: list[EdgeSpec] = Field(default_factory=list)
    view: ViewSpec = Field(default_factory=ViewSpec)

    def node(self, node_id: str) -> NodeSpec | None:
        for item in self.nodes:
            if item.id == node_id:
                return item
        return None


@dataclass(slots=True)
class GraphScaffold:
    """The starting graph ``code-ai.newFlow`` writes, as a plain mapping."""

    name: str = "new-flow"

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": SCHEMA_VERSION,
            "name": self.name,
            "variables": [],
            "nodes": [
                {"id": "n1", "kind": "start", "pos": [-160, 0], "data": {}},
                {"id": "n2", "kind": "end", "pos": [240, 0], "data": {}},
            ],
            "edges": [{"id": "e1", "kind": "exec", "from": ["n1", "out"], "to": ["n2", "in"]}],
            "view": {"pan": [0, 0], "zoom": 1},
        }


# Key order used when a graph is written back to disk. A node nudged four pixels
# must not reorder the file, or every save becomes an unreadable diff.
DOC_KEY_ORDER: tuple[str, ...] = ("version", "name", "variables", "nodes", "edges", "view")
NODE_KEY_ORDER: tuple[str, ...] = ("id", "kind", "pos", "data")
EDGE_KEY_ORDER: tuple[str, ...] = ("id", "kind", "from", "to")
VARIABLE_KEY_ORDER: tuple[str, ...] = ("id", "name", "type", "default")
