import assert from "node:assert/strict";
import test from "node:test";

import {
  canConnect,
  emptyFlow,
  FLOW_SCHEMA_VERSION,
  nextId,
  parseFlow,
  serializeFlow,
} from "../flow-schema";

const SAMPLE = {
  version: 1,
  name: "pipeline",
  variables: [{ id: "v1", name: "ticket", type: "string", default: "ABC-1" }],
  nodes: [
    { id: "n1", kind: "start", pos: [-220, 0], data: {} },
    {
      id: "n2",
      kind: "profession",
      pos: [20, 0],
      data: { profession: "architect", task: "desenhe" },
    },
  ],
  edges: [{ id: "e1", kind: "exec", from: ["n1", "out"], to: ["n2", "in"] }],
  view: { pan: [0, 0], zoom: 1 },
};

// -- reading -------------------------------------------------------------

test("an empty file opens as a fresh graph rather than an error", () => {
  const { graph, error } = parseFlow("   \n");

  assert.equal(error, undefined);
  assert.equal(graph?.nodes.length, 2);
  assert.equal(graph?.edges.length, 1);
});

test("broken JSON is reported, not thrown", () => {
  const { graph, error } = parseFlow("{ oops");

  assert.equal(graph, undefined);
  assert.match(error ?? "", /JSON/);
});

test("a JSON array is not a graph", () => {
  assert.match(parseFlow("[]").error ?? "", /objeto JSON/);
});

test("a file from a newer build is refused with its version", () => {
  const { error } = parseFlow(JSON.stringify({ ...SAMPLE, version: 99 }));

  assert.match(error ?? "", /99/);
});

test("missing sections default instead of breaking the editor", () => {
  const { graph } = parseFlow(JSON.stringify({ version: 1, name: "x" }));

  assert.deepEqual(graph?.nodes, []);
  assert.deepEqual(graph?.edges, []);
  assert.deepEqual(graph?.variables, []);
  assert.deepEqual(graph?.view, { pan: [0, 0], zoom: 1 });
});

test("a half-written edge is dropped rather than drawn as a stub", () => {
  const { graph } = parseFlow(
    JSON.stringify({
      ...SAMPLE,
      edges: [
        { id: "e1", kind: "exec", from: ["n1"], to: ["n2", "in"] },
        { id: "e2", kind: "data", from: ["n1", "out"], to: ["n2", "task"] },
      ],
    })
  );

  assert.equal(graph?.edges.length, 1);
  assert.equal(graph?.edges[0].id, "e2");
});

test("a zoom of zero is read as 1, not as a canvas nobody can see", () => {
  const { graph } = parseFlow(JSON.stringify({ ...SAMPLE, view: { pan: [1, 2], zoom: 0 } }));

  assert.equal(graph?.view.zoom, 1);
  assert.deepEqual(graph?.view.pan, [1, 2]);
});

// -- writing -------------------------------------------------------------

test("a parse/serialize round trip is byte-stable", () => {
  const text = serializeFlow(parseFlow(JSON.stringify(SAMPLE)).graph!);
  const again = serializeFlow(parseFlow(text).graph!);

  assert.equal(text, again);
  assert.ok(text.endsWith("}\n"), "a text file ends with a newline");
});

test("the key order is fixed, so a moved node is a small diff", () => {
  const graph = parseFlow(JSON.stringify(SAMPLE)).graph!;
  const before = serializeFlow(graph).split("\n");

  graph.nodes[1].pos = [24, 0];
  const after = serializeFlow(graph).split("\n");

  const changed = before.filter((line, index) => line !== after[index]);
  assert.equal(changed.length, 1, `a 4px nudge changed ${changed.length} lines`);
  assert.match(changed[0], /20/);
});

test("node fields are written in a stable order whatever order they were set", () => {
  const graph = emptyFlow("x");
  graph.nodes[0] = {
    id: "n1",
    kind: "profession",
    pos: [0, 0],
    data: { task: "b", profession: "a", max_steps: 2 },
  };
  const first = serializeFlow(graph);

  graph.nodes[0].data = { max_steps: 2, profession: "a", task: "b" };
  assert.equal(serializeFlow(graph), first);
});

test("positions are written as integers", () => {
  const graph = emptyFlow("x");
  graph.nodes[0].pos = [12.4, -7.8];

  assert.match(serializeFlow(graph), /"pos": \[\s*12,\s*-8\s*\]/);
});

test("an undefined field is left out instead of written as null", () => {
  const graph = emptyFlow("x");
  graph.nodes[0].data = { label: "a", gone: undefined };

  const written = JSON.parse(serializeFlow(graph));
  assert.deepEqual(written.nodes[0].data, { label: "a" });
});

test("a graph with no name still writes the version", () => {
  const written = JSON.parse(serializeFlow({ ...emptyFlow(""), version: 0 }));

  assert.equal(written.version, FLOW_SCHEMA_VERSION);
  assert.equal(written.name, "");
});

// -- pin rules -----------------------------------------------------------

test("exec never mixes with data", () => {
  assert.equal(canConnect("exec", "exec"), true);
  assert.equal(canConnect("exec", "string"), false);
  assert.equal(canConnect("string", "exec"), false);
  assert.equal(canConnect("exec", "any"), false);
});

test("any accepts and is accepted by everything but exec", () => {
  for (const type of ["string", "int", "float", "bool", "model"]) {
    assert.equal(canConnect(type, "any"), true, `${type} -> any`);
    assert.equal(canConnect("any", type), true, `any -> ${type}`);
  }
});

test("int widens to float, and scalars stringify, but not the other way", () => {
  assert.equal(canConnect("int", "float"), true);
  assert.equal(canConnect("float", "int"), false);
  assert.equal(canConnect("int", "string"), true);
  assert.equal(canConnect("bool", "string"), true);
  assert.equal(canConnect("string", "int"), false);
  assert.equal(canConnect("model", "string"), false);
});

// -- ids -----------------------------------------------------------------

test("ids fill the first free slot and never collide", () => {
  assert.equal(nextId("n", []), "n1");
  assert.equal(nextId("n", ["n1", "n2", "n4"]), "n3");
  assert.equal(nextId("e", ["e1", "e2", "e3"]), "e4");
});
