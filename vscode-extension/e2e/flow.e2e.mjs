// The orchestrator-graph editor, in a real browser, against a fake host.
//
// Every scenario drives the panel exactly as the extension does: the catalog
// and the document arrive as postMessage, and what the panel sends back is read
// off `window.__toHost`. Screenshots land in e2e/shots/ - a canvas is the one
// thing assertions cannot fully describe.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const CATALOG = JSON.parse(
  readFileSync(join(root, "e2e", "fixtures", "catalog.json"), "utf8")
);
// The example that ships with the repo, opened exactly as a user would open it.
const EXAMPLE = JSON.parse(
  readFileSync(join(root, "..", "examples", "flows", "revisao-em-tres-voltas.flow.json"), "utf8")
);

function node(id, kind, pos, data = {}) {
  return { id, kind, pos, data };
}

function wire(id, from, to, kind = "exec") {
  return { id, kind, from, to };
}

function graph(nodes, edges, extra = {}) {
  return {
    version: 1,
    name: "pipeline",
    variables: [],
    nodes,
    edges,
    view: { pan: [60, 40], zoom: 0.8 },
    ...extra,
  };
}

/** Start -> architect -> writer -> print -> end, with a shared Model node. */
function pipeline() {
  return graph(
    [
      node("n1", "start", [0, 120]),
      node("n2", "model", [0, 320], {
        model: "qwen3.6-35b",
        base_url: "http://localhost:11434/v1",
        api_key_ref: "session",
      }),
      node("n3", "profession", [270, 120], {
        profession: "architect",
        task: "design the importer",
      }),
      node("n4", "profession", [540, 120], { profession: "code_writer" }),
      node("n5", "print", [810, 120], { label: "result" }),
      node("n6", "end", [1060, 120]),
    ],
    [
      wire("e1", ["n1", "out"], ["n3", "in"]),
      wire("e2", ["n2", "model"], ["n3", "model"], "data"),
      wire("e3", ["n3", "out"], ["n4", "in"]),
      wire("e4", ["n3", "report"], ["n4", "task"], "data"),
      wire("e5", ["n2", "model"], ["n4", "model"], "data"),
      wire("e6", ["n4", "out"], ["n5", "in"]),
      wire("e7", ["n4", "report"], ["n5", "value"], "data"),
      wire("e8", ["n5", "out"], ["n6", "in"]),
    ],
    {
      variables: [
        { id: "v1", name: "ticket", type: "string", default: "ABC-1" },
        { id: "v2", name: "voltas", type: "int", default: 3 },
        { id: "v3", name: "aprovado", type: "bool", default: false },
      ],
    }
  );
}

async function boot(ui, doc = pipeline(), { ok = true, issues = [] } = {}) {
  await ui.fromHost({ type: "flowCatalog", catalog: CATALOG });
  await ui.fromHost({ type: "flowDocument", graph: doc, fileName: "pipeline.flow.json", readOnly: false });
  await ui.fromHost({ type: "flowValidation", ok, issues });
  await ui.page.waitForSelector('[data-testid="flow-canvas"] g.flow-node, .flow-empty');
  await ui.clearToHost();
  return doc;
}

/** The centre of an element, in page coordinates. */
async function centre(ui, selector) {
  const box = await ui.page.locator(selector).first().boundingBox();
  if (!box) throw new Error(`no box for ${selector}`);
  return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
}

async function dragPins(ui, fromSelector, toSelector) {
  const a = await centre(ui, fromSelector);
  const b = await centre(ui, toSelector);
  await ui.page.mouse.move(a.x, a.y);
  await ui.page.mouse.down();
  await ui.page.mouse.move((a.x + b.x) / 2, (a.y + b.y) / 2, { steps: 4 });
  await ui.page.mouse.move(b.x, b.y, { steps: 4 });
  await ui.page.mouse.up();
}

async function lastGraph(ui) {
  const edits = await ui.toHost("flowEdit");
  if (edits.length === 0) throw new Error("the panel sent no flowEdit");
  return edits[edits.length - 1].graph;
}

/** Right-click somewhere on the canvas and wait for the menu. */
async function openCanvasMenu(ui, at = { x: 600, y: 460 }) {
  await ui.page.mouse.click(at.x, at.y, { button: "right" });
  await ui.page.waitForSelector('[data-testid="context-menu"]');
}

/** Right-click a node, near its top-left corner, and wait for its menu. */
async function openNodeMenu(ui, nodeId) {
  // A copy pasted over it is offset down and right: the corner stays its own.
  const box = await ui.page.locator(`[data-testid="node-${nodeId}"] .flow-node-box`).first().boundingBox();
  await ui.page.mouse.click(box.x + 8, box.y + 8, { button: "right" });
  await ui.page.waitForSelector('[data-testid="context-menu"]');
}

/** Drag a wire off a pin and release over empty canvas. */
async function dragPinToEmpty(ui, pinSelector, to) {
  const from = await centre(ui, pinSelector);
  await ui.page.mouse.move(from.x, from.y);
  await ui.page.mouse.down();
  await ui.page.mouse.move(to.x, to.y, { steps: 6 });
  await ui.page.mouse.up();
  await ui.page.waitForSelector('[data-testid="context-menu"]');
}

/** Two professions the user wrote, talking to each other across a loop. */
function conversation() {
  return graph(
    [
      node("n1", "start", [0, 140]),
      node("n2", "custom_profession", [0, 330], {
        name: "Ana",
        role: "You are Ana. You argue for shipping the smallest thing that works.",
        can_read: true,
        can_write: false,
        can_run: false,
        can_browse: false,
        framing: false,
        max_steps: 20,
      }),
      node("n3", "custom_profession", [0, 560], {
        name: "Bruno",
        role: "You are Bruno. You argue for the version that will still hold in a year.",
        can_read: true,
        framing: false,
        max_steps: 20,
      }),
      node("n4", "loop", [260, 140], { kind: "count", count: 3 }),
      node("n5", "var_get", [260, 380], { variable: "transcript" }),
      node("n6", "profession", [520, 140], {}),
      node("n7", "var_set", [790, 140], {
        variable: "transcript",
        mode: "append",
        prefix: "Ana:",
      }),
      node("n8", "profession", [1040, 140], {}),
      node("n9", "var_set", [1310, 140], {
        variable: "transcript",
        mode: "append",
        prefix: "Bruno:",
      }),
      node("n10", "end", [1310, 380]),
    ],
    [
      wire("e1", ["n1", "out"], ["n4", "in"]),
      wire("e2", ["n4", "body"], ["n6", "in"]),
      wire("e3", ["n2", "profession"], ["n6", "profession"], "data"),
      wire("e4", ["n5", "value"], ["n6", "task"], "data"),
      wire("e5", ["n6", "out"], ["n7", "in"]),
      wire("e6", ["n6", "report"], ["n7", "value"], "data"),
      wire("e7", ["n7", "out"], ["n8", "in"]),
      wire("e8", ["n3", "profession"], ["n8", "profession"], "data"),
      wire("e9", ["n5", "value"], ["n8", "task"], "data"),
      wire("e10", ["n8", "out"], ["n9", "in"]),
      wire("e11", ["n8", "report"], ["n9", "value"], "data"),
      wire("e12", ["n4", "completed"], ["n10", "in"]),
    ],
    { variables: [{ id: "v1", name: "transcript", type: "string", default: "" }] }
  );
}

/** The camera as the canvas draws it, read off its transform. */
async function viewOf(ui) {
  const transform = await ui.page.evaluate(() =>
    document.querySelector('[data-testid="flow-canvas"] > g').getAttribute("transform")
  );
  const [, x, y, zoom] = transform.match(/translate\(([-\d.e]+), ([-\d.e]+)\) scale\(([-\d.e]+)\)/);
  return { pan: [Number(x), Number(y)], zoom: Number(zoom) };
}

export const tests = {
  // 0 -----------------------------------------------------------------
  // At the width an editor tab really has, with the run half-finished: the
  // one scenario whose point is what it looks like.
  "the editor reads well at editor width, mid-run": async (ui, assert) => {
    await ui.page.setViewportSize({ width: 1600, height: 950 });
    await boot(ui);
    await ui.page.click('[data-testid="frame"]');

    await ui.fromHost({ type: "flowRunState", running: true, runId: "run-1", message: "" });
    await ui.event("graph.run.started", { run_id: "run-1", name: "pipeline", node_count: 6 });
    await ui.event("graph.node.started", { run_id: "run-1", node_id: "n1", kind: "start", index: 1 });
    await ui.event("graph.node.completed", { run_id: "run-1", node_id: "n1", status: "completed" });
    await ui.event("graph.edge.active", { run_id: "run-1", edge_id: "e1", kind: "exec" });
    await ui.event("graph.node.started", {
      run_id: "run-1",
      node_id: "n3",
      kind: "profession",
      index: 2,
    });
    await ui.event("graph.agent.started", {
      run_id: "run-1",
      node_id: "n3",
      agent_name: "Turing",
      profession: "architect",
      model: "qwen3.6-35b",
      writes: false,
    });
    await ui.event("graph.node.progress", {
      run_id: "run-1",
      node_id: "n3",
      event: "tool.call.started",
      detail: "grep_search(importer)",
    });
    await ui.event("graph.node.completed", { run_id: "run-1", node_id: "n3", status: "completed" });
    await ui.event("graph.edge.active", { run_id: "run-1", edge_id: "e3", kind: "exec" });
    await ui.event("graph.edge.active", { run_id: "run-1", edge_id: "e4", kind: "data" });
    await ui.event("graph.node.started", {
      run_id: "run-1",
      node_id: "n4",
      kind: "profession",
      index: 3,
    });
    await ui.event("graph.agent.started", {
      run_id: "run-1",
      node_id: "n4",
      agent_name: "Lovelace",
      profession: "code_writer",
      model: "qwen3.6-35b",
      writes: true,
    });
    await ui.event("graph.print", {
      run_id: "run-1",
      node_id: "n5",
      label: "plan",
      text: "three modules: reader, validator, writer",
    });

    await ui.page.waitForSelector('[data-testid="dot-e3"]');
    await ui.shot("00-hero-running");
    assert(
      (await ui.page.locator('[data-testid="node-n4"]').getAttribute("data-status")) === "running",
      "the second agent is not marked as running"
    );
  },

  // 1 -----------------------------------------------------------------
  "draws the graph it was given": async (ui, assert) => {
    await boot(ui);
    await ui.page.click('[data-testid="frame"]');

    const nodes = await ui.page.locator("g.flow-node").count();
    const wires = await ui.page.locator("path.flow-wire").count();
    assert(nodes === 6, `expected 6 nodes, drew ${nodes}`);
    assert(wires === 8, `expected 8 wires, drew ${wires}`);

    const title = await ui.page.locator('[data-testid="node-n3"] .flow-node-title').textContent();
    assert(title === "Agent", `agent node title was ${title}`);
    const summary = await ui.page
      .locator('[data-testid="node-n3"] .flow-node-summary')
      .textContent();
    assert(summary === "architect", `summary was ${summary}`);
    await ui.shot("01-pipeline");
  },

  // 2 -----------------------------------------------------------------
  "the right-click menu adds a node where it was opened": async (ui, assert) => {
    await boot(ui);
    await openCanvasMenu(ui, { x: 600, y: 460 });
    await ui.shot("02-context-menu");

    await ui.page.click('[data-testid="menu-kind-loop"]');
    assert(
      (await ui.page.locator('[data-testid="context-menu"]').count()) === 0,
      "the menu stayed open after a pick"
    );

    const graphSent = await lastGraph(ui);
    const added = graphSent.nodes.find((item) => item.kind === "loop");
    assert(Boolean(added), "no loop node reached the document");
    assert(added.data.kind === "count", `loop defaults were ${JSON.stringify(added.data)}`);
    assert(graphSent.nodes.length === 7, `document has ${graphSent.nodes.length} nodes`);

    // The new node is selected, so the inspector is already on it.
    const inspectorTitle = await ui.page
      .locator(".flow-inspector .flow-section-title span")
      .first()
      .textContent();
    assert(inspectorTitle === "Loop", `inspector showed ${inspectorTitle}`);
    await ui.shot("02b-added-loop");
  },

  // 2b ----------------------------------------------------------------
  "the menu filters as you type and Enter picks the first match": async (ui, assert) => {
    await boot(ui);
    await openCanvasMenu(ui);

    await ui.page.fill('[data-testid="menu-search"]', "print");
    const rows = await ui.page.locator(".flow-menu-row").allTextContents();
    assert(rows.length === 1, `filter left ${rows.length} rows: ${JSON.stringify(rows)}`);

    await ui.page.press('[data-testid="menu-search"]', "Enter");
    const sent = await lastGraph(ui);
    assert(
      sent.nodes.filter((item) => item.kind === "print").length === 2,
      "Enter did not create the filtered node"
    );
  },

  // 2c ----------------------------------------------------------------
  "the menu creates a variable together with the node that reads it": async (ui, assert) => {
    await boot(ui, graph([node("n1", "start", [0, 0])], []));
    await openCanvasMenu(ui);

    await ui.page.fill('[data-testid="menu-search"]', "new");
    await ui.shot("02c-menu-variables");
    await ui.page.click('[data-testid="menu-new-var-int"]');

    const sent = await lastGraph(ui);
    assert(sent.variables.length === 1, `got ${sent.variables.length} variables`);
    assert(sent.variables[0].type === "int", `type was ${sent.variables[0].type}`);
    const reader = sent.nodes.find((item) => item.kind === "var_get");
    assert(Boolean(reader), "no Get node was created");
    assert(
      reader.data.variable === sent.variables[0].name,
      `the node reads ${reader.data.variable}`
    );

    // One gesture, one undo.
    await ui.page.click('[data-testid="undo"]');
    const undone = await lastGraph(ui);
    assert(undone.variables.length === 0 && undone.nodes.length === 1, "undo left half of it");
  },

  // 3 -----------------------------------------------------------------
  "only one Start node is allowed": async (ui, assert) => {
    await boot(ui);
    await openCanvasMenu(ui);

    await ui.page.click('[data-testid="menu-kind-start"]');
    const notice = await ui.page.locator('[data-testid="notice"]').textContent();
    assert(notice.includes("Start"), `notice was ${notice}`);
    const edits = await ui.toHost("flowEdit");
    assert(edits.length === 0, "a second Start reached the document");
  },

  // 3b ----------------------------------------------------------------
  "dropping a wire on empty canvas offers only what that pin accepts": async (ui, assert) => {
    await boot(ui);
    await ui.page.click('[data-testid="frame"]');

    // The architect's `report` is a string: it fits a task, a print, a Set
    // string variable - and never an exec pin.
    // Framed, the graph keeps clear of the canvas's edges.
    const canvas = await ui.page.locator('[data-testid="flow-canvas"]').boundingBox();
    await dragPinToEmpty(ui, '[data-testid="node-n3"] [data-testid="pin-out-report"]', {
      x: canvas.x + 24,
      y: canvas.y + canvas.height - 24,
    });
    await ui.shot("03b-pin-menu");

    const labels = await ui.page.locator(".flow-menu-row .label").allTextContents();
    assert(labels.length > 0, "the pin menu was empty for a string output");
    assert(
      labels.every((label) => !label.includes(" - in")),
      `an exec pin was offered: ${JSON.stringify(labels)}`
    );
    assert(
      labels.some((label) => label.includes("Print")),
      `Print was not offered: ${JSON.stringify(labels)}`
    );

    await ui.page.click('[data-testid="menu-wire-print-value"]');
    const sent = await lastGraph(ui);
    const created = sent.nodes.find((item) => item.kind === "print" && item.id !== "n5");
    assert(Boolean(created), "the node was not created");
    assert(
      sent.edges.some(
        (edge) =>
          edge.kind === "data" &&
          edge.from[0] === "n3" &&
          edge.from[1] === "report" &&
          edge.to[0] === created.id &&
          edge.to[1] === "value"
      ),
      "the new node was not wired to the pin the drag came from"
    );
  },

  // 3c ----------------------------------------------------------------
  "an exec output only offers exec inputs": async (ui, assert) => {
    await boot(ui);
    await ui.page.click('[data-testid="frame"]');

    await dragPinToEmpty(ui, '[data-testid="node-n1"] [data-testid="pin-out-out"]', {
      x: 400,
      y: 640,
    });

    const ids = await ui.page.locator(".flow-menu-row").evaluateAll((rows) =>
      rows.map((row) => row.getAttribute("data-testid"))
    );
    assert(ids.length > 0, "an exec output offered nothing");
    assert(
      ids.every((id) => id.endsWith("-in")),
      `a data pin was offered to an exec wire: ${JSON.stringify(ids)}`
    );
  },

  // 3d ----------------------------------------------------------------
  "a pin nothing accepts opens an empty menu": async (ui, assert) => {
    // A Model output only fits a `model` pin, and a graph whose kinds are all
    // taken by max_instances still has to answer the gesture.
    await boot(ui, graph([node("n1", "model", [0, 0], { model: "m" })], []));

    await dragPinToEmpty(ui, '[data-testid="node-n1"] [data-testid="pin-out-model"]', {
      x: 500,
      y: 500,
    });
    await ui.page.fill('[data-testid="menu-search"]', "zzz-nada");

    assert(
      await ui.page.locator('[data-testid="context-menu"]').isVisible(),
      "the menu did not open"
    );
    assert(
      (await ui.page.locator(".flow-menu-row").count()) === 0,
      "the menu listed something for an impossible filter"
    );
    await ui.shot("03d-pin-menu-empty");
  },

  // 3e ----------------------------------------------------------------
  "a node's own menu duplicates and deletes it": async (ui, assert) => {
    await boot(ui);
    await ui.page.click('[data-testid="frame"]');

    await openNodeMenu(ui, "n5");
    await ui.shot("03e-node-menu");
    await ui.page.click('[data-testid="menu-duplicate"]');
    let sent = await lastGraph(ui);
    assert(
      sent.nodes.filter((item) => item.kind === "print").length === 2,
      "the node was not duplicated"
    );

    await openNodeMenu(ui, "n5");
    await ui.page.click('[data-testid="menu-delete-node"]');
    sent = await lastGraph(ui);
    assert(
      !sent.nodes.some((item) => item.id === "n5"),
      "the node survived its own delete entry"
    );
  },

  // 4 -----------------------------------------------------------------
  "dragging between pins creates a wire": async (ui, assert) => {
    const doc = graph(
      [node("n1", "start", [-60, 0]), node("n2", "print", [160, 0], { label: "x" })],
      []
    );
    await boot(ui, doc);

    await dragPins(
      ui,
      '[data-testid="node-n1"] [data-testid="pin-out-out"]',
      '[data-testid="node-n2"] [data-testid="pin-in-in"]'
    );

    const sent = await lastGraph(ui);
    assert(sent.edges.length === 1, `expected 1 edge, got ${sent.edges.length}`);
    assert(sent.edges[0].kind === "exec", `edge kind was ${sent.edges[0].kind}`);
    assert(sent.edges[0].from[0] === "n1" && sent.edges[0].to[0] === "n2", "wrong endpoints");
    await ui.shot("04-connected");
  },

  // 5 -----------------------------------------------------------------
  "an exec pin cannot be wired to a data pin": async (ui, assert) => {
    const doc = graph(
      [node("n1", "start", [-60, 0]), node("n2", "print", [160, 0], { label: "x" })],
      []
    );
    await boot(ui, doc);

    await dragPins(
      ui,
      '[data-testid="node-n1"] [data-testid="pin-out-out"]',
      '[data-testid="node-n2"] [data-testid="pin-in-value"]'
    );

    const notice = await ui.page.locator('[data-testid="notice"]').textContent();
    assert(notice.includes("exec pin"), `notice was ${notice}`);
    const edits = await ui.toHost("flowEdit");
    assert(edits.length === 0, "an illegal wire reached the document");
    await ui.shot("05-illegal-wire");
  },

  // 6 -----------------------------------------------------------------
  "the inspector edits a node field": async (ui, assert) => {
    await boot(ui);

    await ui.page.click('[data-testid="node-n4"] .flow-node-box');
    await ui.page.selectOption('[data-testid="field-profession"]', "documenter");

    const sent = await lastGraph(ui);
    const edited = sent.nodes.find((item) => item.id === "n4");
    assert(edited.data.profession === "documenter", `profession is ${edited.data.profession}`);

    const summary = await ui.page
      .locator('[data-testid="node-n4"] .flow-node-summary')
      .textContent();
    assert(summary === "documenter", `node summary is ${summary}`);
    await ui.shot("06-inspector-node");
  },

  // 7 -----------------------------------------------------------------
  "variables are added with the + buttons and coloured by type": async (ui, assert) => {
    await boot(ui, graph([node("n1", "start", [0, 0])], []));

    await ui.page.click('[data-testid="tab-variables"]');
    await ui.page.click('[data-testid="add-var-string"]');
    await ui.page.click('[data-testid="add-var-int"]');
    await ui.page.click('[data-testid="add-var-bool"]');

    const sent = await lastGraph(ui);
    assert(sent.variables.length === 3, `got ${sent.variables.length} variables`);
    assert(
      sent.variables.map((item) => item.type).join(",") === "string,int,bool",
      `types were ${sent.variables.map((item) => item.type)}`
    );
    // Distinct colours per type is the whole point of the swatches.
    const colours = await ui.page.$$eval(".flow-var-row .swatch", (items) =>
      items.map((item) => item.style.background)
    );
    assert(new Set(colours).size === 3, `swatches were ${colours.join(" | ")}`);
    await ui.shot("07-variables");
  },

  // 8 -----------------------------------------------------------------
  "renaming a variable follows it into the nodes that read it": async (ui, assert) => {
    const doc = graph([node("n1", "start", [0, 0]), node("n2", "var_get", [200, 0], { variable: "ticket" })], [], {
      variables: [{ id: "v1", name: "ticket", type: "string", default: "" }],
    });
    await boot(ui, doc);

    await ui.page.click('[data-testid="tab-variables"]');
    await ui.page.fill('[data-testid="var-name-v1"]', "chamado");

    const sent = await lastGraph(ui);
    assert(sent.variables[0].name === "chamado", "the variable was not renamed");
    const reader = sent.nodes.find((item) => item.id === "n2");
    assert(reader.data.variable === "chamado", `node still reads ${reader.data.variable}`);
  },

  // 9 -----------------------------------------------------------------
  "undo and redo restore the graph exactly": async (ui, assert) => {
    const doc = await boot(ui);
    const before = JSON.stringify(doc);

    await openCanvasMenu(ui);
    await ui.page.click('[data-testid="menu-kind-decision"]');
    await ui.page.click('[data-testid="undo"]');
    const undone = JSON.stringify(await lastGraph(ui));
    assert(undone === before, "undo did not restore the original graph");

    await ui.page.click('[data-testid="redo"]');
    const redone = await lastGraph(ui);
    assert(
      redone.nodes.some((item) => item.kind === "decision"),
      "redo did not bring the node back"
    );
    await ui.shot("09-undo-redo");
  },

  // 10 ----------------------------------------------------------------
  "copy and paste duplicates a subgraph with fresh ids": async (ui, assert) => {
    await boot(ui);

    await ui.page.click('[data-testid="node-n3"] .flow-node-box');
    // The paste is read from the browser's own paste event, which only the
    // system's shortcut raises: Cmd on a Mac, Ctrl everywhere else.
    await ui.page.keyboard.down("ControlOrMeta");
    await ui.page.keyboard.press("KeyC");
    // Whether the system clipboard took the copy is known a moment later, and
    // a browser under test may refuse it: paste once that is settled, as a
    // hand does, so the copy is found on the clipboard or in memory.
    await ui.page.waitForTimeout(100);
    await ui.page.keyboard.press("KeyV");
    await ui.page.keyboard.up("ControlOrMeta");

    const sent = await lastGraph(ui);
    const agents = sent.nodes.filter((item) => item.kind === "profession");
    assert(agents.length === 3, `expected 3 agent nodes, got ${agents.length}`);
    const ids = new Set(sent.nodes.map((item) => item.id));
    assert(ids.size === sent.nodes.length, "pasted nodes reused an id");
    const copy = sent.nodes.find((item) => !["n1", "n2", "n3", "n4", "n5", "n6"].includes(item.id));
    assert(copy.pos[0] !== 270 || copy.pos[1] !== 120, "the copy landed exactly on the original");
    await ui.shot("10-pasted");
  },

  // 11 ----------------------------------------------------------------
  "Delete removes the selection and its wires": async (ui, assert) => {
    await boot(ui);

    await ui.page.click('[data-testid="node-n4"] .flow-node-box');
    await ui.page.keyboard.press("Delete");

    const sent = await lastGraph(ui);
    assert(sent.nodes.length === 5, `expected 5 nodes, got ${sent.nodes.length}`);
    assert(
      sent.edges.every((edge) => edge.from[0] !== "n4" && edge.to[0] !== "n4"),
      "an edge survived the node it was attached to"
    );
  },

  // 12 ----------------------------------------------------------------
  "validation errors mark the node and block Play": async (ui, assert) => {
    await boot(ui, pipeline(), {
      ok: false,
      issues: [
        {
          message: "Agent needs task: wire it or fill it in.",
          node_id: "n4",
          pin: "task",
          field: "",
          severity: "error",
        },
        {
          message: "Nothing reads this node.",
          node_id: "n2",
          pin: "",
          field: "",
          severity: "warning",
        },
      ],
    });

    const play = ui.page.locator('[data-testid="play"]');
    assert(await play.isDisabled(), "Play is enabled with errors on the graph");
    const marked = await ui.page.locator('[data-testid="node-n4"]').getAttribute("class");
    assert(marked.includes("error"), `node classes were ${marked}`);
    const status = await ui.page.locator('[data-testid="run-status"]').textContent();
    assert(status.includes("1 error"), `status was ${status}`);
    await ui.shot("12-validation-errors");
  },

  // 13 ----------------------------------------------------------------
  "Play sends the graph with the toolbar's variable overrides": async (ui, assert) => {
    await boot(ui);

    await ui.page.click('[data-testid="tab-variables"]');
    await ui.page.fill('[data-testid="override-ticket"]', "XYZ-99");
    await ui.page.click('[data-testid="play"]');

    const runs = await ui.toHost("flowRun");
    assert(runs.length === 1, `expected one run request, got ${runs.length}`);
    assert(runs[0].variables.ticket === "XYZ-99", `variables were ${JSON.stringify(runs[0].variables)}`);
    assert(runs[0].variables.voltas === undefined, "an untouched variable was sent as an override");
    await ui.shot("13-play");
  },

  // 14 ----------------------------------------------------------------
  "a run lights the wires, marks the nodes and fills the log": async (ui, assert) => {
    await boot(ui);
    await ui.page.click('[data-testid="frame"]');
    await ui.fromHost({ type: "flowRunState", running: true, runId: "run-1", message: "" });
    await ui.event("graph.run.started", { run_id: "run-1", name: "pipeline", node_count: 6 });
    await ui.event("graph.node.started", { run_id: "run-1", node_id: "n1", kind: "start", index: 1 });
    await ui.event("graph.node.completed", { run_id: "run-1", node_id: "n1", status: "completed" });
    await ui.event("graph.edge.active", { run_id: "run-1", edge_id: "e1", kind: "exec" });
    await ui.event("graph.node.started", { run_id: "run-1", node_id: "n3", kind: "profession", index: 2 });
    await ui.event("graph.agent.started", {
      run_id: "run-1",
      node_id: "n3",
      agent_name: "Turing",
      profession: "architect",
      model: "qwen3.6-35b",
      writes: false,
    });
    await ui.event("graph.node.progress", {
      run_id: "run-1",
      node_id: "n3",
      event: "tool.call.started",
      detail: "read_file",
    });
    await ui.event("graph.print", { run_id: "run-1", node_id: "n5", label: "result", text: "three modules" });

    await ui.page.waitForSelector('[data-testid="dot-e1"]');
    const running = await ui.page.locator('[data-testid="node-n3"]').getAttribute("data-status");
    assert(running === "running", `n3 status was ${running}`);
    const done = await ui.page.locator('[data-testid="node-n1"]').getAttribute("data-status");
    assert(done === "done", `n1 status was ${done}`);
    const badge = await ui.page.locator('[data-testid="node-n3"] .flow-badge').textContent();
    assert(badge === "Turing", `badge was ${badge}`);
    const rows = await ui.page.locator('[data-testid="log-row"]').allTextContents();
    assert(
      rows.some((row) => row.includes("three modules")),
      `log rows were ${JSON.stringify(rows)}`
    );
    // The stop button replaces Play while a run is live.
    assert(await ui.page.locator('[data-testid="stop"]').isVisible(), "Stop is not shown");
    await ui.shot("14-running");
  },

  // 15 ----------------------------------------------------------------
  "a loop node counts its laps on the badge": async (ui, assert) => {
    const doc = graph(
      [
        node("n1", "start", [-300, 0]),
        node("n2", "loop", [0, 0], { kind: "count", count: 3 }),
        node("n3", "print", [300, 0], { label: "lap" }),
      ],
      [wire("e1", ["n1", "out"], ["n2", "in"]), wire("e2", ["n2", "body"], ["n3", "in"])]
    );
    await boot(ui, doc);

    await ui.event("graph.run.started", { run_id: "r", name: "l", node_count: 3 });
    await ui.event("graph.loop.iteration", { run_id: "r", node_id: "n2", index: 1, item: "" });
    const badge = await ui.page.locator('[data-testid="node-n2"] .flow-badge').textContent();
    assert(badge === "#2", `loop badge was ${badge}`);

    await ui.event("graph.loop.capped", { run_id: "r", node_id: "n2", cap: 50 });
    const rows = await ui.page.locator('[data-testid="log-row"]').allTextContents();
    assert(rows.some((row) => row.includes("cap of 50")), "the cap was not logged");
    await ui.shot("15-loop");
  },

  // 16 ----------------------------------------------------------------
  "the user-chat card takes an answer and posts it": async (ui, assert) => {
    const doc = graph(
      [
        node("n1", "start", [-300, 0]),
        node("n2", "user_chat", [0, 0], { title: "which module?" }),
        node("n3", "end", [300, 0]),
      ],
      [wire("e1", ["n1", "out"], ["n2", "in"]), wire("e2", ["n2", "out"], ["n3", "in"])]
    );
    await boot(ui, doc);
    await ui.fromHost({ type: "flowRunState", running: true, runId: "run-9", message: "" });
    await ui.event("graph.user_chat.requested", {
      run_id: "run-9",
      node_id: "n2",
      call_id: "c1",
      prompt: "Which module should I touch?",
    });

    await ui.page.waitForSelector('[data-testid="question"]');
    await ui.shot("16-question");
    await ui.page.fill('[data-testid="question-input"]', "the importer");
    await ui.page.click('[data-testid="question-send"]');

    const answers = await ui.toHost("flowAnswer");
    assert(answers.length === 1, `expected one answer, got ${answers.length}`);
    assert(answers[0].callId === "c1", `answered call ${answers[0].callId}`);
    assert(answers[0].text === "the importer", `answered ${answers[0].text}`);

    await ui.event("graph.user_chat.answered", { run_id: "run-9", node_id: "n2", call_id: "c1" });
    await ui.page.waitForSelector('[data-testid="question"]', { state: "detached" });
  },

  // 17 ----------------------------------------------------------------
  "the model node lists the models of its own endpoint": async (ui, assert) => {
    await boot(ui);

    await ui.page.click('[data-testid="frame"]');
    await ui.page.click('[data-testid="node-n2"] .flow-node-box');
    await ui.page.click('[data-testid="list-models"]');

    const asked = await ui.toHost("flowListModels");
    assert(asked.length === 1, "the panel did not ask for the models");
    assert(asked[0].baseUrl === "http://localhost:11434/v1", `asked ${asked[0].baseUrl}`);
    assert(asked[0].nodeId === "n2", `asked for node ${asked[0].nodeId}`);

    await ui.fromHost({
      type: "flowModels",
      nodeId: "n2",
      ok: true,
      models: ["qwen3.6-35b", "llama4:70b", "gemma4:31b"],
      message: "",
    });
    const count = await ui.page.locator('[data-testid="models-count"]').textContent();
    assert(count.includes("3 models"), `counter said ${count}`);
    await ui.shot("17-model-node");
  },

  // 18 ----------------------------------------------------------------
  "a Sequence grows and shrinks its pins": async (ui, assert) => {
    const doc = graph([node("n1", "sequence", [0, 0], { outputs: 2 })], []);
    await boot(ui, doc);

    await ui.page.click('[data-testid="node-n1"] .flow-node-box');
    assert(
      (await ui.page.locator('[data-testid="node-n1"] [data-direction="out"]').count()) === 2,
      "a fresh Sequence did not have two pins"
    );

    await ui.page.click('[data-testid="seq-add"]');
    await ui.page.click('[data-testid="seq-add"]');
    assert(
      (await ui.page.locator('[data-testid="node-n1"] [data-direction="out"]').count()) === 4,
      "the pins did not grow"
    );

    await ui.page.click('[data-testid="seq-remove"]');
    assert(
      (await ui.page.locator('[data-testid="node-n1"] [data-direction="out"]').count()) === 3,
      "the pins did not shrink"
    );
    await ui.shot("18-sequence");
  },

  // 19 ----------------------------------------------------------------
  "panning and zooming move the view without touching the nodes": async (ui, assert) => {
    const doc = await boot(ui);
    const before = await centre(ui, '[data-testid="node-n3"] .flow-node-box');

    await ui.page.keyboard.down("Control");
    await ui.page.mouse.move(500, 500);
    await ui.page.mouse.down({ button: "left" });
    await ui.page.mouse.move(560, 540, { steps: 4 });
    await ui.page.mouse.up({ button: "left" });
    await ui.page.keyboard.up("Control");

    const after = await centre(ui, '[data-testid="node-n3"] .flow-node-box');
    assert(Math.abs(after.x - before.x - 60) < 6, `panned to ${after.x - before.x}px`);

    const sentAll = await ui.toHost("flowEdit");
    const positionsChanged = sentAll.some((edit) =>
      edit.graph.nodes.some((item, index) => {
        const original = doc.nodes[index];
        return original && (item.pos[0] !== original.pos[0] || item.pos[1] !== original.pos[1]);
      })
    );
    assert(!positionsChanged, "panning moved the nodes");
    await ui.shot("19-panned");
  },

  // 20 ----------------------------------------------------------------
  "dragging a node writes its new position once": async (ui, assert) => {
    await boot(ui);
    const from = await centre(ui, '[data-testid="node-n5"] .flow-node-title');

    await ui.page.mouse.move(from.x, from.y);
    await ui.page.mouse.down();
    await ui.page.mouse.move(from.x + 80, from.y + 60, { steps: 6 });
    await ui.page.mouse.up();

    const edits = await ui.toHost("flowEdit");
    assert(edits.length === 1, `a drag wrote the document ${edits.length} times`);
    const moved = edits[0].graph.nodes.find((item) => item.id === "n5");
    // 80px right and 60px down at zoom 0.8, snapped to the 10px grid.
    assert(moved.pos[0] === 910 && moved.pos[1] === 200, `node landed at ${moved.pos}`);
  },

  // 21 ----------------------------------------------------------------
  "the layout survives a phone-width viewport": async (ui, assert) => {
    await boot(ui);
    await ui.page.click('[data-testid="frame"]');

    for (const [name, size] of [
      ["wide", { width: 1440, height: 900 }],
      ["tablet", { width: 820, height: 800 }],
      ["narrow", { width: 420, height: 760 }],
    ]) {
      await ui.page.setViewportSize(size);
      await ui.page.waitForTimeout(120);
      const scrollWidth = await ui.page.evaluate(() => document.body.scrollWidth);
      assert(
        scrollWidth <= size.width + 1,
        `${name}: the page scrolls sideways (${scrollWidth} > ${size.width})`
      );
      assert(
        await ui.page.locator('[data-testid="play"]').isVisible(),
        `${name}: the Play button is not visible`
      );
      assert(
        await ui.page.locator('[data-testid="inspector"]').isVisible(),
        `${name}: the inspector is not visible`
      );
      assert(
        await ui.page.locator('[data-testid="flow-canvas"]').isVisible(),
        `${name}: the canvas is not visible`
      );
      await ui.shot(`21-${name}`);
    }

    // At phone width the inspector goes under the canvas instead of squeezing it.
    const box = await ui.page.locator('[data-testid="inspector"]').boundingBox();
    const canvas = await ui.page.locator('[data-testid="flow-canvas"]').boundingBox();
    assert(box.y > canvas.y, "the inspector stayed beside the canvas at 420px");
  },

  // 22 ----------------------------------------------------------------
  "a finished run reports how it ended": async (ui, assert) => {
    await boot(ui);
    await ui.fromHost({ type: "flowRunState", running: true, runId: "run-2", message: "" });
    await ui.event("graph.run.started", { run_id: "run-2", name: "pipeline", node_count: 6 });
    await ui.event("graph.run.completed", {
      run_id: "run-2",
      status: "completed",
      seconds: 12.5,
      nodes_executed: 6,
      reason: "reached End",
      variables: { ticket: "ABC-1" },
    });
    await ui.fromHost({
      type: "flowRunState",
      running: false,
      runId: "run-2",
      message: "reached End",
    });

    assert(await ui.page.locator('[data-testid="play"]').isVisible(), "Play did not come back");
    const rows = await ui.page.locator('[data-testid="log-row"]').allTextContents();
    assert(rows.some((row) => row.includes("12.5s")), `log was ${JSON.stringify(rows)}`);

    await ui.page.click('[data-testid="tab-run"]');
    const value = await ui.page.locator('[data-testid="run-var-ticket"]').textContent();
    assert(value === "ABC-1", `run variable showed ${value}`);
    await ui.shot("22-finished");
  },

  // 23 ----------------------------------------------------------------
  "a failed node is marked and its error logged": async (ui, assert) => {
    await boot(ui);
    await ui.event("graph.run.started", { run_id: "r", name: "p", node_count: 6 });
    await ui.event("graph.node.started", { run_id: "r", node_id: "n3", kind: "profession", index: 1 });
    await ui.event("graph.node.failed", { run_id: "r", node_id: "n3", error: "the model refused" });

    const status = await ui.page.locator('[data-testid="node-n3"]').getAttribute("data-status");
    assert(status === "failed", `status was ${status}`);
    const rows = await ui.page.locator(".flow-log-row.error .text").allTextContents();
    assert(rows.some((row) => row.includes("the model refused")), "the error was not logged");
    await ui.shot("23-failed");
  },

  // 24 ----------------------------------------------------------------
  "Ctrl+S asks the host to save": async (ui, assert) => {
    await boot(ui);

    await ui.page.click('[data-testid="save"]');
    await ui.page.keyboard.press("Control+s");

    const saves = await ui.toHost("flowSave");
    assert(saves.length === 2, `expected two save requests, got ${saves.length}`);
  },

  // 26 ----------------------------------------------------------------
  "Escape closes the menu without creating anything": async (ui, assert) => {
    await boot(ui);
    await openCanvasMenu(ui);

    await ui.page.press('[data-testid="menu-search"]', "Escape");
    await ui.page.waitForSelector('[data-testid="context-menu"]', { state: "detached" });
    assert((await ui.toHost("flowEdit")).length === 0, "Escape created a node");
  },

  // 27 ----------------------------------------------------------------
  "clicking elsewhere closes the menu": async (ui, assert) => {
    await boot(ui);
    await openCanvasMenu(ui, { x: 400, y: 300 });

    await ui.page.mouse.click(200, 600);
    await ui.page.waitForSelector('[data-testid="context-menu"]', { state: "detached" });
    assert((await ui.toHost("flowEdit")).length === 0, "the dismissal created a node");
  },

  // 28 ----------------------------------------------------------------
  "a menu opened at the bottom edge stays on screen": async (ui, assert) => {
    await boot(ui);
    const size = ui.page.viewportSize();
    // The bottom-right corner *of the canvas*: below it is the log panel, and
    // a right-click there is not a canvas gesture at all.
    const canvas = await ui.page.locator('[data-testid="flow-canvas"]').boundingBox();
    await openCanvasMenu(ui, {
      x: canvas.x + canvas.width - 12,
      y: canvas.y + canvas.height - 8,
    });

    const box = await ui.page.locator('[data-testid="context-menu"]').boundingBox();
    assert(box.x + box.width <= size.width + 1, `menu ran off the right edge (${box.x + box.width})`);
    assert(box.y + box.height <= size.height + 1, `menu ran off the bottom (${box.y + box.height})`);
    await ui.shot("28-menu-edge");
  },

  // 29 ----------------------------------------------------------------
  "arrow keys walk the menu and Enter takes the highlighted row": async (ui, assert) => {
    await boot(ui, graph([node("n1", "start", [0, 0])], []));
    await openCanvasMenu(ui);

    await ui.page.press('[data-testid="menu-search"]', "ArrowDown");
    await ui.page.press('[data-testid="menu-search"]', "ArrowDown");
    const active = await ui.page.locator(".flow-menu-row.active").getAttribute("data-testid");
    await ui.page.press('[data-testid="menu-search"]', "Enter");

    const sent = await lastGraph(ui);
    assert(sent.nodes.length === 2, "Enter created nothing");
    assert(
      active.includes(sent.nodes[1].kind) || active.startsWith("menu-kind-"),
      `the highlighted row was ${active} but ${sent.nodes[1].kind} was created`
    );
  },

  // 30 ----------------------------------------------------------------
  "zooming keeps the point under the cursor still": async (ui, assert) => {
    await boot(ui);
    const before = await centre(ui, '[data-testid="node-n3"] .flow-node-box');

    await ui.page.mouse.move(before.x, before.y);
    await ui.page.mouse.wheel(0, -120);
    await ui.page.waitForTimeout(60);

    const after = await centre(ui, '[data-testid="node-n3"] .flow-node-box');
    // The node under the pointer may drift a little (its centre is not the
    // cursor), but it must not fly off across the canvas.
    assert(
      Math.abs(after.x - before.x) < 40 && Math.abs(after.y - before.y) < 40,
      `the node jumped ${Math.round(after.x - before.x)},${Math.round(after.y - before.y)}px`
    );
    await ui.shot("30-zoomed");
  },

  // 31 ----------------------------------------------------------------
  "a marquee selects everything it covers": async (ui, assert) => {
    await boot(ui);
    await ui.page.click('[data-testid="frame"]');

    const canvas = await ui.page.locator('[data-testid="flow-canvas"]').boundingBox();
    await ui.page.mouse.move(canvas.x + 8, canvas.y + 8);
    await ui.page.mouse.down();
    await ui.page.mouse.move(canvas.x + canvas.width - 8, canvas.y + canvas.height - 8, {
      steps: 8,
    });
    await ui.page.mouse.up();

    const selected = await ui.page.locator("g.flow-node.selected").count();
    assert(selected === 6, `the marquee selected ${selected} of 6 nodes`);
    // The inspector talks about the selection, not about whichever node
    // happens to be first in it.
    const count = await ui.page.locator('[data-testid="selection-count"]').textContent();
    assert(count.startsWith("6"), `the inspector said ${count}`);
    await ui.shot("31-marquee");

    // And Delete then clears the whole graph in one go.
    await ui.page.keyboard.press("Delete");
    const sent = await lastGraph(ui);
    assert(sent.nodes.length === 0 && sent.edges.length === 0, "the selection survived Delete");
  },

  // 32 ----------------------------------------------------------------
  "an edge can be selected and deleted from its own menu": async (ui, assert) => {
    await boot(ui);
    await ui.page.click('[data-testid="frame"]');

    const path = await ui.page.locator('[data-testid="edge-e1"] .flow-wire-hit').boundingBox();
    // The hit area is a thick stroke along the curve; its box centre is close
    // enough for a wire this short.
    await ui.page.mouse.click(path.x + path.width / 2, path.y + path.height / 2, {
      button: "right",
    });
    await ui.page.waitForSelector('[data-testid="context-menu"]');
    await ui.page.click('[data-testid="menu-delete-edge"]');

    const sent = await lastGraph(ui);
    assert(!sent.edges.some((edge) => edge.id === "e1"), "the wire survived its delete entry");
  },

  // 33 ----------------------------------------------------------------
  "the log follows the tail and can be cleared": async (ui, assert) => {
    await boot(ui);
    await ui.event("graph.run.started", { run_id: "r", name: "p", node_count: 1 });
    for (let index = 0; index < 40; index += 1) {
      await ui.event("graph.print", { run_id: "r", node_id: "n5", text: `linha ${index}` });
    }

    const count = await ui.page.locator('[data-testid="log-row"]').count();
    assert(count === 40, `the log shows ${count} rows`);
    const scrolled = await ui.page.locator(".flow-log-rows").evaluate(
      (element) => element.scrollTop + element.clientHeight >= element.scrollHeight - 4
    );
    assert(scrolled, "the log did not follow the tail");

    await ui.page.click('[data-testid="log-clear"]');
    assert(
      (await ui.page.locator('[data-testid="log-row"]').count()) === 0,
      "clearing left rows behind"
    );
  },

  // 34 ----------------------------------------------------------------
  "a secret reference offers to store the key in the vault": async (ui, assert) => {
    await boot(ui);
    await ui.page.click('[data-testid="frame"]');
    await ui.page.click('[data-testid="node-n2"] .flow-node-box');

    await ui.page.fill('[data-testid="field-api_key_ref"]', "secret:trabalho");
    await ui.page.click('[data-testid="store-secret"]');

    const asked = await ui.toHost("flowStoreSecret");
    assert(asked.length === 1, "the panel did not ask the host to store the key");
    assert(asked[0].name === "trabalho", `it asked for ${asked[0].name}`);
    // The reference is all the document ever holds.
    const sent = await lastGraph(ui);
    const model = sent.nodes.find((item) => item.id === "n2");
    assert(model.data.api_key_ref === "secret:trabalho", "the reference was not saved");
    assert(!JSON.stringify(sent).includes("sk-"), "something key-shaped reached the document");
  },

  // 35 ----------------------------------------------------------------
  "the run panel shows the agents and what they are doing": async (ui, assert) => {
    await boot(ui);
    await ui.event("graph.run.started", { run_id: "r", name: "p", node_count: 6 });
    await ui.event("graph.node.started", { run_id: "r", node_id: "n3", kind: "profession" });
    await ui.event("graph.agent.started", {
      run_id: "r",
      node_id: "n3",
      agent_name: "Turing",
      profession: "architect",
      model: "qwen3.6-35b",
      writes: false,
    });
    await ui.event("graph.node.progress", {
      run_id: "r",
      node_id: "n3",
      event: "tool.call.started",
      detail: "read_file(src/app.ts)",
    });

    await ui.page.click('[data-testid="tab-run"]');
    const text = await ui.page.locator(".flow-inspector-body").textContent();
    assert(text.includes("Turing"), "the agent is not listed");
    assert(text.includes("read_file"), "what the agent is doing is not shown");
    await ui.shot("35-run-panel");
  },

  // 36 ----------------------------------------------------------------
  "the example that ships with the repo opens and reads well": async (ui, assert) => {
    await ui.page.setViewportSize({ width: 1600, height: 950 });
    await boot(ui, EXAMPLE);
    await ui.page.click('[data-testid="frame"]');

    const nodes = await ui.page.locator("g.flow-node").count();
    const wires = await ui.page.locator("path.flow-wire").count();
    assert(nodes === EXAMPLE.nodes.length, `drew ${nodes} of ${EXAMPLE.nodes.length} nodes`);
    assert(wires === EXAMPLE.edges.length, `drew ${wires} of ${EXAMPLE.edges.length} wires`);

    // Nothing spills out of the canvas once it is framed.
    const canvas = await ui.page.locator('[data-testid="flow-canvas"]').boundingBox();
    const boxes = await ui.page.locator("g.flow-node .flow-node-box").evaluateAll((items) =>
      items.map((item) => item.getBoundingClientRect())
    );
    for (const box of boxes) {
      assert(
        box.left >= canvas.x - 1 && box.right <= canvas.x + canvas.width + 1,
        "a node sits outside the canvas after framing"
      );
    }
    await ui.shot("36-example");
  },

  // 25 ----------------------------------------------------------------
  "an unreadable file is reported instead of a blank canvas": async (ui, assert) => {
    await ui.fromHost({ type: "flowCatalog", catalog: CATALOG });
    await ui.fromHost({
      type: "flowNotice",
      level: "error",
      text: "This file is not valid JSON: Unexpected token }",
    });

    const notice = await ui.page.locator('[data-testid="notice"]').textContent();
    assert(notice.includes("JSON"), `notice was ${notice}`);
    assert(
      await ui.page.locator('[data-testid="flow-canvas"]').isVisible(),
      "the canvas disappeared with the bad file"
    );
    await ui.shot("25-bad-file");
  },

  // -------------------------------------------------------------------
  // A profession written in the editor, wired into two agents that talk.
  "two professions written in the editor drive a conversation": async (ui, assert) => {
    await ui.page.setViewportSize({ width: 1600, height: 950 });
    await boot(ui, conversation());

    // The Profession node shows the name it was given, not its kind.
    const ana = await ui.page.locator('[data-testid="node-n2"] .flow-node-summary').textContent();
    assert(ana === "Ana", `the Profession node reads ${JSON.stringify(ana)}`);

    // And the Agent stops asking for one, because a wire answered.
    const agent = await ui.page.locator('[data-testid="node-n6"] .flow-node-summary').textContent();
    assert(
      agent === "wired profession",
      `the Agent still asks for a profession: ${JSON.stringify(agent)}`
    );

  },

  // -------------------------------------------------------------------
  // The feedback a long agent owes: a spinner, the tool it is in, the clock.
  "a working agent says what it is doing on the node": async (ui, assert) => {
    await ui.page.setViewportSize({ width: 1600, height: 950 });
    await boot(ui, conversation());
    await ui.fromHost({ type: "flowRunState", running: true, runId: "run-9", message: "" });
    await ui.event("graph.run.started", { run_id: "run-9", name: "conversation", node_count: 10 });
    await ui.event("graph.node.started", { run_id: "run-9", node_id: "n6", kind: "profession", index: 1 });
    await ui.event("graph.agent.started", {
      run_id: "run-9",
      node_id: "n6",
      agent_name: "Turing",
      profession: "Ana",
      model: "qwen3.6-35b",
      writes: false,
    });
    for (const detail of ["read_file(store.ts)", "grep_search(transcript)"]) {
      await ui.event("graph.node.progress", {
        run_id: "run-9",
        node_id: "n6",
        event: "tool.call.started",
        detail,
      });
    }

    await ui.page.waitForSelector('[data-testid="activity-n6"]');
    // The node is opening on its spring: until it is wide, the line is cut short.
    await ui.page
      .waitForFunction(() => document.querySelector('[data-testid="activity-n6"]')?.textContent.includes("step 2"), null, { timeout: 2000 })
      .catch(() => {});
    const line = await ui.page.locator('[data-testid="activity-n6"]').textContent();
    // The tool it is in, and a step count that moved twice.
    assert(line.includes("grep_search"), `no tool on the node: ${JSON.stringify(line)}`);
    assert(line.includes("step 2"), `the step counter did not move: ${JSON.stringify(line)}`);
    assert(
      (await ui.page.locator('[data-testid="node-n6"] .flow-spinner-arc').count()) === 1,
      "the working node has no spinner"
    );
    // An idle node must not claim to be working.
    assert(
      (await ui.page.locator('[data-testid="activity-n8"]').count()) === 0,
      "an idle node is showing activity"
    );

  },
  // -------------------------------------------------------------------
  // Generic agents: the switches follow the profession until set, and a
  // Tools node wired into `tools` is the whole grant.
  "an agent's switches follow its profession until one is set": async (ui, assert) => {
    await ui.page.setViewportSize({ width: 1400, height: 950 });
    await boot(ui);
    await ui.page.click('[data-testid="node-n3"] .flow-node-box');
    await ui.page.waitForSelector('[data-testid="permissions"]');

    // The architect reads and browses, and nothing on the node says otherwise.
    assert(await ui.page.isChecked('[data-testid="field-can_read"]'), "read is off");
    assert(!(await ui.page.isChecked('[data-testid="field-can_write"]')), "write is on");

    await ui.page.click('[data-testid="permissions"] .flow-switch-row:nth-child(2) .flow-switch');
    let sent = await lastGraph(ui);
    let agent = sent.nodes.find((item) => item.id === "n3");
    assert(agent.data.can_write === true, `can_write is ${agent.data.can_write}`);
    await ui.page.waitForSelector('[data-testid="reset-can_write"]');
    await ui.shot("40-agent-permissions");

    await ui.page.click('[data-testid="reset-can_write"]');
    sent = await lastGraph(ui);
    agent = sent.nodes.find((item) => item.id === "n3");
    assert(!("can_write" in agent.data), `can_write stayed: ${JSON.stringify(agent.data)}`);
  },

  "tools nodes wire into an agent and stack": async (ui, assert) => {
    await ui.page.setViewportSize({ width: 1400, height: 950 });
    const doc = graph(
      [
        node("n1", "start", [0, 0]),
        node("n2", "profession", [300, 0], { profession: "code_writer", task: "t" }),
        node("n3", "tools", [0, 220], { name: "read-only", tools: ["read_file"] }),
        node("n4", "tools", [0, 380], { tools: [] }),
        node("n5", "end", [600, 0]),
      ],
      [wire("e1", ["n1", "out"], ["n2", "in"]), wire("e2", ["n2", "out"], ["n5", "in"])]
    );
    await boot(ui, doc);

    // Pick tools in the second node through its picker.
    await ui.page.click('[data-testid="node-n4"] .flow-node-box');
    await ui.page.fill('[data-testid="search-tools"]', "web_search");
    await ui.page.check('[data-testid="tool-tools-web_search"]');
    await ui.page.fill('[data-testid="search-tools"]', "");
    await ui.page.click('[data-testid="group-all-tools-edit"]');
    let sent = await lastGraph(ui);
    const picked = sent.nodes.find((item) => item.id === "n4").data.tools;
    assert(picked.includes("web_search"), `web_search not picked: ${JSON.stringify(picked)}`);
    assert(picked.includes("write_file"), `the edit group was not added: ${JSON.stringify(picked)}`);
    await ui.shot("41-tools-picker");

    for (const source of ["n3", "n4"]) {
      await dragPins(
        ui,
        `[data-testid="node-${source}"] [data-testid="pin-out-tools"]`,
        '[data-testid="node-n2"] [data-testid="pin-in-tools"]'
      );
    }
    sent = await lastGraph(ui);
    const wires = sent.edges.filter((edge) => edge.to[0] === "n2" && edge.to[1] === "tools");
    assert(wires.length === 2, `the tools pin holds ${wires.length} wires`);

    await ui.page.click('[data-testid="node-n2"] .flow-node-box');
    await ui.page.waitForSelector('[data-testid="permissions-locked"]');
    assert(
      await ui.page.isDisabled('[data-testid="field-can_run"]'),
      "the switches still work while tools are wired"
    );
    const granted = await ui.page.locator('[data-testid="granted-toggle"]').textContent();
    assert(granted.includes(String(picked.length + 1)), `granted says ${granted}`);
    await ui.page.click('[data-testid="granted-toggle"]');
    await ui.shot("42-agent-with-wired-tools");
  },
  // -------------------------------------------------------------------
  "the toolbar zooms around the middle and resets to 100%": async (ui, assert) => {
    await ui.page.setViewportSize({ width: 1400, height: 950 });
    await boot(ui);

    await ui.page.click('[data-testid="zoom-in"]');
    let view = await viewOf(ui);
    assert(Math.abs(view.zoom - 0.96) < 0.001, `zoom in gave ${view.zoom}`);
    await ui.page.click('[data-testid="zoom-reset"]');
    view = await viewOf(ui);
    assert(view.zoom === 1, `reset gave ${view.zoom}`);
    // The camera is not an edit: it reaches the file with the save, not before.
    assert((await ui.toHost("flowEdit")).length === 0, "zooming wrote the document");
    await ui.page.keyboard.press("Control+s");
    const saved = await lastGraph(ui);
    assert(saved.view.zoom === 1, `the save carried zoom ${saved.view.zoom}`);
    const label = await ui.page.locator('[data-testid="zoom-reset"]').textContent();
    assert(label === "100%", `the zoom label reads ${label}`);
    await ui.shot("43-toolbar");
  },

  "the minimap centres the view where it is clicked": async (ui, assert) => {
    await ui.page.setViewportSize({ width: 1400, height: 950 });
    await boot(ui);
    // The map is there all along, the whole graph fitting on screen or not.
    await ui.page.waitForSelector('[data-testid="minimap"]');
    const before = (await viewOf(ui)).pan;
    const box = await ui.page.locator('[data-testid="minimap"]').boundingBox();
    await ui.page.mouse.click(box.x + box.width - 12, box.y + box.height - 12);

    const after = (await viewOf(ui)).pan;
    assert(
      after[0] !== before[0] || after[1] !== before[1],
      `the view did not move: ${JSON.stringify(after)}`
    );
    await ui.shot("45-minimap");
    await ui.page.click('[data-testid="minimap-toggle"]');
    assert(
      (await ui.page.locator('[data-testid="minimap"]').count()) === 0,
      "the minimap stayed after being switched off"
    );
  },

  "the inspector folds an agent's limits until asked": async (ui, assert) => {
    await ui.page.setViewportSize({ width: 1400, height: 950 });
    await boot(ui);
    await ui.page.click('[data-testid="node-n3"] .flow-node-box');

    assert(
      (await ui.page.locator('[data-testid="field-max_steps"]').count()) === 0,
      "the limits are open from the start"
    );
    await ui.page.click('[data-testid="section-toggle-Limits"]');
    await ui.page.fill('[data-testid="field-max_steps"]', "12");
    const sent = await lastGraph(ui);
    const agent = sent.nodes.find((item) => item.id === "n3");
    assert(agent.data.max_steps === 12, `max_steps is ${agent.data.max_steps}`);

    // Blocking a tool starts from a short button, not a 50-row checklist.
    assert(
      (await ui.page.locator('[data-testid="search-blocked_tools"]').count()) === 0,
      "the block list opened on its own"
    );
    await ui.page.click('[data-testid="picker-toggle-blocked_tools"]');
    await ui.page.fill('[data-testid="search-blocked_tools"]', "web_search");
    await ui.page.check('[data-testid="tool-blocked_tools-web_search"]');
    const blocked = (await lastGraph(ui)).nodes.find((item) => item.id === "n3").data.blocked_tools;
    assert(
      JSON.stringify(blocked) === '["web_search"]',
      `blocked_tools is ${JSON.stringify(blocked)}`
    );
    await ui.shot("44-inspector-sections");
  },
  // -------------------------------------------------------------------
  "pasting text into a field never pastes nodes": async (ui, assert) => {
    await boot(ui);
    // Something is on the node clipboard, so a stray paste would show.
    await ui.page.click('[data-testid="node-n3"] .flow-node-box');
    await ui.page.keyboard.down("Control");
    await ui.page.keyboard.press("KeyC");
    await ui.page.keyboard.up("Control");
    await ui.clearToHost();

    await ui.page.click('[data-testid="field-role_override"]');
    const pasted = await ui.page.evaluate(() => {
      const field = document.querySelector('[data-testid="field-role_override"]');
      const data = new DataTransfer();
      data.setData("text/plain", "Voce responde perguntas de uma ia");
      const event = new ClipboardEvent("paste", {
        clipboardData: data,
        bubbles: true,
        cancelable: true,
      });
      field.dispatchEvent(event);
      return !event.defaultPrevented;
    });
    assert(pasted, "the graph swallowed a paste meant for the text field");
    const edits = await ui.toHost("flowEdit");
    const grew = edits.some((edit) => edit.graph.nodes.length !== 6);
    assert(!grew, "pasting into the field added nodes to the graph");
  },

  "dragging on the canvas selects no text": async (ui, assert) => {
    await boot(ui);
    await ui.page.click('[data-testid="frame"]');
    const canvas = await ui.page.locator('[data-testid="flow-canvas"]').boundingBox();
    await ui.page.mouse.move(canvas.x + 8, canvas.y + 8);
    await ui.page.mouse.down();
    await ui.page.mouse.move(canvas.x + canvas.width - 8, canvas.y + canvas.height - 8, {
      steps: 8,
    });
    await ui.page.mouse.up();

    // A node dragged by its title must not select the title either.
    const title = await centre(ui, '[data-testid="node-n3"] .flow-node-title');
    await ui.page.mouse.move(title.x, title.y);
    await ui.page.mouse.down();
    await ui.page.mouse.move(title.x + 60, title.y + 40, { steps: 6 });
    await ui.page.mouse.up();

    const selected = await ui.page.evaluate(() => String(window.getSelection() ?? ""));
    assert(selected === "", `the drag selected text: ${JSON.stringify(selected.slice(0, 80))}`);
  },
  // -------------------------------------------------------------------
  "a clean agent sends only what it is given": async (ui, assert) => {
    await ui.page.setViewportSize({ width: 1400, height: 950 });
    await boot(ui, graph([node("n1", "start", [0, 0])], []));
    await openCanvasMenu(ui);
    await ui.page.fill('[data-testid="menu-search"]', "clean");
    await ui.page.click('[data-testid="menu-kind-clean_agent"]');

    let sent = await lastGraph(ui);
    const added = sent.nodes.find((item) => item.kind === "clean_agent");
    assert(Boolean(added), "no clean agent reached the document");
    // What it may touch is said when the node is pointed at.
    const said = () => ui.page.locator(`[data-testid="node-${added.id}"] > title`).textContent();
    let footer = await said();
    assert(footer.includes("no system prompt"), `the node said ${JSON.stringify(footer)}`);

    await ui.page.fill('[data-testid="field-system"]', "Sempre termine com uma pergunta.");
    await ui.page.fill('[data-testid="field-task"]', "Ola, tudo bem?");
    sent = await lastGraph(ui);
    const edited = sent.nodes.find((item) => item.id === added.id);
    assert(edited.data.system === "Sempre termine com uma pergunta.", "the system prompt was lost");
    footer = await said();
    assert(footer.includes("own system prompt"), `the node said ${JSON.stringify(footer)}`);
    await ui.shot("46-clean-agent");
  },
  // -------------------------------------------------------------------
  "math and text nodes come from the menu and edit like any other": async (ui, assert) => {
    await ui.page.setViewportSize({ width: 1400, height: 950 });
    await boot(ui, graph([node("n1", "start", [0, 0])], []));

    await openCanvasMenu(ui);
    await ui.page.fill('[data-testid="menu-search"]', "append");
    await ui.page.click('[data-testid="menu-kind-append"]');
    await ui.page.click('[data-testid="pins-add"]');
    await ui.page.fill('[data-testid="literal-in_0"]', "Mendel: ");
    let sent = await lastGraph(ui);
    const append = sent.nodes.find((item) => item.kind === "append");
    assert(append.data.inputs === 3, `the append has ${append.data.inputs} inputs`);
    assert(append.data.in_0 === "Mendel: ", `the first part is ${JSON.stringify(append.data.in_0)}`);
    const pins = await ui.page.locator(`[data-testid="node-${append.id}"] [data-direction="in"]`).count();
    assert(pins === 3, `the node draws ${pins} input pins`);

    await openCanvasMenu(ui, { x: 600, y: 600 });
    await ui.page.fill('[data-testid="menu-search"]', "math");
    await ui.page.click('[data-testid="menu-kind-math"]');
    await ui.page.selectOption('[data-testid="field-op"]', "multiply");
    await ui.page.selectOption('[data-testid="field-type"]', "int");
    sent = await lastGraph(ui);
    const math = sent.nodes.find((item) => item.kind === "math");
    const summary = await ui.page
      .locator(`[data-testid="node-${math.id}"] .flow-node-summary`)
      .textContent();
    assert(summary === "a × b · int", `the math node reads ${summary}`);
  },

  "a graph built from functions reads well": async (ui, assert) => {
    await ui.page.setViewportSize({ width: 1600, height: 950 });
    const doc = graph(
      [
        node("n1", "start", [0, 60]),
        node("n2", "loop", [260, 60], { kind: "count", count: 3 }),
        node("n3", "var_get", [0, 300], { variable: "turno" }),
        node("n4", "math", [260, 300], { op: "add", type: "int", b: 1 }),
        node("n5", "var_set", [520, 60], { variable: "turno" }),
        node("n6", "format", [520, 300], { template: "Rodada {0}: {1}", inputs: 2 }),
        node("n7", "text_check", [780, 300], { op: "contains", part: "fim" }),
        node("n8", "print", [780, 60], { label: "rodada" }),
        node("n9", "to_int", [0, 480], { mode: "round" }),
      ],
      [
        wire("e1", ["n1", "out"], ["n2", "in"]),
        wire("e2", ["n2", "body"], ["n5", "in"]),
        wire("e3", ["n3", "value"], ["n4", "a"], "data"),
        wire("e4", ["n4", "result"], ["n5", "value"], "data"),
        wire("e5", ["n5", "out"], ["n8", "in"]),
        wire("e6", ["n3", "value"], ["n6", "arg_0"], "data"),
        wire("e7", ["n6", "result"], ["n8", "value"], "data"),
        wire("e8", ["n6", "result"], ["n7", "text"], "data"),
      ],
      { variables: [{ id: "v1", name: "turno", type: "int", default: 0 }] }
    );
    await boot(ui, doc);
    await ui.page.click('[data-testid="frame"]');
    await ui.shot("47-functions");
    const nodes = await ui.page.locator("g.flow-node").count();
    assert(nodes === 9, `drew ${nodes} nodes`);
  },

  // Opening a file is not an edit. The panel mounts on a placeholder graph, and
  // pushing that one dirties the tab and blanks the file before it arrives.
  "opening a graph leaves the document alone": async (ui, assert) => {
    await ui.fromHost({ type: "flowCatalog", catalog: CATALOG });
    await ui.fromHost({
      type: "flowDocument",
      graph: pipeline(),
      fileName: "pipeline.flow.json",
      readOnly: false,
    });
    await ui.page.waitForSelector('[data-testid="node-n3"]');

    const edits = await ui.toHost("flowEdit");
    assert(edits.length === 0, `sent ${edits.length} flowEdit just by opening`);
    const nodes = await ui.page.locator("g.flow-node").count();
    assert(nodes === 6, `drew ${nodes} nodes`);
  },
};
