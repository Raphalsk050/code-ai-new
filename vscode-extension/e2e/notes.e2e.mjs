// Comment boxes and note bubbles on the graph editor (webview/flow/notes.tsx),
// driven with the mouse and keyboard the way Unreal's are.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const CATALOG = JSON.parse(readFileSync(join(root, "e2e", "fixtures", "catalog.json"), "utf8"));
// What the agent's write_graph makes of a review loop (tests/unit/test_graph_authoring.py).
const AGENT_WRITTEN = JSON.parse(
  readFileSync(join(root, "e2e", "fixtures", "agent-written.flow.json"), "utf8")
);

function node(id, kind, pos, data = {}) {
  return { id, kind, pos, data };
}

/**
 * A comment holding Start and a Print, with an End outside it, all inside the
 * 800px of canvas left of the inspector. Zoom 1, so screen px are graph px.
 */
function doc(extra = {}) {
  return {
    version: 1,
    name: "notes",
    variables: [],
    nodes: [
      node("c1", "comment", [0, 0], { title: "Setup", color: "#2563eb", width: 520, height: 320 }),
      node("n1", "start", [30, 80]),
      node("n2", "print", [270, 80], { label: "hello" }),
      node("n3", "end", [560, 80]),
    ],
    edges: [
      { id: "e1", kind: "exec", from: ["n1", "out"], to: ["n2", "in"] },
      { id: "e2", kind: "exec", from: ["n2", "out"], to: ["n3", "in"] },
    ],
    view: { pan: [40, 120], zoom: 1 },
    ...extra,
  };
}

async function boot(ui, graph = doc()) {
  await ui.fromHost({ type: "flowCatalog", catalog: CATALOG });
  await ui.fromHost({ type: "flowDocument", graph, fileName: "notes.flow.json", readOnly: false });
  await ui.fromHost({ type: "flowValidation", ok: true, issues: [] });
  await ui.page.waitForSelector('[data-testid="flow-canvas"] g.flow-node');
  await ui.clearToHost();
}

async function box(ui, selector) {
  const found = await ui.page.locator(selector).first().boundingBox();
  if (!found) throw new Error(`no box for ${selector}`);
  return found;
}

async function centre(ui, selector) {
  const found = await box(ui, selector);
  return { x: found.x + found.width / 2, y: found.y + found.height / 2 };
}

async function drag(ui, from, to) {
  await ui.page.mouse.move(from.x, from.y);
  await ui.page.mouse.down();
  await ui.page.mouse.move((from.x + to.x) / 2, (from.y + to.y) / 2, { steps: 4 });
  await ui.page.mouse.move(to.x, to.y, { steps: 4 });
  await ui.page.mouse.up();
}

async function lastGraph(ui) {
  const edits = await ui.toHost("flowEdit");
  if (edits.length === 0) throw new Error("the panel sent no flowEdit");
  return edits[edits.length - 1].graph;
}

const at = (graph, id) => graph.nodes.find((item) => item.id === id);

function overlap(a, b) {
  return a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height;
}

export const tests = {
  async "a graph the agent wrote opens with nothing drawn over anything"(ui, assert) {
    await boot(ui, AGENT_WRITTEN);
    await ui.page.click('[data-testid="frame"]');
    const ids = AGENT_WRITTEN.nodes.filter((item) => item.kind !== "comment").map((item) => item.id);
    const boxes = {};
    for (const id of ids) boxes[id] = await box(ui, `[data-testid="node-${id}"] .flow-node-box`);
    for (let i = 0; i < ids.length; i += 1) {
      for (let j = i + 1; j < ids.length; j += 1) {
        assert(!overlap(boxes[ids[i]], boxes[ids[j]]), `${ids[i]} and ${ids[j]} overlap`);
      }
    }
    const comment = AGENT_WRITTEN.nodes.find((item) => item.kind === "comment");
    const frame = await box(ui, `[data-testid="comment-${comment.id}"] .flow-comment-body`);
    for (const id of ["plan", "llm"]) {
      const inner = boxes[id];
      assert(
        inner.x > frame.x && inner.y > frame.y &&
          inner.x + inner.width < frame.x + frame.width && inner.y + inner.height < frame.y + frame.height,
        `${id} is not inside the comment drawn around it`
      );
    }
    await ui.page.waitForSelector('[data-testid="note-plan"]');
    await ui.shot("notes-04-agent-written");
  },


  async "a comment is drawn behind the nodes it holds"(ui, assert) {
    await boot(ui);
    const order = await ui.page.evaluate(() =>
      Array.from(document.querySelectorAll("[data-node-id]")).map((el) => el.getAttribute("data-node-id"))
    );
    assert(order[0] === "c1", `the comment is not painted first: ${order.join(", ")}`);
    const title = await ui.page.locator('[data-testid="comment-c1"] .flow-comment-title').textContent();
    assert(title.includes("Setup"), `the title reads ${JSON.stringify(title)}`);
    await ui.shot("notes-01-comment");
  },

  async "dragging a comment by its title moves what it holds, once"(ui, assert) {
    await boot(ui);
    const from = await centre(ui, '[data-testid="comment-header-c1"]');
    await drag(ui, from, { x: from.x + 100, y: from.y + 60 });

    const edits = await ui.toHost("flowEdit");
    assert(edits.length === 1, `the drag wrote the document ${edits.length} times`);
    const graph = edits[0].graph;
    assert(at(graph, "c1").pos.join() === "100,60", `the comment is at ${at(graph, "c1").pos}`);
    assert(at(graph, "n1").pos.join() === "130,140", `Start is at ${at(graph, "n1").pos}`);
    assert(at(graph, "n2").pos.join() === "370,140", `Print is at ${at(graph, "n2").pos}`);
    assert(at(graph, "n3").pos.join() === "560,80", "the node outside moved too");
  },

  async "a comment set to move alone leaves its nodes"(ui, assert) {
    const graph = doc();
    graph.nodes[0].data.move_mode = "comment";
    await boot(ui, graph);
    const from = await centre(ui, '[data-testid="comment-header-c1"]');
    await drag(ui, from, { x: from.x + 100, y: from.y });

    const sent = await lastGraph(ui);
    assert(at(sent, "c1").pos.join() === "100,0", `the comment is at ${at(sent, "c1").pos}`);
    assert(at(sent, "n1").pos.join() === "30,80", "a node moved with a comment set to move alone");
  },

  async "a comment resizes from its corner and its left edge"(ui, assert) {
    await boot(ui);
    const corner = await centre(ui, '[data-testid="comment-c1"] [data-testid="comment-handle-se"]');
    await drag(ui, corner, { x: corner.x + 120, y: corner.y + 80 });
    let sent = await lastGraph(ui);
    assert(at(sent, "c1").data.width === 640, `width is ${at(sent, "c1").data.width}`);
    assert(at(sent, "c1").data.height === 400, `height is ${at(sent, "c1").data.height}`);

    await ui.clearToHost();
    const left = await centre(ui, '[data-testid="comment-c1"] [data-testid="comment-handle-w"]');
    await drag(ui, left, { x: left.x + 40, y: left.y });
    sent = await lastGraph(ui);
    assert(at(sent, "c1").pos[0] === 40, `the left edge is at ${at(sent, "c1").pos[0]}`);
    assert(at(sent, "c1").data.width === 600, `width is ${at(sent, "c1").data.width}`);
  },

  async "a marquee inside a comment picks its nodes, not the comment"(ui, assert) {
    await boot(ui);
    const start = await box(ui, '[data-testid="node-n1"] .flow-node-box');
    const print = await box(ui, '[data-testid="node-n2"] .flow-node-box');
    await drag(ui, { x: start.x - 15, y: start.y - 15 }, { x: print.x + 30, y: print.y + 30 });

    const count = await ui.page.locator('[data-testid="selection-count"]').textContent();
    assert(count.includes("2"), `the selection is ${count}`);
    const comment = await ui.page.locator('[data-testid="comment-c1"]').getAttribute("class");
    assert(!comment.includes("selected"), "the comment was selected by a marquee inside it");
  },

  async "C wraps the selection in a comment whose title is ready to type"(ui, assert) {
    await boot(ui, doc({ nodes: doc().nodes.slice(1) }));
    await ui.page.click('[data-testid="node-n3"] .flow-node-box');
    await ui.page.keyboard.press("c");
    await ui.page.waitForSelector('[data-testid="comment-edit"]');
    await ui.page.keyboard.press("Control+A");
    await ui.page.keyboard.type("Wrap up");
    await ui.page.keyboard.press("Enter");

    const sent = await lastGraph(ui);
    const comment = sent.nodes.find((item) => item.kind === "comment");
    assert(comment, "no comment was created");
    assert(comment.data.title === "Wrap up", `the title is ${JSON.stringify(comment.data.title)}`);
    const end = at(sent, "n3");
    assert(
      comment.pos[0] < end.pos[0] && comment.pos[1] < end.pos[1] && comment.pos[0] + comment.data.width > end.pos[0] + 200,
      "the comment does not surround the node"
    );
    await ui.shot("notes-02-wrapped");
  },

  async "double-clicking a title edits it, and Escape leaves it as it was"(ui, assert) {
    await boot(ui);
    await ui.page.dblclick('[data-testid="comment-header-c1"]');
    await ui.page.waitForSelector('[data-testid="comment-edit"]');
    await ui.page.keyboard.type(" changed");
    await ui.page.keyboard.press("Escape");
    assert((await ui.toHost("flowEdit")).length === 0, "Escape wrote the edit");

    await ui.page.dblclick('[data-testid="comment-header-c1"]');
    await ui.page.keyboard.press("End");
    await ui.page.keyboard.press("Shift+Enter");
    await ui.page.keyboard.type("second line");
    await ui.page.keyboard.press("Enter");
    const sent = await lastGraph(ui);
    assert(at(sent, "c1").data.title === "Setup\nsecond line", `title is ${JSON.stringify(at(sent, "c1").data.title)}`);
  },

  async "the inspector recolours a comment"(ui, assert) {
    await boot(ui);
    await ui.page.click('[data-testid="comment-header-c1"]');
    await ui.page.click('[data-testid="swatch-16a34a"]');
    const sent = await lastGraph(ui);
    assert(at(sent, "c1").data.color === "#16a34a", `colour is ${at(sent, "c1").data.color}`);
    const fill = await ui.page.locator('[data-testid="comment-c1"] .flow-comment-body').getAttribute("fill");
    assert(fill === "#16a34a", `the box is filled ${fill}`);
  },

  async "a note goes on a node from its menu and shows as a bubble"(ui, assert) {
    await boot(ui);
    const title = await centre(ui, '[data-testid="node-n2"] .flow-node-title');
    await ui.page.mouse.click(title.x, title.y, { button: "right" });
    await ui.page.click('[data-testid="menu-edit-note"]');
    await ui.page.waitForSelector('[data-testid="note-edit"]');
    await ui.page.keyboard.type("Prints the greeting for the log");
    await ui.page.keyboard.press("Enter");

    let sent = await lastGraph(ui);
    assert(at(sent, "n2").data.note === "Prints the greeting for the log", "the note was not saved");
    assert(at(sent, "n2").data.note_shown === true, "the bubble is not open");
    const bubble = await box(ui, '[data-testid="note-n2"]');
    const nodeBox = await box(ui, '[data-testid="node-n2"] .flow-node-box');
    assert(bubble.y + bubble.height <= nodeBox.y + 2, "the bubble is not above the node");
    await ui.shot("notes-03-bubble");

    // Hidden, it leaves a mark that brings it back.
    await ui.page.click('[data-testid="note-hide-n2"]');
    sent = await lastGraph(ui);
    assert(at(sent, "n2").data.note_shown === false, "the bubble did not close");
    assert((await ui.page.locator('[data-testid="note-n2"]').count()) === 0, "the bubble is still drawn");
    await ui.page.click('[data-testid="note-show-n2"]');
    await ui.page.waitForSelector('[data-testid="note-n2"]');
  },

  async "a note follows its node and clearing the text removes it"(ui, assert) {
    const graph = doc();
    graph.nodes[3].data = { note: "the answer", note_shown: true };
    await boot(ui, graph);
    const before = await box(ui, '[data-testid="note-n3"]');
    const title = await centre(ui, '[data-testid="node-n3"] .flow-node-title');
    await drag(ui, title, { x: title.x + 60, y: title.y + 40 });
    const after = await box(ui, '[data-testid="note-n3"]');
    assert(Math.abs(after.x - before.x - 60) < 2 && Math.abs(after.y - before.y - 40) < 2, "the bubble stayed behind");

    await ui.page.click('[data-testid="node-n3"] .flow-node-box');
    await ui.page.fill('[data-testid="field-note"]', "");
    const sent = await lastGraph(ui);
    assert(!("note" in at(sent, "n3").data) && !("note_shown" in at(sent, "n3").data), "the note keys stayed");
  },

  async "the comment kind is offered under Notes in the add menu"(ui, assert) {
    await boot(ui);
    await ui.page.mouse.click(500, 700, { button: "right" });
    await ui.page.waitForSelector('[data-testid="context-menu"]');
    await ui.page.click('[data-testid="menu-kind-comment"]');
    const sent = await lastGraph(ui);
    const added = sent.nodes.filter((item) => item.kind === "comment");
    assert(added.length === 2, `there are ${added.length} comments`);
    assert(added[1].data.width === 400 && added[1].data.title === "Comment", "the new comment has no defaults");
  },
};
