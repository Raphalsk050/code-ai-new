import assert from "node:assert/strict";
import test from "node:test";

import catalogFixture from "../../../e2e/fixtures/catalog.json";
import type { GraphCatalog } from "../../../src/flow-schema";
import { aimAt, indexCatalog, isRound, layoutGraph, pinAnchor, ROUND_D, wirePath } from "../model";

const CATALOG = indexCatalog(catalogFixture as unknown as GraphCatalog);

function layouts() {
  return layoutGraph(
    {
      version: 1,
      name: "t",
      variables: [{ id: "v1", name: "ticket", type: "string", default: "" }],
      nodes: [
        { id: "m", kind: "model", pos: [0, 0], data: {} },
        { id: "v", kind: "var_get", pos: [0, 200], data: { variable: "ticket" } },
        { id: "a", kind: "profession", pos: [400, 0], data: {} },
      ],
      edges: [],
      view: { pan: [0, 0], zoom: 1 },
    },
    CATALOG
  );
}

test("models and variables are round; the nodes that take a step are not", () => {
  assert.ok(isRound("model") && isRound("var_get"));
  assert.ok(!isRound("profession") && !isRound("var_set") && !isRound("start"));
  const model = layouts().get("m")!;
  assert.equal(model.inputs.length, 0);
  assert.equal(model.outputs.length, 1);
});

test("a round node's connector rides its rim, facing where it is aimed", () => {
  const all = layouts();
  const model = all.get("m")!;
  const center = { x: model.width / 2, y: ROUND_D / 2 };
  const near = (a: number, b: number) => Math.abs(a - b) < 1e-9;
  const rest = pinAnchor(all, "m", "model", "out")!;
  assert.ok(near(rest.x, center.x + ROUND_D / 2) && near(rest.y, center.y), "no aim: facing right");
  const down = pinAnchor(all, "m", "model", "out", new Map([["m", Math.PI / 2]]))!;
  assert.ok(near(down.x, center.x) && near(down.y, center.y + ROUND_D / 2));
  assert.ok(near(down.dx, 0) && near(down.dy, 1), "and its wire leaves outwards");
});

test("a connector aims at the middle of where its wires go", () => {
  const center = { x: 0, y: 0 };
  assert.equal(aimAt(center, []), 0);
  assert.ok(Math.abs(aimAt(center, [{ x: 10, y: -10 }, { x: 10, y: 10 }])) < 1e-9);
  assert.ok(Math.abs(aimAt(center, [{ x: 0, y: -50 }]) + Math.PI / 2) < 1e-9);
});

test("a side pin's wire is drawn as before: straight out, straight in", () => {
  const all = layouts();
  const out = pinAnchor(all, "a", "report", "out")!;
  assert.deepEqual([out.dx, out.dy], [1, 0]);
  assert.equal(
    wirePath({ x: 0, y: 0 }, { x: 200, y: 50 }),
    "M 0 0 C 100 0, 100 50, 200 50"
  );
});
