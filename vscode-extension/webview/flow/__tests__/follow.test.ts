import assert from "node:assert/strict";
import test from "node:test";

import catalogFixture from "../../../e2e/fixtures/catalog.json";
import type { FlowGraph, FlowNode, GraphCatalog } from "../../../src/flow-schema";
import { wheelGesture } from "../canvas";
import {
  FocusReel,
  growthOf,
  MAX_BACKLOG,
  OPEN_W,
  PANEL_H,
  presented,
  ReelInput,
  stepSpring,
} from "../focus";
import { Bounds, commentOrder, indexCatalog, layoutGraph, NodeLayout } from "../model";
import { Action, EditorState, initialState, NodeRunStatus, OUTPUT_LIMIT, readable, reduce, worthOpening } from "../store";

const CATALOG = catalogFixture as unknown as GraphCatalog;

function node(id: string, kind: string, pos: [number, number], data: Record<string, unknown> = {}): FlowNode {
  return { id, kind, pos, data };
}

function graph(nodes: FlowNode[], view = { pan: [0, 0] as [number, number], zoom: 1 }): FlowGraph {
  return { version: 1, name: "t", variables: [], nodes, edges: [], view };
}

function booted(doc: FlowGraph): EditorState {
  let state = reduce(initialState(), { type: "catalog", catalog: CATALOG });
  state = reduce(state, { type: "document", graph: doc, fileName: "t.flow.json" });
  return state;
}

function apply(state: EditorState, ...actions: Action[]): EditorState {
  return actions.reduce(reduce, state);
}

const event = (eventType: string, payload: Record<string, unknown>): Action => ({
  type: "graphEvent",
  eventType,
  payload,
});

const layoutsOf = (doc: FlowGraph) => layoutGraph(doc, indexCatalog(CATALOG));

function boxOf(layout: NodeLayout): Bounds {
  const [x, y] = layout.node.pos;
  return { minX: x, minY: y, maxX: x + layout.width, maxY: y + layout.height };
}

function overlap(a: Bounds, b: Bounds): boolean {
  return a.minX < b.maxX && b.minX < a.maxX && a.minY < b.maxY && b.minY < a.maxY;
}

// -- comments --------------------------------------------------------------

const box = (id: string, minX: number, minY: number, width: number, height: number) => ({
  id,
  box: { minX, minY, maxX: minX + width, maxY: minY + height },
});

test("a comment starting right of another's middle is drawn in front of it", () => {
  const order = commentOrder([box("right", 300, 50, 900, 600), box("left", 0, 0, 500, 300)]);
  assert.deepEqual(order.map((item) => item.id), ["left", "right"]);
});

test("a comment starting left of another's middle may go behind it", () => {
  const order = commentOrder([box("small", 100, 50, 300, 200), box("big", 0, 0, 1000, 700)]);
  // Nested: the big one goes behind, so the small one stays reachable.
  assert.deepEqual(order.map((item) => item.id), ["big", "small"]);
});

test("comments that do not touch keep the size order", () => {
  const order = commentOrder([box("a", 0, 0, 100, 100), box("b", 2000, 0, 900, 900)]);
  assert.deepEqual(order.map((item) => item.id), ["b", "a"]);
});

// -- the camera is not an edit ---------------------------------------------

test("undo and redo leave the camera where the user has it", () => {
  let state = booted(graph([node("n1", "print", [0, 0])]));
  state = apply(state, { type: "moveNodes", ids: ["n1"], dx: 40, dy: 0, commit: true });
  state = apply(state, { type: "setView", pan: [300, 200], zoom: 1.5 });
  state = apply(state, { type: "undo" });
  assert.deepEqual(state.graph.nodes[0].pos, [0, 0]);
  assert.deepEqual(state.graph.view, { pan: [300, 200], zoom: 1.5 });
  state = apply(state, { type: "setView", pan: [-50, 10] }, { type: "redo" });
  assert.deepEqual(state.graph.nodes[0].pos, [40, 0]);
  assert.deepEqual(state.graph.view.pan, [-50, 10]);
});

test("panning pushes nothing onto the undo stack", () => {
  let state = booted(graph([node("n1", "print", [0, 0])]));
  state = apply(state, { type: "setView", pan: [10, 10] }, { type: "setView", zoom: 2 });
  assert.equal(state.undo.length, 0);
  assert.equal(state.dirty, false);
});

test("a document that changes under the panel keeps the panel's camera", () => {
  let state = booted(graph([node("n1", "print", [0, 0])], { pan: [5, 5], zoom: 1 }));
  assert.deepEqual(state.graph.view.pan, [5, 5]);
  state = apply(state, { type: "setView", pan: [400, 100] });
  state = apply(state, {
    type: "document",
    graph: graph([node("n1", "print", [80, 0])], { pan: [5, 5], zoom: 1 }),
    fileName: "t.flow.json",
  });
  assert.deepEqual(state.graph.nodes[0].pos, [80, 0]);
  assert.deepEqual(state.graph.view.pan, [400, 100]);
});

test("only the user's own pans count against the camera that follows a run", () => {
  let state = booted(graph([]));
  state = apply(state, { type: "setView", pan: [1, 1], follow: true });
  assert.equal(state.viewEdits, 0);
  state = apply(state, { type: "setView", pan: [2, 2] });
  assert.equal(state.viewEdits, 1);
});

// -- what each node says ---------------------------------------------------

test("a run remembers the order it reached its nodes in", () => {
  let state = booted(graph([]));
  state = apply(
    state,
    event("graph.run.started", { run_id: "r1" }),
    event("graph.node.started", { node_id: "a" }),
    event("graph.node.started", { node_id: "b" }),
    event("graph.node.started", { node_id: "a" })
  );
  assert.deepEqual(state.run.order, ["a", "b", "a"]);
  assert.equal(state.run.orderBase, 0);
});

test("an agent's stream reads as one answer, with its tools in between", () => {
  let state = booted(graph([]));
  state = apply(
    state,
    event("graph.run.started", { run_id: "r1" }),
    event("graph.node.started", { node_id: "a" }),
    event("graph.agent.started", { node_id: "a", agent_name: "ada", profession: "coder", model: "m" }),
    event("graph.node.output", { node_id: "a", channel: "thinking", text: "hmm " }),
    event("graph.node.output", { node_id: "a", channel: "thinking", text: "ok" }),
    event("graph.node.output", { node_id: "a", channel: "answer", text: "Vou " }),
    event("graph.node.output", { node_id: "a", channel: "answer", text: "ler." }),
    event("graph.node.progress", { node_id: "a", event: "tool.call.started", detail: "read_file" }),
    event("graph.node.output", { node_id: "a", channel: "answer", text: "Pronto." }),
    event("graph.node.completed", { node_id: "a", summary: "Pronto.", seconds: 2 })
  );
  assert.deepEqual(
    state.run.output.a.map((part) => [part.kind, part.text]),
    [
      ["info", "ada · coder · m"],
      ["thinking", "hmm ok"],
      ["text", "Vou ler."],
      ["tool", "read_file"],
      ["text", "Pronto."],
      ["info", "Done in 2s"],
    ]
  );
  assert.equal(readable(state.run, "a"), true);
});

test("a node with no text of its own shows its result", () => {
  let state = booted(graph([]));
  state = apply(
    state,
    event("graph.run.started", { run_id: "r1" }),
    event("graph.node.started", { node_id: "m" }),
    event("graph.node.completed", { node_id: "m", summary: "42", seconds: 0 })
  );
  assert.deepEqual(state.run.output.m[0], { kind: "result", text: "42" });
});

test("a node's text is bounded, oldest first", () => {
  let state = booted(graph([]));
  state = apply(state, event("graph.run.started", { run_id: "r1" }));
  for (let index = 0; index < 8; index += 1) {
    state = apply(state, event("graph.node.output", { node_id: "a", channel: "answer", text: "x".repeat(10000) }));
  }
  const total = state.run.output.a.reduce((sum, part) => sum + part.text.length, 0);
  assert.equal(total, OUTPUT_LIMIT);
});

// -- making room -----------------------------------------------------------

/** A 3x3 grid of print nodes, 300 apart, the focus in the middle. */
function grid(): FlowGraph {
  const nodes: FlowNode[] = [];
  for (let row = 0; row < 3; row += 1) {
    for (let col = 0; col < 3; col += 1) nodes.push(node(`r${row}c${col}`, "print", [col * 300, row * 220]));
  }
  return graph(nodes);
}

test("an open node grows about its centre by the panel and the reading width", () => {
  const layouts = layoutsOf(grid());
  const before = layouts.get("r1c1")!;
  const after = presented(layouts, "r1c1", 1).get("r1c1")!;
  assert.equal(after.width, OPEN_W);
  assert.equal(after.height, before.height + PANEL_H);
  const centre = (layout: NodeLayout) => [layout.node.pos[0] + layout.width / 2, layout.node.pos[1] + layout.height / 2];
  assert.deepEqual(centre(after), centre(before));
  assert.ok(after.outputs.every((pin) => pin.x === OPEN_W));
});

test("neighbours step aside along the side they are on", () => {
  const layouts = layoutsOf(grid());
  const shown = presented(layouts, "r1c1", 1);
  const moved = (id: string) => {
    const a = layouts.get(id)!.node.pos;
    const b = shown.get(id)!.node.pos;
    return [Math.sign(b[0] - a[0]), Math.sign(b[1] - a[1])];
  };
  assert.deepEqual(moved("r1c2"), [1, 0]);
  assert.deepEqual(moved("r1c0"), [-1, 0]);
  assert.deepEqual(moved("r0c1"), [0, -1]);
  assert.deepEqual(moved("r2c1"), [0, 1]);
  assert.deepEqual(moved("r0c0"), [-1, -1]);
  assert.deepEqual(moved("r2c2"), [1, 1]);
});

test("nothing overlaps once a node is open, at any point of the spring", () => {
  const layouts = layoutsOf(grid());
  for (const p of [0.25, 0.5, 1, 1.08]) {
    const shown = [...presented(layouts, "r1c1", p).values()];
    for (let i = 0; i < shown.length; i += 1) {
      for (let j = i + 1; j < shown.length; j += 1) {
        assert.ok(!overlap(boxOf(shown[i]), boxOf(shown[j])), `${shown[i].node.id} hits ${shown[j].node.id} at p=${p}`);
      }
    }
  }
});

test("a comment holding the open node stretches to keep holding it", () => {
  const doc = graph([
    node("c1", "comment", [-40, -80], { width: 400, height: 260 }),
    node("n1", "print", [0, 0]),
  ]);
  const shown = presented(layoutsOf(doc), "n1", 1);
  const comment = boxOf(shown.get("c1")!);
  const opened = boxOf(shown.get("n1")!);
  assert.ok(comment.minX <= opened.minX && comment.maxX >= opened.maxX);
  assert.ok(comment.minY <= opened.minY && comment.maxY >= opened.maxY);
});

test("a closed node is drawn exactly where it is", () => {
  const layouts = layoutsOf(grid());
  assert.equal(presented(layouts, "r1c1", 0), layouts);
  assert.equal(presented(layouts, null, 1), layouts);
});

// -- one node at a time ----------------------------------------------------

test("the spring overshoots and comes to rest", () => {
  let spring = { x: 0, v: 0 };
  let peak = 0;
  for (let frame = 0; frame < 120; frame += 1) {
    spring = stepSpring(spring, 1, 1 / 60, { k: 210, c: 17 });
    peak = Math.max(peak, spring.x);
  }
  assert.ok(peak > 1.02, `peak ${peak}`);
  assert.ok(Math.abs(spring.x - 1) < 0.004);
});

function reelInput(over: Partial<ReelInput> & { statuses?: Record<string, NodeRunStatus> }): ReelInput {
  const statuses = over.statuses ?? {};
  return {
    now: 0,
    enabled: true,
    running: true,
    order: [],
    orderBase: 0,
    status: (id) => statuses[id] ?? "idle",
    readable: () => true,
    pinned: null,
    ...over,
  };
}

/** Run the reel at 60 fps for `seconds`, recording which node was open on each frame. */
function play(reel: FocusReel, seconds: number, input: (now: number) => ReelInput, start = 0): string[] {
  const seen: string[] = [];
  for (let frame = 0; frame < seconds * 60; frame += 1) {
    const now = start + (frame * 1000) / 60;
    reel.tick(input(now), 1 / 60);
    seen.push(reel.step ? `${reel.step.id}:${reel.step.phase}` : "-");
  }
  return seen;
}

test("the next node opens only once the one before has folded all the way back", () => {
  const reel = new FocusReel();
  const statuses: Record<string, NodeRunStatus> = { a: "done", b: "done" };
  const seen = play(reel, 6, (now) => reelInput({ now, order: ["a", "b"], statuses }));
  const firstB = seen.findIndex((item) => item.startsWith("b:"));
  assert.ok(firstB > 0, "b opened");
  assert.equal(seen[firstB - 1], "a:closing");
  assert.ok(!seen.slice(firstB).some((item) => item.startsWith("a:")), "a never reopened");
  // At the moment b starts, a is shut: the reel's spring started again from zero.
  assert.equal(seen[seen.length - 1], "-");
});

test("a node stays open while it is still working", () => {
  const reel = new FocusReel();
  const seen = play(reel, 5, (now) => reelInput({ now, order: ["a", "b"], statuses: { a: "running" } }));
  assert.equal(seen[seen.length - 1], "a:open");
});

test("a backlog past the limit drops its oldest steps", () => {
  const reel = new FocusReel();
  const order = Array.from({ length: MAX_BACKLOG + 10 }, (_, index) => `n${index}`);
  const statuses = Object.fromEntries(order.map((id) => [id, "done" as NodeRunStatus]));
  reel.tick(reelInput({ order, statuses }), 1 / 60);
  assert.equal(reel.step?.id, `n${order.length - MAX_BACKLOG}`);
});

test("a node with nothing to say runs by in place, and the next one that talks opens", () => {
  const reel = new FocusReel();
  const statuses: Record<string, NodeRunStatus> = { start: "done", sum: "done", agent: "running" };
  const seen = play(reel, 2, (now) =>
    reelInput({ now, order: ["start", "sum", "agent"], statuses, opens: (id) => id === "agent" })
  );
  assert.ok(!seen.some((item) => item.startsWith("start:") || item.startsWith("sum:")), "a quiet node opened");
  assert.equal(seen[seen.length - 1], "agent:open");
});

test("a quiet node still running is waited on: what it says next may be worth opening for", () => {
  const reel = new FocusReel();
  const said = { print: false };
  const statuses: Record<string, NodeRunStatus> = { print: "running" };
  const input = (now: number) => reelInput({ now, order: ["print"], statuses, opens: () => said.print });
  const open = () => reel.step;
  play(reel, 0.2, input);
  assert.equal(open(), null);
  said.print = true;
  statuses.print = "done";
  play(reel, 0.2, input, 200);
  assert.equal(open()?.id, "print");
});

test("agents, decisions and questions open; a sum, a start or a variable only when it said something", () => {
  let state = booted(graph([node("p", "print", [0, 0]), node("m", "math", [0, 0])]));
  const run = () => state.run;
  assert.ok(worthOpening(run(), "profession", "a"));
  assert.ok(worthOpening(run(), "user_chat", "q"));
  assert.ok(!worthOpening(run(), "math", "m"));
  state = apply(state, event("graph.node.completed", { node_id: "m", summary: "5", seconds: 0 }));
  assert.ok(!worthOpening(run(), "math", "m"), "a one-line result is not worth opening for");
  state = apply(state, event("graph.print", { node_id: "p", text: "hello" }));
  assert.ok(worthOpening(run(), "print", "p"));
});

test("with the setting off nothing opens on its own, and a node opened by hand still does", () => {
  const reel = new FocusReel();
  const open = () => reel.step;
  reel.tick(reelInput({ enabled: false, order: ["a"], statuses: { a: "running" } }), 1 / 60);
  assert.equal(open(), null);
  reel.tick(reelInput({ enabled: false, order: ["a"], pinned: "a" }), 1 / 60);
  assert.equal(open()?.id, "a");
  assert.equal(open()?.manual, true);
});

test("a node opened by hand stays open until it is closed by hand", () => {
  const reel = new FocusReel();
  const open = play(reel, 4, (now) => reelInput({ now, running: false, pinned: "a" }));
  assert.equal(open[open.length - 1], "a:open");
  const shut = play(reel, 3, (now) => reelInput({ now, running: false, pinned: null }), 4000);
  assert.equal(shut[shut.length - 1], "-");
});

test("growth leaves a node no smaller than it is", () => {
  const layout = layoutsOf(grid()).get("r0c0")!;
  const growth = growthOf(layout);
  assert.ok(growth.w >= 0 && growth.h === PANEL_H);
});

// -- the wheel --------------------------------------------------------------

test("a Magic Mouse slide moves the camera, a wheel notch zooms, a pinch zooms", () => {
  const base = { deltaX: 0, deltaY: 0, deltaMode: 0, ctrlKey: false };
  assert.equal(wheelGesture({ ...base, deltaY: 4.5, wheelDeltaY: -13 }), "pan");
  assert.equal(wheelGesture({ ...base, deltaX: 6, deltaY: 0, wheelDeltaY: 0 }), "pan");
  assert.equal(wheelGesture({ ...base, deltaY: 100, wheelDeltaY: -120 }), "wheel");
  assert.equal(wheelGesture({ ...base, deltaY: 125, wheelDeltaY: -120 }), "wheel");
  assert.equal(wheelGesture({ ...base, deltaY: -3, ctrlKey: true }), "pinch");
  assert.equal(wheelGesture({ ...base, deltaY: 3, deltaMode: 1 }), "wheel");
  // The momentum of a slide that happens to land on 120 is still the slide.
  assert.equal(wheelGesture({ ...base, deltaY: 40, wheelDeltaY: -120 }, true), "pan");
});
