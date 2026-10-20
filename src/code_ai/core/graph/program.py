"""The execution-ready form of a graph.

A :class:`GraphProgram` is what the interpreter walks: every lookup it needs
during a run is already resolved into a dict, so the hot path never scans the
edge list. Producing one is the compiler's job (:mod:`code_ai.core.graph.compile`);
a program only exists if the document validated, which is why nothing here
returns an error.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from code_ai.core.graph.models import NodeKindSpec, PinType, VariableSpec


@dataclass(frozen=True, slots=True)
class NodeProgram:
    """One node with its wiring resolved.

    Exec outputs are 1:1 (an exec pin drives one wire, as in a Blueprint), while
    data inputs are 1:1 in the other direction: a value has one producer, and a
    producer may feed many consumers.
    """

    id: str
    kind: str
    spec: NodeKindSpec
    data: dict[str, Any]
    label: str
    # exec out pin -> (target node id, edge id)
    exec_out: dict[str, tuple[str, str]] = field(default_factory=dict)
    # data in pin -> (source node id, source pin, edge id)
    data_in: dict[str, tuple[str, str, str]] = field(default_factory=dict)
    # A multi input's wires, in file order. Such a pin never appears in data_in.
    multi_in: dict[str, tuple[tuple[str, str, str], ...]] = field(default_factory=dict)
    # Ordered exec out pins, for the kinds where order is semantic (sequence).
    ordered_exec: tuple[str, ...] = ()

    def literal(self, pin: str) -> Any:
        """The inline default for a data pin, as the inspector stored it."""

        return self.data.get(pin)

    def sources(self) -> list[tuple[str, str, str]]:
        """Every wire feeding this node, single and multi inputs alike."""

        wires = list(self.data_in.values())
        for many in self.multi_in.values():
            wires.extend(many)
        return wires

    def target(self, pin: str) -> str | None:
        wired = self.exec_out.get(pin)
        return wired[0] if wired else None

    def edge_for(self, pin: str) -> str:
        wired = self.exec_out.get(pin)
        return wired[1] if wired else ""


@dataclass(frozen=True, slots=True)
class FunctionProgram:
    """A function's graph, as a Call node needs it: where to go in, and what it takes and gives."""

    id: str
    name: str
    entry_id: str
    # Parameter ids, which are also the pin names on its Entry, Return and Calls.
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    node_ids: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class GraphProgram:
    """A validated graph, ready to run."""

    name: str
    start_id: str
    nodes: dict[str, NodeProgram]
    # Declared variables by name, carrying their type and default.
    variables: dict[str, VariableSpec]
    # Resolved output type per pure node pin, so a var_get reports the declared
    # type of the variable it reads rather than "any".
    pin_types: dict[tuple[str, str], str] = field(default_factory=dict)
    functions: dict[str, FunctionProgram] = field(default_factory=dict)

    def node(self, node_id: str) -> NodeProgram | None:
        return self.nodes.get(node_id)

    def pin_type(self, node_id: str, pin: str) -> str:
        return self.pin_types.get((node_id, pin), PinType.ANY)

    def kinds_used(self) -> set[str]:
        return {node.kind for node in self.nodes.values()}

    def initial_variables(self) -> dict[str, Any]:
        return {name: spec.default for name, spec in self.variables.items()}
