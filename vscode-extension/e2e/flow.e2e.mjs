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
async function openCanvasMenu(ui, at = { x: 600, y: 300 }) {
  await ui.page.mouse.click(at.x, at.y, { button: "right" });
  await ui.page.waitForSelector('[data-testid="context-menu"]');
}

/** Right-click a node and wait for its menu. */
async function openNodeMenu(ui, nodeId) {
  const point = await centre(ui, `[data-testid="node-${nodeId}"] .flow-node-title`);
  await ui.page.mouse.click(point.x, point.y, { button: "right" });
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
    await openCanvasMenu(ui, { x: 600, y: 300 });
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
    await dragPinToEmpty(ui, '[data-testid="node-n3"] [data-testid="pin-out-report"]', {
      x: 620,
      y: 620,
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
      [node("n1", "start", [-200, 0]), node("n2", "print", [120, 0], { label: "x" })],
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
      [node("n1", "start", [-200, 0]), node("n2", "print", [120, 0], { label: "x" })],
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
    await ui.page.keyboard.down("Control");
    await ui.page.keyboard.press("KeyC");
    await ui.page.keyboard.press("KeyV");
    await ui.page.keyboard.up("Control");

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
    await ui.shot("20-dragged");
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
};
