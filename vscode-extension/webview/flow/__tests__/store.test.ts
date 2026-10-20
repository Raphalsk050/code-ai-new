import assert from "node:assert/strict";
import test from "node:test";

import catalogFixture from "../../../e2e/fixtures/catalog.json";
import type { FlowGraph, GraphCatalog } from "../../../src/flow-schema";
import { Action, EditorState, hasErrors, initialState, reduce, UNDO_LIMIT } from "../store";

const CATALOG = catalogFixture as unknown as GraphCatalog;

function node(id: string, kind: string, data: Record<string, unknown> = {}) {
  return { id, kind, pos: [0, 0] as [number, number], data };
}

function graph(partial: Partial<FlowGraph> = {}): FlowGraph {
  return {
    version: 1,
    name: "t",
    variables: [],
    nodes: [],
    edges: [],
    view: { pan: [0, 0], zoom: 1 },
    ...partial,
  };
}

/** A booted editor: catalog loaded, document in place. */
function booted(doc: FlowGraph = graph()): EditorState {
  let state = reduce(initialState(), { type: "catalog", catalog: CATALOG });
  state = reduce(state, { type: "document", graph: doc, fileName: "t.flow.json" });
  return state;
}

function apply(state: EditorState, ...actions: Action[]): EditorState {
  return actions.reduce(reduce, state);
}

// -- documents -----------------------------------------------------------

test("loading a document clears the history", () => {
  let state = booted(graph({ nodes: [node("n1", "start")] }));
  state = apply(state, { type: "addNode", kind: "print", at: { x: 0, y: 0 } });
  assert.equal(state.undo.length, 1);

  state = reduce(state, {
    type: "document",
    graph: graph({ nodes: [node("n9", "start")] }),
    fileName: "t.flow.json",
  });

  assert.deepEqual(state.undo, []);
  assert.equal(state.dirty, false);
  assert.equal(state.graph.nodes[0].id, "n9");
});

test("a document identical to the current one only clears the dirty flag", () => {
  const doc = graph({ nodes: [node("n1", "start")] });
  let state = booted(doc);
  state = apply(state, { type: "addNode", kind: "print", at: { x: 10, y: 10 } });
  const graphBefore = state.graph;

  // The host echoing back what we wrote must not reset the editor mid-edit.
  state = reduce(state, { type: "document", graph: state.graph, fileName: "t.flow.json" });

  assert.equal(state.graph, graphBefore);
  assert.equal(state.dirty, false);
});

// -- nodes ---------------------------------------------------------------

test("a new node gets the catalog's defaults and is selected", () => {
  const state = apply(booted(), { type: "addNode", kind: "loop", at: { x: 33, y: 47 } });
  const added = state.graph.nodes[0];

  assert.equal(added.kind, "loop");
  assert.equal(added.data.kind, "count");
  assert.equal(added.data.count, 3);
  assert.deepEqual(state.selection, [added.id]);
  // Snapped to the half-grid, so nodes line up without a tidy pass.
  assert.deepEqual(added.pos, [30, 50]);
});

test("a second Start node is refused with a notice", () => {
  let state = booted(graph({ nodes: [node("n1", "start")] }));
  state = apply(state, { type: "addNode", kind: "start", at: { x: 0, y: 0 } });

  assert.equal(state.graph.nodes.length, 1);
  assert.match(state.notice?.text ?? "", /Start/);
});

test("ids are reused once the node that held them is gone", () => {
  let state = apply(booted(), { type: "addNode", kind: "print", at: { x: 0, y: 0 } });
  const first = state.graph.nodes[0].id;
  state = apply(state, { type: "deleteSelection" });
  state = apply(state, { type: "addNode", kind: "print", at: { x: 0, y: 0 } });

  assert.equal(state.graph.nodes[0].id, first);
});

test("deleting a node takes its wires with it", () => {
  const doc = graph({
    nodes: [node("n1", "start"), node("n2", "print", { label: "x" }), node("n3", "end")],
    edges: [
      { id: "e1", kind: "exec", from: ["n1", "out"], to: ["n2", "in"] },
      { id: "e2", kind: "exec", from: ["n2", "out"], to: ["n3", "in"] },
    ],
  });
  const state = apply(booted(doc), { type: "select", ids: ["n2"] }, { type: "deleteSelection" });

  assert.deepEqual(state.graph.nodes.map((item) => item.id), ["n1", "n3"]);
  assert.deepEqual(state.graph.edges, []);
});

test("a drag is one undo entry and lands on the grid", () => {
  const doc = graph({ nodes: [node("n1", "print", { label: "x" })] });
  const state = apply(booted(doc), {
    type: "moveNodes",
    ids: ["n1"],
    dx: 63,
    dy: -24,
    commit: true,
  });

  assert.deepEqual(state.graph.nodes[0].pos, [60, -20]);
  assert.equal(state.undo.length, 1);
});

// -- connections ---------------------------------------------------------

test("an exec output drives one wire: a second connection replaces the first", () => {
  const doc = graph({
    nodes: [node("n1", "start"), node("n2", "print", { label: "a" }), node("n3", "end")],
  });
  let state = booted(doc);
  state = apply(state, {
    type: "connect",
    edge: { kind: "exec", from: ["n1", "out"], to: ["n2", "in"] },
  });
  state = apply(state, {
    type: "connect",
    edge: { kind: "exec", from: ["n1", "out"], to: ["n3", "in"] },
  });

  assert.equal(state.graph.edges.length, 1);
  assert.equal(state.graph.edges[0].to[0], "n3");
});

test("a data input takes one value: a second producer replaces the first", () => {
  const doc = graph({
    nodes: [
      node("n1", "model", { model: "a" }),
      node("n2", "model", { model: "b" }),
      node("n3", "profession", { profession: "architect", task: "x" }),
    ],
  });
  let state = booted(doc);
  state = apply(state, {
    type: "connect",
    edge: { kind: "data", from: ["n1", "model"], to: ["n3", "model"] },
  });
  state = apply(state, {
    type: "connect",
    edge: { kind: "data", from: ["n2", "model"], to: ["n3", "model"] },
  });

  assert.equal(state.graph.edges.length, 1);
  assert.equal(state.graph.edges[0].from[0], "n2");
});

test("an exec pin cannot be wired to a data pin", () => {
  const doc = graph({ nodes: [node("n1", "start"), node("n2", "print", { label: "x" })] });
  const state = apply(booted(doc), {
    type: "connect",
    edge: { kind: "exec", from: ["n1", "out"], to: ["n2", "value"] },
  });

  assert.deepEqual(state.graph.edges, []);
  assert.match(state.notice?.text ?? "", /exec pin/);
});

test("a bool value cannot feed a model pin", () => {
  const doc = graph({
    variables: [{ id: "v1", name: "flag", type: "bool", default: false }],
    nodes: [
      node("n1", "var_get", { variable: "flag" }),
      node("n2", "profession", { profession: "architect", task: "x" }),
    ],
  });
  const state = apply(booted(doc), {
    type: "connect",
    edge: { kind: "data", from: ["n1", "value"], to: ["n2", "model"] },
  });

  assert.deepEqual(state.graph.edges, []);
  assert.match(state.notice?.text ?? "", /bool/);
});

test("an int variable may feed a float pin but not the other way", () => {
  const doc = graph({
    variables: [
      { id: "v1", name: "n", type: "int", default: 1 },
      { id: "v2", name: "f", type: "float", default: 1 },
    ],
    nodes: [
      node("n1", "var_get", { variable: "n" }),
      node("n2", "var_get", { variable: "f" }),
      node("n3", "loop", { kind: "count" }),
    ],
  });
  let state = booted(doc);
  state = apply(state, {
    type: "connect",
    edge: { kind: "data", from: ["n2", "value"], to: ["n3", "count"] },
  });
  assert.deepEqual(state.graph.edges, [], "float should not feed an int pin");

  state = apply(state, {
    type: "connect",
    edge: { kind: "data", from: ["n1", "value"], to: ["n3", "count"] },
  });
  assert.equal(state.graph.edges.length, 1);
});

test("the same wire cannot be made twice", () => {
  const doc = graph({ nodes: [node("n1", "start"), node("n2", "end")] });
  let state = booted(doc);
  const edge = { kind: "exec" as const, from: ["n1", "out"] as [string, string], to: ["n2", "in"] as [string, string] };
  state = apply(state, { type: "connect", edge });
  state = apply(state, { type: "connect", edge });

  assert.equal(state.graph.edges.length, 1);
  assert.match(state.notice?.text ?? "", /already exists/);
});

// -- Unreal's link gestures: break with a click, carry with a drag ---------

function chain(): EditorState {
  return booted(
    graph({
      nodes: [node("s", "start"), node("a", "print", { label: "a" }), node("b", "print", { label: "b" }), node("e", "end")],
      edges: [
        { id: "e1", kind: "exec", from: ["s", "out"], to: ["a", "in"] },
        { id: "e2", kind: "exec", from: ["a", "out"], to: ["e", "in"] },
      ],
    })
  );
}

test("a pin's links break together, in one undo step", () => {
  let state = apply(chain(), { type: "deleteEdges", ids: ["e1", "e2"] });
  assert.deepEqual(state.graph.edges, []);
  state = apply(state, { type: "undo" });
  assert.deepEqual(state.graph.edges.map((edge) => edge.id), ["e1", "e2"]);
});

test("a wire carried by its far end lands on the pin it is dropped on", () => {
  let state = apply(chain(), { type: "rewire", ids: ["e1"], keep: "from", target: ["b", "in"] });
  assert.deepEqual(
    state.graph.edges.map((edge) => [edge.from.join(":"), edge.to.join(":")]),
    [
      ["a:out", "e:in"],
      ["s:out", "b:in"],
    ]
  );
  state = apply(state, { type: "undo" });
  assert.deepEqual(state.graph.edges.find((edge) => edge.id === "e1")?.to, ["a", "in"]);
});

test("a wire carried by its near end keeps where it goes", () => {
  const state = apply(chain(), { type: "rewire", ids: ["e2"], keep: "to", target: ["b", "out"] });
  const moved = state.graph.edges.find((edge) => edge.to[0] === "e");
  assert.deepEqual(moved?.from, ["b", "out"]);
});

test("a carried wire that does not fit stays where it was", () => {
  const before = chain();
  const state = apply(before, { type: "rewire", ids: ["e1"], keep: "from", target: ["b", "value"] });
  assert.equal(state.graph, before.graph);
  assert.match(state.notice?.text ?? "", /exec pin/);
});

test("a wire put back on its own pin changes nothing", () => {
  const before = chain();
  const state = apply(before, { type: "rewire", ids: ["e1"], keep: "from", target: ["a", "in"] });
  assert.equal(state, before);
});

test("shrinking a Sequence drops the wires on the pins it lost", () => {
  const doc = graph({
    nodes: [node("n1", "sequence", { outputs: 3 }), node("n2", "end"), node("n3", "end")],
    edges: [
      { id: "e1", kind: "exec", from: ["n1", "out_0"], to: ["n2", "in"] },
      { id: "e2", kind: "exec", from: ["n1", "out_2"], to: ["n3", "in"] },
    ],
  });
  const state = apply(booted(doc), {
    type: "setNodeData",
    id: "n1",
    key: "outputs",
    value: 2,
  });

  assert.deepEqual(state.graph.edges.map((edge) => edge.id), ["e1"]);
});

// -- variables -----------------------------------------------------------

test("each + button adds a variable of its own type with a sane default", () => {
  let state = booted();
  for (const varType of ["string", "int", "bool"]) {
    state = apply(state, { type: "addVariable", varType });
  }

  assert.deepEqual(
    state.graph.variables.map((item) => [item.type, item.default]),
    [
      ["string", ""],
      ["int", 0],
      ["bool", false],
    ]
  );
  // Distinct names, so two + clicks do not produce two "nova".
  assert.equal(new Set(state.graph.variables.map((item) => item.name)).size, 3);
});

test("renaming a variable rewrites the nodes that read it", () => {
  const doc = graph({
    variables: [{ id: "v1", name: "ticket", type: "string", default: "" }],
    nodes: [node("n1", "var_get", { variable: "ticket" }), node("n2", "var_set", { variable: "ticket" })],
  });
  const state = apply(booted(doc), {
    type: "setVariable",
    id: "v1",
    patch: { name: "chamado" },
  });

  assert.deepEqual(
    state.graph.nodes.map((item) => item.data.variable),
    ["chamado", "chamado"]
  );
});

test("deleting a variable clears the nodes that pointed at it", () => {
  const doc = graph({
    variables: [{ id: "v1", name: "ticket", type: "string", default: "" }],
    nodes: [node("n1", "var_get", { variable: "ticket" })],
  });
  const state = apply(booted(doc), { type: "deleteVariable", id: "v1" });

  assert.deepEqual(state.graph.variables, []);
  assert.equal(state.graph.nodes[0].data.variable, "");
});

// -- history -------------------------------------------------------------

test("undo restores the exact previous graph for every mutation", () => {
  const doc = graph({
    variables: [{ id: "v1", name: "x", type: "int", default: 0 }],
    nodes: [node("n1", "start"), node("n2", "print", { label: "p" })],
    edges: [{ id: "e1", kind: "exec", from: ["n1", "out"], to: ["n2", "in"] }],
  });

  const mutations: Action[] = [
    { type: "addNode", kind: "loop", at: { x: 100, y: 100 } },
    { type: "moveNodes", ids: ["n2"], dx: 40, dy: 40, commit: true },
    { type: "setNodeData", id: "n2", key: "label", value: "outro" },
    { type: "addVariable", varType: "bool" },
    { type: "setVariable", id: "v1", patch: { name: "y" } },
    { type: "deleteVariable", id: "v1" },
    { type: "deleteEdge", id: "e1" },
  ];

  for (const mutation of mutations) {
    const before = booted(doc);
    const snapshot = JSON.stringify(before.graph);
    const after = apply(before, mutation);
    assert.notEqual(JSON.stringify(after.graph), snapshot, `${mutation.type} changed nothing`);
    const undone = apply(after, { type: "undo" });
    assert.equal(JSON.stringify(undone.graph), snapshot, `undo of ${mutation.type} lost state`);
  }
});

test("redo puts back exactly what undo took away", () => {
  let state = booted(graph({ nodes: [node("n1", "start")] }));
  state = apply(state, { type: "addNode", kind: "decision", at: { x: 0, y: 0 } });
  const afterAdd = JSON.stringify(state.graph);

  state = apply(state, { type: "undo" }, { type: "redo" });

  assert.equal(JSON.stringify(state.graph), afterAdd);
});

test("a mutation after an undo drops the redo stack", () => {
  let state = booted(graph({ nodes: [node("n1", "start")] }));
  state = apply(state, { type: "addNode", kind: "print", at: { x: 0, y: 0 } });
  state = apply(state, { type: "undo" });
  assert.equal(state.redo.length, 1);

  state = apply(state, { type: "addNode", kind: "end", at: { x: 0, y: 0 } });

  assert.equal(state.redo.length, 0);
});

test("undo on a fresh document is a no-op", () => {
  const state = booted(graph({ nodes: [node("n1", "start")] }));
  assert.equal(apply(state, { type: "undo" }), state);
  assert.equal(apply(state, { type: "redo" }), state);
});

test("the history is bounded", () => {
  let state = booted();
  for (let index = 0; index < UNDO_LIMIT + 20; index += 1) {
    state = apply(state, { type: "addNode", kind: "print", at: { x: index, y: 0 } });
  }
  assert.equal(state.undo.length, UNDO_LIMIT);
});

test("panning never goes on the undo stack", () => {
  const state = apply(booted(), { type: "setView", pan: [10, 20], zoom: 1.5 });

  assert.deepEqual(state.graph.view.pan, [10, 20]);
  assert.equal(state.graph.view.zoom, 1.5);
  assert.equal(state.undo.length, 0);
});

// -- run events ----------------------------------------------------------

function runEvent(
  state: EditorState,
  eventType: string,
  payload: Record<string, unknown> = {}
): EditorState {
  return reduce(state, { type: "graphEvent", eventType, payload });
}

test("a run resets the previous run's state and log", () => {
  let state = booted();
  state = runEvent(state, "graph.print", { node_id: "n1", text: "antigo" });
  state = runEvent(state, "graph.run.started", { run_id: "r2", name: "t", node_count: 2 });

  assert.equal(state.log.length, 0);
  assert.equal(state.run.running, true);
  assert.equal(state.run.runId, "r2");
});

test("node status follows started, completed and failed", () => {
  let state = booted();
  state = runEvent(state, "graph.node.started", { node_id: "n1", index: 1 });
  assert.equal(state.run.nodes.n1, "running");

  state = runEvent(state, "graph.node.completed", { node_id: "n1" });
  assert.equal(state.run.nodes.n1, "done");

  state = runEvent(state, "graph.node.failed", { node_id: "n2", error: "estourou" });
  assert.equal(state.run.nodes.n2, "failed");
  assert.match(state.log.at(-1)?.text ?? "", /estourou/);
});

test("an active edge is remembered as travelled", () => {
  let state = booted();
  state = runEvent(state, "graph.edge.active", { edge_id: "e1" });
  state = runEvent(state, "graph.edge.active", { edge_id: "e1" });
  state = runEvent(state, "graph.edge.active", { edge_id: "e2" });

  assert.deepEqual(state.run.traveled, ["e1", "e2"]);
  assert.deepEqual(Object.keys(state.run.active).sort(), ["e1", "e2"]);
});

test("a print with a label is logged with it", () => {
  const state = runEvent(booted(), "graph.print", {
    node_id: "n5",
    label: "plano",
    text: "tres modulos",
  });

  assert.equal(state.log.at(-1)?.text, "plano: tres modulos");
  assert.equal(state.log.at(-1)?.kind, "print");
});

test("the log is bounded so a loop cannot wedge the panel", () => {
  let state = booted();
  for (let index = 0; index < 600; index += 1) {
    state = runEvent(state, "graph.print", { node_id: "n1", text: `linha ${index}` });
  }

  assert.equal(state.log.length, 400);
  assert.match(state.log.at(-1)?.text ?? "", /linha 599/);
});

test("a pending question is held until it is answered", () => {
  let state = runEvent(booted(), "graph.user_chat.requested", {
    node_id: "n2",
    call_id: "c1",
    prompt: "qual?",
  });
  assert.equal(state.run.question?.callId, "c1");

  state = runEvent(state, "graph.user_chat.answered", { node_id: "n2", call_id: "c1" });
  assert.equal(state.run.question, null);
});

test("a finished run stops, records the reason and drops the lit edges", () => {
  let state = runEvent(booted(), "graph.run.started", { run_id: "r", node_count: 1 });
  state = runEvent(state, "graph.edge.active", { edge_id: "e1" });
  state = runEvent(state, "graph.run.completed", {
    run_id: "r",
    status: "exhausted",
    seconds: 3,
    nodes_executed: 9,
    reason: "reached the node ceiling (200)",
  });

  assert.equal(state.run.running, false);
  assert.equal(state.run.status, "exhausted");
  assert.deepEqual(state.run.active, {});
  assert.match(state.log.at(-1)?.text ?? "", /node ceiling/);
});

test("a validation failure from the backend blocks Play", () => {
  const state = runEvent(booted(), "graph.validation.failed", {
    issues: [{ message: "sem Start", node_id: "", pin: "", field: "", severity: "error" }],
  });

  assert.equal(state.valid, false);
  assert.equal(hasErrors(state.issues), true);
});

test("warnings alone do not block Play", () => {
  const state = reduce(booted(), {
    type: "validation",
    ok: true,
    issues: [{ message: "nothing reads it", node_id: "n1", pin: "", field: "", severity: "warning" }],
  });

  assert.equal(hasErrors(state.issues), false);
});
