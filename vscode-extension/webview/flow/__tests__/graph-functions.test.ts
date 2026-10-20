import assert from "node:assert/strict";
import test from "node:test";

import catalogFixture from "../../../e2e/fixtures/catalog.json";
import type { FlowGraph, GraphCatalog } from "../../../src/flow-schema";
import { parseFlow, serializeFlow } from "../../../src/flow-schema";
import { pinsOf } from "../model";
import { Action, contentKey, documentOf, EditorState, initialState, reduce } from "../store";

const CATALOG = catalogFixture as unknown as GraphCatalog;

function booted(): EditorState {
  let state = reduce(initialState(), { type: "catalog", catalog: CATALOG });
  const doc: FlowGraph = {
    version: 1,
    name: "t",
    variables: [],
    nodes: [
      { id: "n1", kind: "start", pos: [0, 0], data: {} },
      { id: "n2", kind: "end", pos: [400, 0], data: {} },
    ],
    edges: [{ id: "e1", kind: "exec", from: ["n1", "out"], to: ["n2", "in"] }],
    view: { pan: [0, 0], zoom: 1 },
  };
  state = reduce(state, { type: "document", graph: doc, fileName: "t.flow.json" });
  return state;
}

function apply(state: EditorState, ...actions: Action[]): EditorState {
  return actions.reduce(reduce, state);
}

/** A function "Summarize" taking one input, called once from the main graph. */
function withCall(): EditorState {
  let state = apply(booted(), { type: "addFunction", name: "Summarize" });
  state = apply(state, {
    type: "setParams",
    id: "f1",
    side: "inputs",
    params: [{ id: "p1", name: "topic", type: "string" }],
  });
  state = apply(state, { type: "openScope", scope: null });
  state = apply(state, { type: "addNode", kind: "call_function", at: { x: 200, y: 200 }, data: { function: "f1" } });
  return state;
}

test("a new function opens on the canvas with its Entry wired into a Return", () => {
  const state = apply(booted(), { type: "addFunction", name: "Summarize" });
  assert.equal(state.scope, "f1");
  assert.deepEqual(
    state.graph.nodes.map((node) => node.kind),
    ["function_entry", "function_return"]
  );
  assert.equal(state.graph.edges.length, 1);
  // The main graph is still the document's.
  assert.deepEqual(state.doc.nodes.map((node) => node.id), ["n1", "n2"]);
  assert.equal(state.doc.functions?.[0].name, "Summarize");
});

test("what is drawn in a function goes into it, under ids no other graph has", () => {
  let state = apply(booted(), { type: "addFunction" });
  state = apply(state, { type: "addNode", kind: "print", at: { x: 0, y: 200 } });
  const made = state.graph.nodes[state.graph.nodes.length - 1];
  assert.match(made.id, /^f1\.n\d+$/);
  assert.ok(state.doc.functions?.[0].nodes.some((node) => node.id === made.id));
  assert.ok(!state.doc.nodes.some((node) => node.id === made.id));
});

test("a call carries its function's signature, and its pins follow it", () => {
  let state = withCall();
  const call = () => state.graph.nodes.find((node) => node.kind === "call_function")!;
  const pins = () =>
    pinsOf(state.catalog!.kinds.get("call_function")!, call())
      .filter((pin) => pin.type !== "exec")
      .map((pin) => `${pin.direction}:${pin.label}:${pin.type}`);
  assert.deepEqual(pins(), ["in:topic:string"]);

  state = apply(state, {
    type: "setParams",
    id: "f1",
    side: "outputs",
    params: [{ id: "p2", name: "summary", type: "string" }],
  });
  assert.deepEqual(pins(), ["in:topic:string", "out:summary:string"]);
  assert.equal((call().data.signature as { name: string }).name, "Summarize");
});

test("a parameter taken away takes its wires with it", () => {
  let state = withCall();
  state = apply(state, { type: "addNode", kind: "var_get", at: { x: 0, y: 300 }, data: { variable: "" } });
  const source = state.graph.nodes[state.graph.nodes.length - 1].id;
  const call = state.graph.nodes.find((node) => node.kind === "call_function")!.id;
  state = apply(state, { type: "connect", edge: { kind: "data", from: [source, "value"], to: [call, "p1"] } });
  assert.ok(state.graph.edges.some((edge) => edge.to[1] === "p1"));

  state = apply(state, { type: "setParams", id: "f1", side: "inputs", params: [] });
  assert.ok(!state.graph.edges.some((edge) => edge.to[1] === "p1"));
});

test("undo reaches across graphs: a node added in a function goes away from the main graph", () => {
  let state = apply(booted(), { type: "addFunction" });
  state = apply(state, { type: "addNode", kind: "print", at: { x: 0, y: 200 } });
  state = apply(state, { type: "openScope", scope: null });
  assert.equal(state.doc.functions?.[0].nodes.length, 3);
  state = apply(state, { type: "undo" });
  assert.equal(state.doc.functions?.[0].nodes.length, 2);
  assert.equal(state.scope, null);
});

test("undoing a function's creation while inside it lands back on the main graph", () => {
  let state = apply(booted(), { type: "addFunction" });
  state = apply(state, { type: "undo" });
  assert.equal(state.scope, null);
  assert.equal(state.doc.functions, undefined);
  assert.deepEqual(state.graph.nodes.map((node) => node.id), ["n1", "n2"]);
});

test("each graph keeps its own camera", () => {
  let state = apply(booted(), { type: "addFunction" });
  state = apply(state, { type: "setView", pan: [77, 88], zoom: 1.5 });
  state = apply(state, { type: "openScope", scope: null });
  assert.deepEqual(state.graph.view.pan, [0, 0]);
  state = apply(state, { type: "openScope", scope: "f1" });
  assert.deepEqual(state.graph.view.pan, [77, 88]);
  assert.equal(state.graph.view.zoom, 1.5);
});

test("a pan inside a function is not an edit", () => {
  let state = apply(booted(), { type: "addFunction" });
  const before = contentKey(state.doc);
  state = apply(state, { type: "setView", pan: [5, 5] });
  assert.equal(contentKey(state.doc), before);
  assert.deepEqual(documentOf(state).functions?.[0].view.pan, [5, 5]);
});

test("deleting a function deletes every call to it", () => {
  let state = withCall();
  state = apply(state, { type: "deleteFunction", id: "f1" });
  assert.equal(state.doc.functions, undefined);
  assert.ok(!state.graph.nodes.some((node) => node.kind === "call_function"));
});

test("a renamed function keeps its name unique", () => {
  let state = apply(booted(), { type: "addFunction", name: "A" }, { type: "addFunction", name: "B" });
  state = apply(state, { type: "updateFunction", id: "f2", patch: { name: "A" } });
  assert.equal(state.doc.functions?.[1].name, "B");
  assert.match(state.notice?.text ?? "", /already a function/);
});

test("functions survive the file, and a graph without any writes none", () => {
  const plain = serializeFlow(booted().doc);
  assert.ok(!plain.includes("functions"));

  const state = withCall();
  const text = serializeFlow(documentOf(state));
  const read = parseFlow(text).graph!;
  assert.equal(read.functions?.[0].name, "Summarize");
  assert.deepEqual(read.functions?.[0].inputs, [{ id: "p1", name: "topic", type: "string" }]);
  assert.ok(!text.includes('"scope"'));
  assert.equal(serializeFlow(read), text);
});

// -- collapsing a selection into a function --------------------------------

/** Start -> A -> B -> End, A reading a variable, End reading B's... nothing: B prints A's report. */
function chainWithValues(): EditorState {
  let state = reduce(initialState(), { type: "catalog", catalog: CATALOG });
  const doc: FlowGraph = {
    version: 1,
    name: "t",
    variables: [{ id: "v1", name: "topic", type: "string", default: "x" }],
    nodes: [
      { id: "n1", kind: "start", pos: [0, 0], data: {} },
      { id: "n2", kind: "var_get", pos: [100, 200], data: { variable: "topic" } },
      { id: "n3", kind: "profession", pos: [300, 0], data: { profession: "architect" } },
      { id: "n4", kind: "print", pos: [600, 0], data: {} },
      { id: "n5", kind: "print", pos: [900, 0], data: {} },
      { id: "n6", kind: "end", pos: [1200, 0], data: {} },
    ],
    edges: [
      { id: "e1", kind: "exec", from: ["n1", "out"], to: ["n3", "in"] },
      { id: "e2", kind: "data", from: ["n2", "value"], to: ["n3", "task"] },
      { id: "e3", kind: "exec", from: ["n3", "out"], to: ["n4", "in"] },
      { id: "e4", kind: "data", from: ["n3", "report"], to: ["n4", "value"] },
      { id: "e5", kind: "exec", from: ["n4", "out"], to: ["n5", "in"] },
      { id: "e6", kind: "data", from: ["n3", "report"], to: ["n5", "value"] },
      { id: "e7", kind: "exec", from: ["n5", "out"], to: ["n6", "in"] },
    ],
    view: { pan: [0, 0], zoom: 1 },
  };
  return reduce(state, { type: "document", graph: doc, fileName: "t.flow.json" });
}

const wires = (edges: { from: [string, string]; to: [string, string] }[]) =>
  edges.map((edge) => `${edge.from.join(":")}>${edge.to.join(":")}`).sort();

test("collapsing nodes moves them into a function and leaves one call in their place", () => {
  const state = apply(chainWithValues(), { type: "collapseToFunction", ids: ["n3", "n4"] });
  const made = state.doc.functions?.[0];
  assert.ok(made, state.notice?.text);
  assert.equal(made.collapsed, true);
  // What moved took the function's ids: the main graph can never make them again.
  assert.deepEqual(made.nodes.map((node) => node.id), ["f1.n1", "f1.n3", "f1.n4", "f1.n2"]);
  // In: the variable the agent read. Out: the report the second print still reads.
  assert.deepEqual(made.inputs.map((param) => [param.name, param.type]), [["task", "string"]]);
  assert.deepEqual(made.outputs.map((param) => [param.name, param.type]), [["report", "string"]]);
  assert.deepEqual(wires(made.edges), [
    "f1.n1:out>f1.n3:in",
    "f1.n1:p1>f1.n3:task",
    "f1.n3:out>f1.n4:in",
    "f1.n3:report>f1.n2:p2",
    "f1.n3:report>f1.n4:value",
    "f1.n4:out>f1.n2:in",
  ]);
  const ids = [...state.graph.edges, ...made.edges].map((edge) => edge.id);
  assert.equal(new Set(ids).size, ids.length, `edge ids repeat: ${ids}`);
  const call = state.graph.nodes.find((node) => node.kind === "call_function")!;
  assert.deepEqual(state.selection, [call.id]);
  assert.deepEqual(wires(state.graph.edges), [
    `${call.id}:out>n5:in`,
    `${call.id}:p2>n5:value`,
    `n1:out>${call.id}:in`,
    `n2:value>${call.id}:p1`,
    "n5:out>n6:in",
  ].sort());
});

test("a collapse is one undo step", () => {
  const before = chainWithValues();
  const state = apply(before, { type: "collapseToFunction", ids: ["n3", "n4"] }, { type: "undo" });
  assert.equal(state.doc.functions, undefined);
  assert.deepEqual(wires(state.graph.edges), wires(before.graph.edges));
});

test("a collapsed graph is not offered for reuse, a function is", async () => {
  const { callable } = await import("../functions");
  let state = apply(chainWithValues(), { type: "collapseToFunction", ids: ["n3", "n4"] });
  state = apply(state, { type: "addFunction", name: "Reusable" });
  assert.deepEqual(callable(state.doc, null).map((item) => item.name), ["Reusable"]);
});

test("Start and End stay where they are", () => {
  const state = apply(chainWithValues(), { type: "collapseToFunction", ids: ["n1", "n3"] });
  assert.equal(state.doc.functions, undefined);
  assert.match(state.notice?.text ?? "", /stay where they are/);
});

test("a selection the flow enters in two places cannot be one function", () => {
  let state = chainWithValues();
  // n4 now also takes the flow from a Sequence outside the selection.
  state = apply(state, { type: "addNode", kind: "sequence", at: { x: 0, y: 400 } });
  const sequence = state.graph.nodes[state.graph.nodes.length - 1].id;
  state = apply(state, { type: "connect", edge: { kind: "exec", from: [sequence, "out_0"], to: ["n4", "in"] } });
  state = apply(state, { type: "collapseToFunction", ids: ["n3", "n4"] });
  assert.equal(state.doc.functions, undefined);
  assert.match(state.notice?.text ?? "", /one way in/);
});
