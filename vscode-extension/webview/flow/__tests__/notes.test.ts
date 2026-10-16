import assert from "node:assert/strict";
import test from "node:test";

import catalogFixture from "../../../e2e/fixtures/catalog.json";
import type { FlowGraph, FlowNode, GraphCatalog } from "../../../src/flow-schema";
import {
  COMMENT_MIN,
  commentAround,
  indexCatalog,
  layoutGraph,
  movedBy,
  nodesInRect,
  resized,
  wrapText,
} from "../model";
import { Action, EditorState, initialState, reduce } from "../store";

const CATALOG = catalogFixture as unknown as GraphCatalog;

function node(id: string, kind: string, pos: [number, number], data: Record<string, unknown> = {}): FlowNode {
  return { id, kind, pos, data };
}

function graph(nodes: FlowNode[]): FlowGraph {
  return { version: 1, name: "t", variables: [], nodes, edges: [], view: { pan: [0, 0], zoom: 1 } };
}

/** A comment at the origin holding one print node, with another print outside it. */
function boxed(moveMode = "group"): FlowGraph {
  return graph([
    node("c1", "comment", [0, 0], { title: "Stage", width: 600, height: 400, move_mode: moveMode }),
    node("n1", "print", [60, 80]),
    node("n2", "print", [900, 80]),
  ]);
}

function booted(doc: FlowGraph): EditorState {
  let state = reduce(initialState(), { type: "catalog", catalog: CATALOG });
  state = reduce(state, { type: "document", graph: doc, fileName: "t.flow.json" });
  return state;
}

function apply(state: EditorState, ...actions: Action[]): EditorState {
  return actions.reduce(reduce, state);
}

const layoutsOf = (doc: FlowGraph) => layoutGraph(doc, indexCatalog(CATALOG));

test("a comment is laid out at its own size, with no pins", () => {
  const layout = layoutsOf(boxed()).get("c1")!;
  assert.equal(layout.width, 600);
  assert.equal(layout.height, 400);
  assert.deepEqual([layout.inputs.length, layout.outputs.length], [0, 0]);
});

test("a size a hand-edited file got wrong is held to the bounds", () => {
  const doc = graph([node("c1", "comment", [0, 0], { width: 3, height: "tall" })]);
  const layout = layoutsOf(doc).get("c1")!;
  assert.equal(layout.width, COMMENT_MIN);
  assert.equal(layout.height, 240);
});

test("dragging a comment carries what lies inside it", () => {
  assert.deepEqual(movedBy(layoutsOf(boxed()), ["c1"]).sort(), ["c1", "n1"]);
});

test("a comment set to move alone carries nothing", () => {
  assert.deepEqual(movedBy(layoutsOf(boxed("comment")), ["c1"]), ["c1"]);
});

test("a nested comment and its nodes ride along with the outer one", () => {
  const doc = graph([
    node("c1", "comment", [0, 0], { width: 900, height: 600 }),
    node("c2", "comment", [40, 60], { width: 400, height: 300 }),
    node("n1", "print", [80, 140]),
  ]);
  assert.deepEqual(movedBy(layoutsOf(doc), ["c1"]).sort(), ["c1", "c2", "n1"]);
});

test("a node only partly inside is left where it is", () => {
  const doc = boxed();
  doc.nodes[1].pos = [500, 80];
  assert.deepEqual(movedBy(layoutsOf(doc), ["c1"]), ["c1"]);
});

test("a marquee selects a comment only when it encloses the whole box", () => {
  const layouts = layoutsOf(boxed());
  assert.deepEqual(nodesInRect(layouts, { minX: 40, minY: 40, maxX: 400, maxY: 300 }), ["n1"]);
  assert.deepEqual(
    nodesInRect(layouts, { minX: -10, minY: -10, maxX: 700, maxY: 500 }).sort(),
    ["c1", "n1"]
  );
});

test("a comment drawn around nodes leaves room for its title", () => {
  const box = commentAround(layoutsOf(boxed()), ["n1", "n2"])!;
  assert.ok(box.pos[0] < 60 && box.pos[1] < 80 - 30);
  const n2 = layoutsOf(boxed()).get("n2")!;
  assert.ok(box.pos[0] + box.width >= 900 + n2.width);
});

test("a comment drawn around a node with an open note clears the bubble", () => {
  const plain = commentAround(layoutsOf(boxed()), ["n2"])!;
  const doc = boxed();
  doc.nodes[2].data = { note: "a reason this node is here", note_shown: true };
  const noted = commentAround(layoutsOf(doc), ["n2"])!;
  assert.ok(noted.pos[1] <= plain.pos[1] - 40, `${noted.pos[1]} vs ${plain.pos[1]}`);
  doc.nodes[2].data.note_shown = false;
  assert.deepEqual(commentAround(layoutsOf(doc), ["n2"])!.pos, plain.pos);
});

test("resizing from the left or the top moves the box with the edge", () => {
  const start = { x: 0, y: 0, width: 400, height: 300 };
  assert.deepEqual(resized(start, "nw", 100, 50), { x: 100, y: 50, width: 300, height: 250 });
  assert.deepEqual(resized(start, "se", 40, -20), { x: 0, y: 0, width: 440, height: 280 });
  // Never smaller than the minimum, and the far edge stays put.
  assert.deepEqual(resized(start, "w", 1000, 0), { x: 400 - COMMENT_MIN, y: 0, width: COMMENT_MIN, height: 300 });
});

test("C wraps the selection in a comment, in one undo step, with its title open", () => {
  let state = booted(boxed());
  state = apply(state, { type: "select", ids: ["n2"] }, { type: "wrapInComment", ids: ["n2"] });

  const comment = state.graph.nodes[0];
  assert.equal(comment.kind, "comment");
  assert.deepEqual(state.selection, [comment.id]);
  assert.deepEqual(state.editing, { id: comment.id, what: "title" });
  assert.deepEqual(movedBy(layoutsOf(state.graph), [comment.id]).sort(), [comment.id, "n2"].sort());

  state = reduce(state, { type: "undo" });
  assert.equal(state.graph.nodes.some((item) => item.id === comment.id), false);
});

test("a resize is one undo step, snapped and bounded", () => {
  let state = booted(boxed());
  state = reduce(state, { type: "resizeNode", id: "c1", pos: [13, 7], width: 20, height: 503 });
  const comment = state.graph.nodes.find((item) => item.id === "c1")!;
  assert.deepEqual(comment.pos, [10, 10]);
  assert.equal(comment.data.width, COMMENT_MIN);
  assert.equal(comment.data.height, 500);
  assert.equal(reduce(state, { type: "undo" }).graph.nodes[0].data.width, 600);
});

test("a note and its bubble change together, and clearing it removes both keys", () => {
  let state = booted(boxed());
  state = reduce(state, { type: "patchNodeData", id: "n1", patch: { note: "why", note_shown: true } });
  assert.deepEqual(state.graph.nodes[1].data, { note: "why", note_shown: true });
  assert.equal(state.undo.length, 1);

  state = reduce(state, { type: "patchNodeData", id: "n1", patch: { note: undefined, note_shown: undefined } });
  assert.deepEqual(state.graph.nodes[1].data, {});
});

test("long note text wraps at word boundaries and is cut after the last line", () => {
  assert.deepEqual(wrapText("one two three four", 9, 5), ["one two", "three", "four"]);
  assert.deepEqual(wrapText("a\nb", 9, 5), ["a", "b"]);
  const cut = wrapText("x ".repeat(40), 5, 2);
  assert.equal(cut.length, 2);
  assert.ok(cut[1].endsWith("…"));
});
