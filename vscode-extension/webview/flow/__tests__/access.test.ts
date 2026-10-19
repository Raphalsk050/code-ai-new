import assert from "node:assert/strict";
import test from "node:test";

import catalogFixture from "../../../e2e/fixtures/catalog.json";
import type { FlowEdge, FlowGraph, GraphCatalog } from "../../../src/flow-schema";
import { chipsFor, grantedTools, groupTools, permissionsOf, wiredToolsets } from "../access";
import { indexCatalog } from "../model";
import { Action, EditorState, initialState, reduce } from "../store";

const RAW = catalogFixture as unknown as GraphCatalog;
const CATALOG = indexCatalog(RAW);

function node(id: string, kind: string, data: Record<string, unknown> = {}) {
  return { id, kind, pos: [0, 0] as [number, number], data };
}

function data(id: string, from: [string, string], to: [string, string]): FlowEdge {
  return { id, kind: "data", from, to };
}

function graph(nodes: FlowGraph["nodes"], edges: FlowEdge[] = []): FlowGraph {
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

function switches(doc: FlowGraph, id: string): Record<string, boolean> {
  const target = doc.nodes.find((item) => item.id === id)!;
  return Object.fromEntries(
    permissionsOf(CATALOG, doc, target).map((item) => [item.spec.field, item.on])
  );
}

// -- wiring ----------------------------------------------------------------

test("an agent's tools pin stacks wires instead of replacing them", () => {
  let state = booted(
    graph([node("a", "profession"), node("t1", "tools"), node("t2", "tools")])
  );
  state = apply(
    state,
    { type: "connect", edge: { kind: "data", from: ["t1", "tools"], to: ["a", "tools"] } },
    { type: "connect", edge: { kind: "data", from: ["t2", "tools"], to: ["a", "tools"] } }
  );

  assert.equal(state.graph.edges.length, 2);
  assert.deepEqual(
    wiredToolsets(state.graph, "a").map((item) => item.id),
    ["t1", "t2"]
  );
});

test("the same Tools node cannot be wired into an agent twice", () => {
  let state = booted(graph([node("a", "profession"), node("t1", "tools")]));
  const edge = { kind: "data" as const, from: ["t1", "tools"] as [string, string], to: ["a", "tools"] as [string, string] };
  state = apply(state, { type: "connect", edge }, { type: "connect", edge });

  assert.equal(state.graph.edges.length, 1);
  assert.ok(state.notice);
});

test("a Tools node made from a dragged wire lands connected, stacking", () => {
  let state = booted(graph([node("a", "profession"), node("t1", "tools")]));
  state = apply(
    state,
    { type: "connect", edge: { kind: "data", from: ["t1", "tools"], to: ["a", "tools"] } },
    {
      type: "addNode",
      kind: "tools",
      at: { x: 0, y: 0 },
      wire: { newPin: "tools", kind: "data", other: ["a", "tools"], otherIsSource: false },
    }
  );

  assert.equal(wiredToolsets(state.graph, "a").length, 2);
});

// -- what the agent gets -----------------------------------------------------

test("an unset switch follows the profession", () => {
  const doc = graph([node("a", "profession", { profession: "architect" })]);

  assert.deepEqual(switches(doc, "a"), {
    can_read: true,
    can_write: false,
    can_run: false,
    can_browse: true,
    can_review: false,
    // No built-in profession reaches MCP servers; a node has to switch it on.
    can_mcp: false,
  });
});

test("MCP tools are grouped by the server they come from", () => {
  const mcp = (name: string, group: string) => ({
    name,
    description: "",
    capabilities: ["mcp"],
    group,
    enabled: true,
  });
  const view = groupTools(
    indexCatalog({
      ...RAW,
      tools: [
        ...(RAW.tools ?? []),
        mcp("mcp__zeta__a", "mcp:zeta"),
        mcp("mcp__alpha__b", "mcp:alpha"),
      ],
    })
  );

  const titles = view.map((group) => group.title);
  assert.deepEqual(titles.slice(-2), ["MCP · alpha", "MCP · zeta"]);
  assert.equal(titles.includes("Other"), false);
});

test("an agent with no profession starts from the plain toolbox", () => {
  const doc = graph([node("a", "profession", { profession: "" })]);
  const on = switches(doc, "a");

  assert.equal(on.can_read, true);
  assert.equal(on.can_write, true);
  assert.equal(on.can_run, true);
});

test("a switch set on the node wins over the profession", () => {
  const doc = graph([node("a", "profession", { profession: "architect", can_write: true })]);
  const state = permissionsOf(CATALOG, doc, doc.nodes[0]).find(
    (item) => item.spec.field === "can_write"
  )!;

  assert.equal(state.on, true);
  assert.equal(state.explicit, true);
  assert.ok(grantedTools(CATALOG, doc, doc.nodes[0]).includes("write_file"));
});

test("a wired Profession node supplies the switches", () => {
  const doc = graph(
    [node("a", "profession"), node("p", "custom_profession", { can_read: false, can_run: true })],
    [data("e1", ["p", "profession"], ["a", "profession"])]
  );

  assert.equal(switches(doc, "a").can_read, false);
  assert.equal(switches(doc, "a").can_run, true);
});

test("wired tools are exactly what the agent gets, minus the blocked ones", () => {
  const doc = graph(
    [
      node("a", "profession", { profession: "code_writer", blocked_tools: ["list_files"] }),
      node("t1", "tools", { tools: ["read_file", "list_files"] }),
      node("t2", "tools", { tools: ["web_search", "not_a_tool"] }),
    ],
    [data("e1", ["t1", "tools"], ["a", "tools"]), data("e2", ["t2", "tools"], ["a", "tools"])]
  );

  assert.deepEqual(grantedTools(CATALOG, doc, doc.nodes[0]).sort(), ["read_file", "web_search"]);
});

test("every switch off leaves an agent that can only talk", () => {
  const doc = graph([
    node("a", "profession", {
      can_read: false,
      can_write: false,
      can_run: false,
      can_browse: false,
      can_review: false,
    }),
  ]);

  assert.deepEqual(grantedTools(CATALOG, doc, doc.nodes[0]), []);
});

// -- the picker ----------------------------------------------------------------

test("the picker groups tools in the catalog's order and filters by every word", () => {
  const groups = groupTools(CATALOG);
  assert.equal(groups[0].name, "read");
  assert.ok(groups.some((group) => group.name === "browser"));

  const found = groupTools(CATALOG, "browser open");
  assert.deepEqual(
    found.flatMap((group) => group.tools.map((tool) => tool.name)),
    ["browser_open"]
  );
});

// -- what the node says when pointed at ---------------------------------------

test("an agent names the switches that are on", () => {
  const doc = graph([node("a", "profession", { profession: "architect" })]);

  assert.deepEqual(
    chipsFor(CATALOG, doc, doc.nodes[0]).map((chip) => chip.label),
    ["read", "web"]
  );
});

test("an agent with tools wired says how many it gets instead", () => {
  const doc = graph(
    [node("a", "profession"), node("t", "tools", { tools: ["read_file", "web_search"] })],
    [data("e1", ["t", "tools"], ["a", "tools"])]
  );

  const chips = chipsFor(CATALOG, doc, doc.nodes[0]);
  assert.equal(chips.length, 1);
  assert.equal(chips[0].label, "2 tools");
  assert.equal(chips[0].tone, "tools");
});

test("an agent with every switch off says it can only talk", () => {
  const doc = graph([
    node("a", "custom_profession", {
      can_read: false,
      can_write: false,
      can_run: false,
      can_browse: false,
      can_review: false,
    }),
  ]);

  assert.deepEqual(chipsFor(CATALOG, doc, doc.nodes[0]), [{ label: "talk only", tone: "muted" }]);
});
