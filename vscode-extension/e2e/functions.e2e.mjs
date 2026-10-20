// Functions in the graph editor (webview/flow/functions.ts), Blueprint-style:
// made from the Functions tab, called from the add-node menu, opened with a
// double-click, and made out of a selection with "Collapse into a function".

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const CATALOG = JSON.parse(readFileSync(join(root, "e2e", "fixtures", "catalog.json"), "utf8"));

function node(id, kind, pos, data = {}) {
  return { id, kind, pos, data };
}

function wire(id, from, to, kind = "exec") {
  return { id, kind, from, to };
}

/** Start -> Agent -> Print -> End, the agent's report printed. */
function pipeline() {
  return {
    version: 1,
    name: "functions",
    variables: [],
    nodes: [
      node("start", "start", [0, 200]),
      node("agent", "profession", [300, 200], { profession: "coder", task: "Leia o README" }),
      node("print", "print", [700, 200], { label: "fim" }),
      node("end", "end", [1000, 200]),
    ],
    edges: [
      wire("e1", ["start", "out"], ["agent", "in"]),
      wire("e2", ["agent", "out"], ["print", "in"]),
      wire("e3", ["agent", "report"], ["print", "value"], "data"),
      wire("e4", ["print", "out"], ["end", "in"]),
    ],
    view: { pan: [60, 40], zoom: 0.7 },
  };
}

async function boot(ui, graph = pipeline()) {
  await ui.fromHost({ type: "flowCatalog", catalog: CATALOG });
  await ui.fromHost({ type: "flowDocument", graph, fileName: "functions.flow.json", readOnly: false });
  await ui.fromHost({ type: "flowValidation", ok: true, issues: [] });
  await ui.page.waitForSelector('[data-testid="flow-canvas"] g.flow-node');
  await ui.clearToHost();
}

async function lastGraph(ui) {
  const edits = await ui.toHost("flowEdit");
  if (edits.length === 0) throw new Error("the panel sent no flowEdit");
  return edits[edits.length - 1].graph;
}

const settle = (ui, ms = 150) => ui.page.waitForTimeout(ms);

export const tests = {
  async "a function is made, given an input, called and opened"(ui, assert) {
    await boot(ui);
    await ui.page.click('[data-testid="tab-functions"]');
    await ui.page.click('[data-testid="add-function"]');
    // It opens on the canvas: an Entry wired into a Return, and the way back above.
    await ui.page.waitForSelector('[data-testid="crumbs"]');
    const kinds = await ui.page.$$eval('[data-testid="flow-canvas"] g.flow-node', (nodes) => nodes.length);
    assert(kinds === 2, `a new function holds ${kinds} nodes`);
    await ui.page.click('[data-testid="add-inputs"]');
    await ui.page.fill('[data-testid="param-name-inputs-0"]', "topic");
    await ui.page.click('[data-testid="add-outputs"]');
    await settle(ui);
    // The Entry hands on what the function is given.
    assert(
      (await ui.page.locator('[data-testid="node-f1.n1"] [data-testid="pin-out-p1"]').count()) === 1,
      "the Entry has no pin for its input"
    );
    await ui.shot("functions-01-inside");

    await ui.page.click('[data-testid="crumb-main"]');
    await ui.page.waitForSelector('[data-testid="crumbs"]', { state: "detached" });
    await ui.page.mouse.click(600, 600, { button: "right" });
    await ui.page.waitForSelector('[data-testid="context-menu"]');
    await ui.page.click('[data-testid="menu-call-f1"]');
    await settle(ui);
    let sent = await lastGraph(ui);
    const call = sent.nodes.find((item) => item.kind === "call_function");
    assert(call && call.data.function === "f1", "no call to the function was made");
    assert(sent.functions?.[0]?.inputs?.[0]?.name === "topic", `the input did not reach the file: ${JSON.stringify(sent.functions)}`);
    const pins = await ui.page.$$eval(`[data-testid="node-${call.id}"] [data-pin]`, (items) =>
      items.map((item) => `${item.getAttribute("data-direction")}:${item.getAttribute("data-pin")}`)
    );
    assert(pins.includes("in:p1") && pins.includes("out:p2"), `the call's pins are ${pins}`);
    await ui.shot("functions-02-call");

    // A double-click on the call goes into the function.
    const box = await ui.page.locator(`[data-testid="node-${call.id}"] .flow-node-box`).boundingBox();
    await ui.page.mouse.dblclick(box.x + box.width / 2, box.y + 12);
    await ui.page.waitForSelector('[data-testid="crumbs"]');
    const here = await ui.page.locator('[data-testid="crumb-here"]').textContent();
    assert(here === "Function", `the crumbs say ${here}`);
  },

  async "a selection collapses into a function, and undo puts it back"(ui, assert) {
    await boot(ui);
    // Select the agent and the print, then collapse them from the node menu.
    await ui.page.click('[data-testid="node-agent"] .flow-node-box');
    const print = await ui.page.locator('[data-testid="node-print"] .flow-node-box').boundingBox();
    await ui.page.keyboard.down("Shift");
    await ui.page.mouse.click(print.x + 10, print.y + 10);
    await ui.page.keyboard.up("Shift");
    await ui.page.mouse.click(print.x + 10, print.y + 10, { button: "right" });
    await ui.page.waitForSelector('[data-testid="context-menu"]');
    await ui.page.click('[data-testid="menu-collapse-function"]');
    await settle(ui);

    const sent = await lastGraph(ui);
    const made = sent.functions?.[0];
    assert(made?.collapsed === true, `no collapsed function: ${JSON.stringify(sent.functions)}`);
    assert(made.nodes.length === 4, `the function holds ${made.nodes.length} nodes`);
    assert(
      sent.nodes.map((item) => item.kind).sort().join(",") === "call_function,end,start",
      `the main graph is ${sent.nodes.map((item) => item.kind)}`
    );
    await ui.shot("functions-03-collapsed");

    await ui.page.keyboard.press("Control+z");
    await settle(ui);
    const undone = await lastGraph(ui);
    assert(!undone.functions, "undo left the function behind");
    assert(undone.nodes.some((item) => item.id === "agent"), "undo did not bring the agent back");
  },
};
