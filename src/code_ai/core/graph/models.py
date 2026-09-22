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


class NodeKind(StrEnum):
    START = "start"
    END = "end"
    MODEL = "model"
    PROFESSION = "profession"
    SEQUENCE = "sequence"
    DECISION = "decision"
    LOOP = "loop"
    USER_CHAT = "user_chat"
    VAR_SET = "var_set"
    VAR_GET = "var_get"
    PRINT = "print"


class PinType(StrEnum):
    EXEC = "exec"
    STRING = "string"
    INT = "int"
    FLOAT = "float"
    BOOL = "bool"
    MODEL = "model"
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
    PinType.ANY: "#A0A0A0",
}

# The types a user-declared variable may have. Deliberately scalar: a graph
# variable is a knob, not a data structure.
VARIABLE_TYPES: tuple[str, ...] = (PinType.STRING, PinType.INT, PinType.FLOAT, PinType.BOOL)


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
    # Sequence's "+ Add pin": the editor may add/remove numbered copies.
    dynamic: bool = False

    def payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": str(self.type),
            "direction": self.direction,
            "label": self.label or self.name,
            "required": self.required,
            "dynamic": self.dynamic,
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
    kind: str  # text | textarea | number | select | bool | variable | profession | model
    label: str
    default: Any = None
    options: tuple[str, ...] = ()
    help: str = ""

    def payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "label": self.label,
            "default": self.default,
            "options": list(self.options),
            "help": self.help,
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

    @property
    def pure(self) -> bool:
        return not any(pin.type == PinType.EXEC for pin in self.pins)

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
            "pins": [pin.payload() for pin in self.pins],
            "fields": [item.payload() for item in self.fields],
        }


def _exec_in(label: str = "") -> PinSpec:
    return PinSpec(name="in", type=PinType.EXEC, direction="in", label=label)


def _exec_out(name: str = "out", label: str = "", dynamic: bool = False) -> PinSpec:
    return PinSpec(name=name, type=PinType.EXEC, direction="out", label=label, dynamic=dynamic)


_KIND_LIST: tuple[NodeKindSpec, ...] = (
    NodeKindSpec(
        kind=NodeKind.START,
        title="Start",
        description="Onde a linha principal começa. Exatamente um por grafo.",
        category="flow",
        pins=(_exec_out(),),
        max_instances=1,
    ),
    NodeKindSpec(
        kind=NodeKind.END,
        title="End",
        description="Encerra a execução. O que chegar em `result` é a resposta dela.",
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
            "Um endpoint e um nome de modelo. Ligue nos agentes que devem rodar "
            "nele; agente sem modelo ligado usa o da sessão."
        ),
        category="data",
        pins=(PinSpec(name="model", type=PinType.MODEL, direction="out"),),
        fields=(
            FieldSpec(
                name="api_mode",
                kind="select",
                label="Modo da API",
                default="",
                options=("", "responses", "completions", "ollama"),
                help="Vazio segue a sessão.",
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
                label="Chave da API",
                default="session",
                help=(
                    "session (usa a chave da sessão), env:NOME ou secret:NOME. "
                    "Chave literal é recusada - este arquivo vai para o git."
                ),
            ),
            FieldSpec(name="model", kind="model", label="Modelo", default=""),
            FieldSpec(name="temperature", kind="number", label="Temperatura", default=None),
            FieldSpec(name="top_p", kind="number", label="Top P", default=None),
            FieldSpec(name="top_k", kind="number", label="Top K", default=None),
        ),
    ),
    NodeKindSpec(
        kind=NodeKind.PROFESSION,
        title="Agent",
        description=(
            "Um agente isolado, contratado para uma profissão, que faz uma tarefa "
            "e devolve um relatório. A profissão decide em que ferramentas ele "
            "pode mexer."
        ),
        category="agent",
        pins=(
            _exec_in(),
            _exec_out(),
            PinSpec(name="model", type=PinType.MODEL, direction="in"),
            PinSpec(name="task", type=PinType.STRING, direction="in", required=True),
            PinSpec(name="report", type=PinType.STRING, direction="out"),
        ),
        fields=(
            FieldSpec(name="profession", kind="profession", label="Profissão", default=""),
            FieldSpec(name="task", kind="textarea", label="Tarefa", default=""),
            FieldSpec(
                name="role_override",
                kind="textarea",
                label="Instruções extras",
                default="",
                help="Entram junto com o papel da profissão.",
            ),
            FieldSpec(
                name="max_steps",
                kind="number",
                label="Passos no máximo",
                default=0,
                help="0 mantém o orçamento da profissão.",
            ),
            FieldSpec(
                name="timeout_s",
                kind="number",
                label="Tempo limite (s)",
                default=0,
                help="0 mantém o orçamento da profissão.",
            ),
        ),
    ),
    NodeKindSpec(
        kind=NodeKind.SEQUENCE,
        title="Sequence",
        description=(
            "Dispara cada saída em ordem, esperando um ramo terminar antes "
            "de começar o próximo."
        ),
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
            "Dois caminhos. `bool` lê uma condição ligada; `judge` faz uma "
            "pergunta de sim/não ao modelo sobre o que aconteceu até aqui."
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
                label="Modo",
                default="bool",
                options=("bool", "judge"),
            ),
            FieldSpec(name="condition", kind="bool", label="Condição", default=False),
            FieldSpec(
                name="prompt",
                kind="textarea",
                label="Pergunta",
                default="",
                help="Deve dar para responder com sim ou não.",
            ),
        ),
    ),
    NodeKindSpec(
        kind=NodeKind.LOOP,
        title="Loop",
        description=(
            "Repete `body` e depois segue por `completed`. Sempre limitado: o teto "
            "de voltas vale para qualquer tipo."
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
                label="Tipo",
                default="count",
                options=("count", "while", "foreach"),
                help="foreach anda uma linha de `items` por volta.",
            ),
            FieldSpec(name="count", kind="number", label="Voltas", default=3),
            FieldSpec(
                name="max_iterations",
                kind="number",
                label="Voltas no máximo",
                default=0,
                help="0 usa o teto da configuração.",
            ),
        ),
    ),
    NodeKindSpec(
        kind=NodeKind.USER_CHAT,
        title="Ask the user",
        description="Pausa a execução até o usuário responder no próprio nó.",
        category="agent",
        pins=(
            _exec_in(),
            _exec_out(),
            PinSpec(name="prompt", type=PinType.STRING, direction="in"),
            PinSpec(name="answer", type=PinType.STRING, direction="out"),
        ),
        fields=(
            FieldSpec(name="title", kind="text", label="Título", default=""),
            FieldSpec(name="prompt", kind="textarea", label="Pergunta", default=""),
        ),
    ),
    NodeKindSpec(
        kind=NodeKind.VAR_SET,
        title="Set variable",
        description="Grava uma variável do grafo.",
        category="data",
        pins=(
            _exec_in(),
            _exec_out(),
            PinSpec(name="value", type=PinType.ANY, direction="in", required=True),
        ),
        fields=(
            FieldSpec(name="variable", kind="variable", label="Variável", default=""),
            FieldSpec(name="value", kind="text", label="Valor", default=""),
        ),
    ),
    NodeKindSpec(
        kind=NodeKind.VAR_GET,
        title="Get variable",
        description="Lê uma variável do grafo.",
        category="data",
        pins=(PinSpec(name="value", type=PinType.ANY, direction="out"),),
        fields=(FieldSpec(name="variable", kind="variable", label="Variable", default=""),),
    ),
    NodeKindSpec(
        kind=NodeKind.PRINT,
        title="Print",
        description="Escreve um valor no log da execução. O melhor amigo de quem depura.",
        category="debug",
        pins=(
            _exec_in(),
            _exec_out(),
            PinSpec(name="value", type=PinType.ANY, direction="in"),
        ),
        fields=(
            FieldSpec(name="label", kind="text", label="Rótulo", default=""),
            FieldSpec(name="value", kind="textarea", label="Valor", default=""),
        ),
    ),
)

NODE_KINDS: dict[str, NodeKindSpec] = {str(spec.kind): spec for spec in _KIND_LIST}


def catalog_payload(
    professions: list[dict[str, Any]] | None = None, *, language: str = "en"
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
