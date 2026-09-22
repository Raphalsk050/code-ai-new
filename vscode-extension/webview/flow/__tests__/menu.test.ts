import assert from "node:assert/strict";
import test from "node:test";

import catalogFixture from "../../../e2e/fixtures/catalog.json";
import type { FlowGraph, GraphCatalog } from "../../../src/flow-schema";
import { filterEntries, MenuEntry } from "../context-menu";
import { instantiate, buildClip, parseClip } from "../clipboard";
import { layoutNode, pinsOf } from "../model";
import { Action, EditorState, initialState, reduce } from "../store";

const CATALOG = catalogFixture as unknown as GraphCatalog;

function entry(label: string, section = "Fluxo", hint = ""): MenuEntry {
  return { id: label, label, hint, section, colour: "#fff", run: () => undefined };
}

// -- the filter ----------------------------------------------------------

test("an empty query keeps every entry, in order", () => {
  const entries = [entry("Start"), entry("Print", "Debug")];
  assert.deepEqual(filterEntries(entries, "  "), entries);
});

test("the filter matches label, hint and section", () => {
  const entries = [entry("Start"), entry("Print", "Debug"), entry("Agent", "Agents", "string")];

  assert.deepEqual(filterEntries(entries, "print").map((item) => item.label), ["Print"]);
  assert.deepEqual(filterEntries(entries, "agents").map((item) => item.label), ["Agent"]);
  assert.deepEqual(filterEntries(entries, "string").map((item) => item.label), ["Agent"]);
});

test("every word has to match, in any order", () => {
  const entries = [entry("Set variable", "Dados", "int"), entry("Get variable", "Dados", "string")];

  assert.deepEqual(
    filterEntries(entries, "variable int").map((item) => item.label),
    ["Set variable"]
  );
  assert.deepEqual(filterEntries(entries, "int variable").length, 1);
  assert.deepEqual(filterEntries(entries, "variable zzz"), []);
});

test("the filter ignores case", () => {
  assert.equal(filterEntries([entry("Ask the user", "Agentes")], "ASK THE").length, 1);
});

// -- what the catalog offers a dragged pin -------------------------------

/**
 * The same rule the editor applies when it builds the pin menu. Kept here as a
 * small reimplementation on purpose: if this and the schema ever disagree, the
 * test says so instead of both drifting together.
 */
function acceptingPins(from: string): string[] {
  const hits: string[] = [];
  for (const spec of CATALOG.node_kinds) {
    const probe = { id: "p", kind: spec.kind, pos: [0, 0] as [number, number], data: { outputs: 2 } };
    for (const pin of pinsOf(spec, probe)) {
      if (pin.direction !== "in") continue;
      const fits =
        from === "exec" || pin.type === "exec"
          ? from === pin.type
          : from === pin.type ||
            from === "any" ||
            pin.type === "any" ||
            (from === "int" && pin.type === "float") ||
            (pin.type === "string" && ["int", "float", "bool"].includes(from));
      if (fits) hits.push(`${spec.kind}.${pin.name}`);
    }
  }
  return hits;
}

test("an exec output is only offered exec inputs", () => {
  const hits = acceptingPins("exec");

  assert.ok(hits.includes("print.in"));
  assert.ok(hits.includes("end.in"));
  assert.ok(!hits.some((hit) => hit.endsWith(".task")));
  assert.ok(!hits.some((hit) => hit.endsWith(".model")));
});

test("a string output reaches tasks, prompts and prints but no exec pin", () => {
  const hits = acceptingPins("string");

  assert.ok(hits.includes("profession.task"));
  assert.ok(hits.includes("user_chat.prompt"));
  assert.ok(hits.includes("print.value"));
  assert.ok(hits.includes("loop.items"));
  assert.ok(!hits.some((hit) => hit.endsWith(".in")));
  assert.ok(!hits.includes("profession.model"));
});

test("a model output only fits a model pin (and the any-pins)", () => {
  const hits = acceptingPins("model");

  assert.ok(hits.includes("profession.model"));
  assert.ok(hits.includes("decision.model"));
  assert.ok(!hits.includes("profession.task"));
  // `any` accepts anything by design - Print and Set variable take a model too.
  assert.ok(hits.includes("print.value"));
});

test("a bool output fits a condition and, stringified, a task", () => {
  const hits = acceptingPins("bool");

  assert.ok(hits.includes("decision.condition"));
  assert.ok(hits.includes("loop.condition"));
  assert.ok(hits.includes("profession.task"));
});

// -- creating a node already wired ---------------------------------------

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

function booted(doc: FlowGraph): EditorState {
  let state = reduce(initialState(), { type: "catalog", catalog: CATALOG });
  state = reduce(state, { type: "document", graph: doc, fileName: "t.flow.json" });
  return state;
}

function apply(state: EditorState, ...actions: Action[]): EditorState {
  return actions.reduce(reduce, state);
}

test("a node created from a dragged output comes back wired", () => {
  const doc = graph({
    nodes: [{ id: "n1", kind: "start", pos: [0, 0], data: {} }],
  });
  const state = apply(booted(doc), {
    type: "addNode",
    kind: "print",
    at: { x: 200, y: 0 },
    wire: { newPin: "in", kind: "exec", other: ["n1", "out"], otherIsSource: true },
  });

  const created = state.graph.nodes.find((item) => item.kind === "print");
  assert.ok(created);
  assert.deepEqual(state.graph.edges, [
    { id: "e1", kind: "exec", from: ["n1", "out"], to: [created.id, "in"] },
  ]);
  // One gesture, one undo.
  assert.equal(state.undo.length, 1);
  assert.deepEqual(apply(state, { type: "undo" }).graph.edges, []);
});

test("a node created from a dragged input is wired the other way round", () => {
  const doc = graph({
    nodes: [{ id: "n1", kind: "profession", pos: [0, 0], data: { profession: "architect" } }],
  });
  const state = apply(booted(doc), {
    type: "addNode",
    kind: "model",
    at: { x: -200, y: 0 },
    wire: { newPin: "model", kind: "data", other: ["n1", "model"], otherIsSource: false },
  });

  const created = state.graph.nodes.find((item) => item.kind === "model");
  assert.ok(created);
  assert.deepEqual(state.graph.edges[0].from, [created.id, "model"]);
  assert.deepEqual(state.graph.edges[0].to, ["n1", "model"]);
});

test("wiring into an occupied exec output replaces the wire that was there", () => {
  const doc = graph({
    nodes: [
      { id: "n1", kind: "start", pos: [0, 0], data: {} },
      { id: "n2", kind: "end", pos: [200, 0], data: {} },
    ],
    edges: [{ id: "e1", kind: "exec", from: ["n1", "out"], to: ["n2", "in"] }],
  });
  const state = apply(booted(doc), {
    type: "addNode",
    kind: "print",
    at: { x: 100, y: 100 },
    wire: { newPin: "in", kind: "exec", other: ["n1", "out"], otherIsSource: true },
  });

  assert.equal(state.graph.edges.length, 1);
  assert.equal(state.graph.edges[0].to[0], state.graph.nodes.at(-1)?.id);
});

test("a variable and the node that reads it are born together", () => {
  const state = apply(booted(graph()), {
    type: "createVariableWithNode",
    varType: "float",
    at: { x: 40, y: 40 },
    mode: "get",
  });

  assert.equal(state.graph.variables.length, 1);
  assert.equal(state.graph.variables[0].type, "float");
  assert.equal(state.graph.nodes[0].kind, "var_get");
  assert.equal(state.graph.nodes[0].data.variable, state.graph.variables[0].name);
  assert.equal(state.undo.length, 1);

  const undone = apply(state, { type: "undo" });
  assert.deepEqual(undone.graph.variables, []);
  assert.deepEqual(undone.graph.nodes, []);
});

test("the set flavour creates a Set node instead", () => {
  const state = apply(booted(graph()), {
    type: "createVariableWithNode",
    varType: "bool",
    at: { x: 0, y: 0 },
    mode: "set",
  });

  assert.equal(state.graph.nodes[0].kind, "var_set");
});

// -- duplicating through the menu ----------------------------------------

test("duplicating a selection keeps the wires inside it and drops the rest", () => {
  const doc = graph({
    nodes: [
      { id: "n1", kind: "start", pos: [0, 0], data: {} },
      { id: "n2", kind: "profession", pos: [200, 0], data: { profession: "architect", task: "x" } },
      { id: "n3", kind: "print", pos: [400, 0], data: { label: "p" } },
    ],
    edges: [
      { id: "e1", kind: "exec", from: ["n1", "out"], to: ["n2", "in"] },
      { id: "e2", kind: "exec", from: ["n2", "out"], to: ["n3", "in"] },
      { id: "e3", kind: "data", from: ["n2", "report"], to: ["n3", "value"] },
    ],
  });
  const state = booted(doc);
  const clip = buildClip(state.graph, ["n2", "n3"]);
  assert.ok(clip);
  assert.equal(clip.nodes.length, 2);
  assert.equal(clip.edges.length, 2, "only the wires inside the selection travel");

  const { nodes, edges } = instantiate(clip, state.graph);
  const pasted = apply(state, { type: "paste", nodes, edges });

  assert.equal(pasted.graph.nodes.length, 5);
  assert.equal(pasted.graph.edges.length, 5);
  const ids = pasted.graph.nodes.map((item) => item.id);
  assert.equal(new Set(ids).size, ids.length, "a pasted node reused an id");
  // The copy is offset, so it is visibly a copy.
  const copy = pasted.graph.nodes.find((item) => !["n1", "n2", "n3"].includes(item.id));
  assert.notDeepEqual(copy?.pos, [200, 0]);
});

test("a clip from another window is read back, and rubbish is ignored", () => {
  const doc = graph({ nodes: [{ id: "n1", kind: "start", pos: [0, 0], data: {} }] });
  const clip = buildClip(doc, ["n1"]);
  assert.ok(clip);

  assert.deepEqual(parseClip(JSON.stringify(clip))?.nodes.length, 1);
  assert.equal(parseClip("not json at all"), null);
  assert.equal(parseClip(JSON.stringify({ nodes: [] })), null);
});

// -- geometry ------------------------------------------------------------

test("a node grows with the number of pin rows", () => {
  const start = CATALOG.node_kinds.find((item) => item.kind === "start")!;
  const agent = CATALOG.node_kinds.find((item) => item.kind === "profession")!;

  const small = layoutNode({ id: "a", kind: "start", pos: [0, 0], data: {} }, start);
  const big = layoutNode(
    { id: "b", kind: "profession", pos: [0, 0], data: { profession: "architect" } },
    agent
  );

  assert.ok(big.height > small.height);
  assert.equal(big.inputs.length, 3);
  assert.equal(big.outputs.length, 2);
  // Exec pins lead, so the flow reads across the top of the node.
  assert.equal(big.inputs[0].pin.type, "exec");
  assert.equal(big.outputs[0].pin.type, "exec");
});

test("a Sequence shows as many pins as it was asked for", () => {
  const spec = CATALOG.node_kinds.find((item) => item.kind === "sequence")!;
  const node = { id: "s", kind: "sequence", pos: [0, 0] as [number, number], data: { outputs: 5 } };

  const pins = pinsOf(spec, node).filter((pin) => pin.direction === "out");

  assert.deepEqual(
    pins.map((pin) => pin.name),
    ["out_0", "out_1", "out_2", "out_3", "out_4"]
  );
});
