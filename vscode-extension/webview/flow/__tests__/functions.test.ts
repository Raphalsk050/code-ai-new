import assert from "node:assert/strict";
import test from "node:test";

import catalogFixture from "../../../e2e/fixtures/catalog.json";
import type { FlowGraph, GraphCatalog } from "../../../src/flow-schema";
import { indexCatalog, layoutNode, pinsOf, summaryOf } from "../model";
import { Action, EditorState, initialState, reduce } from "../store";

const RAW = catalogFixture as unknown as GraphCatalog;
const CATALOG = indexCatalog(RAW);

function node(id: string, kind: string, data: Record<string, unknown> = {}) {
  return { id, kind, pos: [0, 0] as [number, number], data };
}

function graph(nodes: FlowGraph["nodes"], edges: FlowGraph["edges"] = []): FlowGraph {
  return { version: 1, name: "t", variables: [], nodes, edges, view: { pan: [0, 0], zoom: 1 } };
}

function booted(doc: FlowGraph): EditorState {
  let state = reduce(initialState(), { type: "catalog", catalog: RAW });
  state = reduce(state, { type: "document", graph: doc, fileName: "t.flow.json" });
  return state;
}

function apply(state: EditorState, ...actions: Action[]): EditorState {
  return actions.reduce(reduce, state);
}

function spec(kind: string) {
  const found = CATALOG.kinds.get(kind);
  assert.ok(found, `no ${kind} in the catalog`);
  return found;
}

test("every function node the backend serves is in the catalog", () => {
  for (const kind of ["math", "compare", "logic", "select", "append", "format", "split", "to_int"]) {
    assert.ok(CATALOG.kinds.get(kind), kind);
  }
});

test("an Append shows as many inputs as it asked for", () => {
  const pins = pinsOf(spec("append"), node("a", "append", { inputs: 3 }));

  assert.deepEqual(
    pins.filter((pin) => pin.direction === "in").map((pin) => pin.name),
    ["in_0", "in_1", "in_2"]
  );
});

test("a Format labels its inputs after the placeholder they fill", () => {
  const labels = pinsOf(spec("format"), node("f", "format", { inputs: 2 }))
    .filter((pin) => pin.name.startsWith("arg_"))
    .map((pin) => pin.label);

  assert.deepEqual(labels, ["{0}", "{1}"]);
});

test("an int Math node has int pins, a float one float pins", () => {
  const types = (data: Record<string, unknown>) =>
    pinsOf(spec("math"), node("m", "math", data)).map((pin) => pin.type);

  assert.deepEqual(types({ type: "int" }), ["int", "int", "int"]);
  assert.deepEqual(types({}), ["float", "float", "float"]);
});

test("an int Math result may feed a Loop count, a float one may not", () => {
  let state = booted(graph([node("m", "math", { type: "int" }), node("l", "loop")]));
  state = apply(state, {
    type: "connect",
    edge: { kind: "data", from: ["m", "result"], to: ["l", "count"] },
  });
  assert.equal(state.graph.edges.length, 1);

  state = booted(graph([node("m", "math", { type: "float" }), node("l", "loop")]));
  state = apply(state, {
    type: "connect",
    edge: { kind: "data", from: ["m", "result"], to: ["l", "count"] },
  });
  assert.equal(state.graph.edges.length, 0);
  assert.ok(state.notice);
});

test("a new Append starts with two inputs and shrinking drops the wire on the lost one", () => {
  let state = booted(graph([node("p", "print")]));
  state = apply(state, { type: "addNode", kind: "append", at: { x: 0, y: 0 } });
  const id = state.selection[0];
  assert.equal(state.graph.nodes.find((item) => item.id === id)?.data.inputs, 2);

  state = apply(
    state,
    { type: "setNodeData", id, key: "inputs", value: 3 },
    { type: "connect", edge: { kind: "data", from: [id, "result"], to: ["p", "value"] } }
  );
  const feeder = graph([node("v", "var_get", { variable: "" })]).nodes[0];
  state = apply(state, { type: "paste", nodes: [feeder], edges: [] });
  state = apply(state, {
    type: "connect",
    edge: { kind: "data", from: ["v", "value"], to: [id, "in_2"] },
  });
  assert.equal(state.graph.edges.length, 2);

  state = apply(state, { type: "setNodeData", id, key: "inputs", value: 2 });
  assert.equal(state.graph.edges.length, 1);
  assert.deepEqual(state.graph.edges[0].to, ["p", "value"]);
});

test("a function node says what it computes, and grows no footer", () => {
  const math = node("m", "math", { op: "multiply", type: "int" });

  assert.equal(summaryOf(math, spec("math")), "a × b · int");
  assert.equal(summaryOf(node("c", "compare", { op: "less_equal" }), spec("compare")), "a <= b");
  assert.equal(layoutNode(math, spec("math")).footer, 0);
});
