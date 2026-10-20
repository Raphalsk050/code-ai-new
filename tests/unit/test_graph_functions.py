"""Functions, Blueprint-style: a graph of its own, entered at its Entry, left at a
Return, and run by Call nodes from the main graph or from another function."""

from __future__ import annotations

from typing import Any

from code_ai.core.graph.compile import compile_graph
from code_ai.core.graph.models import NODE_KINDS, catalog_payload, expanded_pins
from code_ai.core.graph.runtime import RunStatus
from tests.unit.test_graph_runtime import PROFESSIONS, FakeExecutor, doc, node, run, wire


def function(
    function_id: str,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    inputs: list[tuple[str, str, str]] = (),
    outputs: list[tuple[str, str, str]] = (),
    name: str = "",
) -> dict[str, Any]:
    return {
        "id": function_id,
        "name": name or function_id,
        "inputs": [{"id": pid, "name": pname, "type": ptype} for pid, pname, ptype in inputs],
        "outputs": [{"id": pid, "name": pname, "type": ptype} for pid, pname, ptype in outputs],
        "nodes": nodes,
        "edges": edges,
        "view": {"pan": [0, 0], "zoom": 1},
    }


def summarize() -> dict[str, Any]:
    """Takes a topic, has an agent write about it, hands the report back."""

    return function(
        "f1",
        [
            node("f1.entry", "function_entry"),
            node("f1.agent", "profession", profession="architect"),
            node("f1.return", "function_return"),
        ],
        [
            wire("f1.e1", ("f1.entry", "out"), ("f1.agent", "in")),
            wire("f1.e2", ("f1.entry", "p1"), ("f1.agent", "task"), "data"),
            wire("f1.e3", ("f1.agent", "out"), ("f1.return", "in")),
            wire("f1.e4", ("f1.agent", "report"), ("f1.return", "p2"), "data"),
        ],
        inputs=[("p1", "topic", "string")],
        outputs=[("p2", "summary", "string")],
        name="Summarize",
    )


def calling(*calls: dict[str, Any], functions: list[dict[str, Any]]) -> dict[str, Any]:
    """Start -> each call in turn, each followed by a Print of its first output -> End."""

    nodes = [node("start", "start")]
    edges = []
    previous = ("start", "out")
    for index, call in enumerate(calls):
        show = f"show{index}"
        nodes += [call, node(show, "print")]
        edges += [
            wire(f"c{index}", previous, (call["id"], "in")),
            wire(f"s{index}", (call["id"], "out"), (show, "in")),
            wire(f"v{index}", (call["id"], "p2"), (show, "value"), "data"),
        ]
        previous = (show, "out")
    nodes.append(node("end", "end"))
    edges.append(wire("last", previous, ("end", "in")))
    return doc(nodes, edges, functions=functions)


async def test_a_call_runs_its_function_with_what_it_is_given_and_gets_the_result_back() -> None:
    call = node("call", "call_function", function="f1", p1="the importer")
    graph = calling(call, functions=[summarize()])
    executor = FakeExecutor(reports={"f1.agent": lambda task: f"about {task}"})

    outcome, recorder, _ = await run(graph, executor)

    assert outcome.status is RunStatus.COMPLETED
    assert recorder.node_order() == [
        *("start", "call", "f1.entry", "f1.agent", "f1.return"),
        *("show0", "end"),
    ]
    assert executor.agent_calls[0]["task"] == "the importer"
    assert recorder.printed() == ["about the importer"]


async def test_each_call_gets_its_own_values() -> None:
    graph = calling(
        node("a", "call_function", function="f1", p1="one"),
        node("b", "call_function", function="f1", p1="two"),
        functions=[summarize()],
    )
    executor = FakeExecutor(reports={"f1.agent": lambda task: f"about {task}"})

    outcome, recorder, _ = await run(graph, executor)

    assert outcome.status is RunStatus.COMPLETED
    assert recorder.printed() == ["about one", "about two"]


async def test_a_return_leaves_the_function_on_the_spot() -> None:
    body = function(
        "f1",
        [
            node("f1.entry", "function_entry"),
            node("f1.seq", "sequence", outputs=2),
            node("f1.return", "function_return", p2="early"),
            node("f1.never", "print", value="never"),
        ],
        [
            wire("f1.e1", ("f1.entry", "out"), ("f1.seq", "in")),
            wire("f1.e2", ("f1.seq", "out_0"), ("f1.return", "in")),
            wire("f1.e3", ("f1.seq", "out_1"), ("f1.never", "in")),
        ],
        outputs=[("p2", "summary", "string")],
    )
    graph = calling(node("call", "call_function", function="f1"), functions=[body])

    outcome, recorder, _ = await run(graph)

    assert outcome.status is RunStatus.COMPLETED
    assert "f1.never" not in recorder.node_order()
    assert recorder.printed() == ["early"]


async def test_a_function_may_call_another() -> None:
    outer = function(
        "f2",
        [
            node("f2.entry", "function_entry"),
            node("f2.call", "call_function", function="f1"),
            node("f2.return", "function_return"),
        ],
        [
            wire("f2.e1", ("f2.entry", "out"), ("f2.call", "in")),
            wire("f2.e2", ("f2.entry", "p1"), ("f2.call", "p1"), "data"),
            wire("f2.e3", ("f2.call", "out"), ("f2.return", "in")),
            wire("f2.e4", ("f2.call", "p2"), ("f2.return", "p2"), "data"),
        ],
        inputs=[("p1", "topic", "string")],
        outputs=[("p2", "summary", "string")],
    )
    graph = calling(
        node("call", "call_function", function="f2", p1="nesting"), functions=[summarize(), outer]
    )
    executor = FakeExecutor(reports={"f1.agent": lambda task: f"about {task}"})

    outcome, recorder, _ = await run(graph, executor)

    assert outcome.status is RunStatus.COMPLETED
    assert recorder.printed() == ["about nesting"]


async def test_a_function_changes_the_graphs_variables() -> None:
    body = function(
        "f1",
        [
            node("f1.entry", "function_entry"),
            node("f1.set", "var_set", variable="count", value=7),
            node("f1.return", "function_return"),
        ],
        [
            wire("f1.e1", ("f1.entry", "out"), ("f1.set", "in")),
            wire("f1.e2", ("f1.set", "out"), ("f1.return", "in")),
        ],
    )
    graph = doc(
        [node("start", "start"), node("call", "call_function", function="f1"), node("end", "end")],
        [wire("e1", ("start", "out"), ("call", "in")), wire("e2", ("call", "out"), ("end", "in"))],
        functions=[body],
        variables=[{"id": "v1", "name": "count", "type": "int", "default": 0}],
    )

    outcome, _, _ = await run(graph)

    assert outcome.variables["count"] == 7


async def test_an_input_left_unwired_reads_the_calls_own_value_in_its_type() -> None:
    body = function(
        "f1",
        [node("f1.entry", "function_entry"), node("f1.return", "function_return")],
        [
            wire("f1.e1", ("f1.entry", "out"), ("f1.return", "in")),
            wire("f1.e2", ("f1.entry", "p1"), ("f1.return", "p2"), "data"),
        ],
        inputs=[("p1", "n", "int")],
        outputs=[("p2", "n", "int")],
    )
    graph = calling(node("call", "call_function", function="f1", p1="41"), functions=[body])

    _, recorder, interpreter = await run(graph)

    assert recorder.printed() == ["41"]
    assert interpreter._outputs[("call", "p2")] == 41


# -- what the compiler refuses ---------------------------------------------


def problems(graph: dict[str, Any]) -> tuple[Any, list[str], list[str]]:
    program, issues = compile_graph(graph, professions=PROFESSIONS)
    errors = [item.message for item in issues if item.severity == "error"]
    warnings = [item.message for item in issues if item.severity == "warning"]
    return program, errors, warnings


def test_a_function_that_calls_itself_is_refused() -> None:
    body = function(
        "f1",
        [
            node("f1.entry", "function_entry"),
            node("f1.again", "call_function", function="f1"),
        ],
        [wire("f1.e1", ("f1.entry", "out"), ("f1.again", "in"))],
    )
    graph = calling(node("call", "call_function", function="f1"), functions=[body])

    program, errors, _ = problems(graph)

    assert program is None
    assert any("cannot call itself" in message for message in errors)


def test_a_wire_between_two_graphs_is_refused() -> None:
    body = summarize()
    graph = calling(node("call", "call_function", function="f1"), functions=[body])
    graph["edges"].append(wire("x", ("f1.agent", "report"), ("end", "result"), "data"))

    program, errors, _ = problems(graph)

    assert program is None
    assert any("cross from one graph into another" in message for message in errors)


def test_start_and_end_stay_in_the_main_graph_and_entry_and_return_in_functions() -> None:
    body = summarize()
    # The agent's flow goes to an End instead of the Return.
    body["nodes"].append(node("f1.end", "end"))
    body["edges"] = [edge for edge in body["edges"] if edge["id"] != "f1.e3"]
    body["edges"].append(wire("f1.x", ("f1.agent", "out"), ("f1.end", "in")))
    graph = calling(node("call", "call_function", function="f1"), functions=[body])
    graph["nodes"].append(node("stray", "function_return"))
    graph["edges"].append(wire("y", ("show0", "out"), ("stray", "in")))
    graph["edges"] = [edge for edge in graph["edges"] if edge["id"] != "last"]

    program, errors, _ = problems(graph)

    assert program is None
    assert any("belongs to the main graph" in message for message in errors)
    assert any("belongs inside a function" in message for message in errors)


def test_a_function_nobody_calls_only_warns_even_when_broken() -> None:
    body = summarize()
    body["nodes"][1]["data"]["profession"] = "no-such-profession"
    graph = doc(
        [node("start", "start"), node("end", "end")],
        [wire("e1", ("start", "out"), ("end", "in"))],
        functions=[body],
    )

    program, errors, warnings = problems(graph)

    assert program is not None, errors
    assert "Nothing calls this function." in warnings


def test_a_broken_node_in_a_called_function_blocks_the_run() -> None:
    body = summarize()
    body["nodes"][1]["data"]["profession"] = "no-such-profession"
    graph = calling(node("call", "call_function", function="f1"), functions=[body])

    program, errors, _ = problems(graph)

    assert program is None
    assert errors


def test_a_call_to_nothing_asks_for_its_function() -> None:
    graph = calling(node("call", "call_function"), functions=[])
    graph["edges"] = [edge for edge in graph["edges"] if edge["id"] != "v0"]

    program, errors, _ = problems(graph)

    assert program is None
    assert "Pick the function this node calls." in errors


# -- pins ------------------------------------------------------------------


def test_a_signature_puts_its_pins_on_the_entry_the_return_and_the_call() -> None:
    signature = {
        "inputs": [{"id": "p1", "name": "topic", "type": "string"}],
        "outputs": [{"id": "p2", "name": "summary", "type": "string"}],
    }

    def pins(kind: str) -> list[tuple[str, str, str]]:
        spec = NODE_KINDS[kind]
        return [
            (pin.name, pin.direction, pin.label)
            for pin in expanded_pins(spec, {"signature": signature})
            if pin.type != "exec"
        ]

    assert pins("function_entry") == [("p1", "out", "topic")]
    assert pins("function_return") == [("p2", "in", "summary")]
    assert pins("call_function") == [("p1", "in", "topic"), ("p2", "out", "summary")]


def test_the_catalog_offers_the_function_kinds_and_parameter_types() -> None:
    catalog = catalog_payload()
    kinds = {row["kind"]: row for row in catalog["node_kinds"]}

    assert kinds["call_function"]["signature"] == "call"
    assert "model" in catalog["param_types"] and "exec" not in catalog["param_types"]
