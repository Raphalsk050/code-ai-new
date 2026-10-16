// The graph editor following a run (webview/flow/focus.ts): the node at work
// opens to show what it writes, its neighbours step aside on a spring, and it
// folds back before the next one opens. Plus the camera fixes that came with it.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const CATALOG = JSON.parse(readFileSync(join(root, "e2e", "fixtures", "catalog.json"), "utf8"));

function node(id, kind, pos, data = {}) {
  return { id, kind, pos, data };
}

function wire(id, from, to) {
  return { id, kind: "exec", from, to };
}

/** Start -> Agent -> Print -> End in a row, with a Print above and one below the agent. */
function pipeline() {
  return {
    version: 1,
    name: "follow",
    variables: [],
    nodes: [
      node("start", "start", [0, 200]),
      node("agent", "profession", [300, 200], { profession: "coder", task: "Leia o README" }),
      node("print", "print", [600, 200], { label: "fim" }),
      node("end", "end", [900, 200]),
      node("above", "print", [300, 20]),
      node("below", "print", [300, 420]),
    ],
    edges: [
      wire("e1", ["start", "out"], ["agent", "in"]),
      wire("e2", ["agent", "out"], ["print", "in"]),
      wire("e3", ["print", "out"], ["end", "in"]),
    ],
    view: { pan: [40, 60], zoom: 0.7 },
  };
}

async function boot(ui, graph = pipeline(), catalog = CATALOG) {
  await ui.fromHost({ type: "flowCatalog", catalog });
  await ui.fromHost({ type: "flowDocument", graph, fileName: "follow.flow.json", readOnly: false });
  await ui.fromHost({ type: "flowValidation", ok: true, issues: [] });
  await ui.page.waitForSelector('[data-testid="flow-canvas"] g.flow-node');
  await ui.clearToHost();
}

async function box(ui, selector) {
  const found = await ui.page.locator(selector).first().boundingBox();
  if (!found) throw new Error(`no box for ${selector}`);
  return found;
}

const settle = (ui, ms) => ui.page.waitForTimeout(ms);

async function startAgent(ui) {
  await ui.event("graph.run.started", { run_id: "r1" });
  await ui.fromHost({ type: "flowRunState", running: true, runId: "r1", message: "" });
  await ui.event("graph.node.started", { node_id: "start", index: 1 });
  await ui.event("graph.node.completed", { node_id: "start", summary: "", seconds: 0 });
  await ui.event("graph.node.started", { node_id: "agent", index: 2 });
  await ui.event("graph.agent.started", {
    node_id: "agent",
    agent_name: "witty-sparkling-newton",
    profession: "coder",
    model: "qwen3",
  });
}

export const tests = {
  async "the node at work opens, its neighbours step aside, and it reads what it writes"(ui, assert) {
    await boot(ui);
    const before = {
      above: await box(ui, '[data-testid="node-above"] .flow-node-box'),
      below: await box(ui, '[data-testid="node-below"] .flow-node-box'),
      print: await box(ui, '[data-testid="node-print"] .flow-node-box'),
      agent: await box(ui, '[data-testid="node-agent"] .flow-node-box'),
    };
    await startAgent(ui);
    await ui.event("graph.node.output", { node_id: "agent", channel: "thinking", text: "Preciso ler o README primeiro." });
    await ui.event("graph.node.progress", { node_id: "agent", event: "tool.call.started", detail: "read_file README.md" });
    await ui.event("graph.node.output", {
      node_id: "agent",
      channel: "answer",
      text: "O README descreve o Code-AI: um agente que roda local, com extensão para VS Code e um terminal.",
    });
    // Mid-spring: part open.
    await settle(ui, 120);
    await ui.shot("follow-01-opening");
    await settle(ui, 1400);
    await ui.page.waitForSelector('[data-testid="output-agent"]');
    const text = await ui.page.locator('[data-testid="output-agent"]').innerText();
    assert(text.includes("README descreve"), `the panel reads ${JSON.stringify(text)}`);
    assert(text.includes("read_file README.md"), "the tool call is not in the panel");
    await ui.shot("follow-02-open");

    // The camera follows to the node at a readable zoom.
    const after = {
      above: await box(ui, '[data-testid="node-above"] .flow-node-box'),
      below: await box(ui, '[data-testid="node-below"] .flow-node-box'),
      print: await box(ui, '[data-testid="node-print"] .flow-node-box'),
      agent: await box(ui, '[data-testid="node-agent"] .flow-node-box'),
    };
    const zoom = after.agent.height / before.agent.height;
    assert(after.agent.width > before.agent.width * 1.6, `the agent did not open: ${after.agent.width}`);
    const gap = (a, b) => b.x - (a.x + a.width);
    assert(gap(after.agent, after.print) > 0, "the open agent covers the print beside it");
    assert(after.above.y + after.above.height < after.agent.y, "the node above was not pushed clear");
    assert(after.below.y > after.agent.y + after.agent.height, "the node below was not pushed clear");
    assert(zoom > 1, `the camera did not come closer (${zoom})`);

    await ui.event("graph.node.completed", { node_id: "agent", summary: "Resumo pronto.", seconds: 3.2 });
    await ui.event("graph.node.started", { node_id: "print", index: 3 });
    await ui.event("graph.print", { node_id: "print", label: "fim", text: "acabou" });
    await ui.event("graph.node.completed", { node_id: "print", summary: "acabou", seconds: 0 });
    // The agent lingers a moment, folds, and only then does the print open.
    await settle(ui, 700);
    const lingering = await ui.page.locator('[data-testid="output-agent"]').count();
    assert(lingering === 1, "the agent folded before anyone could read its last words");
    assert((await ui.page.locator('[data-testid="output-print"]').count()) === 0, "the print opened while the agent was still open");
    await settle(ui, 1600);
    await ui.page.waitForSelector('[data-testid="output-print"]', { timeout: 4000 });
    assert((await ui.page.locator('[data-testid="output-agent"]').count()) === 0, "two nodes are open at once");
    await ui.shot("follow-03-next");
    // Folded, the agent keeps its output a click away.
    await ui.page.waitForSelector('[data-testid="output-pill-agent"]');
  },

  async "a folded node's output opens again by hand and closes by hand"(ui, assert) {
    await boot(ui);
    await startAgent(ui);
    await ui.event("graph.node.output", { node_id: "agent", channel: "answer", text: "Feito." });
    await ui.event("graph.node.completed", { node_id: "agent", summary: "Feito.", seconds: 1 });
    await ui.event("graph.run.completed", { run_id: "r1", status: "completed", seconds: 1, nodes_executed: 2 });
    await ui.fromHost({ type: "flowRunState", running: false, runId: "r1", message: "completed" });
    await ui.page.waitForSelector('[data-testid="output-pill-agent"]', { timeout: 6000 });
    await ui.page.click('[data-testid="output-pill-agent"]');
    await ui.page.waitForSelector('[data-testid="output-close-agent"]', { timeout: 3000 });
    await settle(ui, 900);
    const text = await ui.page.locator('[data-testid="output-agent"]').innerText();
    assert(text.includes("Feito."), `the panel reads ${JSON.stringify(text)}`);
    await ui.shot("follow-04-by-hand");
    await ui.page.click('[data-testid="output-close-agent"]');
    await settle(ui, 1200);
    assert((await ui.page.locator('[data-testid="output-agent"]').count()) === 0, "the panel did not close");
  },

  async "the panel scrolls with the wheel instead of zooming the canvas"(ui, assert) {
    await boot(ui);
    await startAgent(ui);
    const long = Array.from({ length: 60 }, (_, index) => `linha ${index + 1}`).join("\n");
    await ui.event("graph.node.output", { node_id: "agent", channel: "answer", text: long });
    await ui.page.waitForSelector('[data-testid="output-agent"]');
    await settle(ui, 1500);
    const zoomBefore = await ui.page.evaluate(() => document.querySelector('[data-testid="flow-canvas"] > g').getAttribute("transform"));
    const panel = await box(ui, '[data-testid="output-agent"]');
    await ui.page.mouse.move(panel.x + panel.width / 2, panel.y + panel.height / 2);
    const top = () => ui.page.evaluate(() => document.querySelector(".flow-output-scroll").scrollTop);
    const atEnd = await top();
    assert(atEnd > 0, "the panel does not follow the end of what is written");
    await ui.page.mouse.wheel(0, -300);
    await settle(ui, 200);
    assert((await top()) < atEnd, "the wheel did not scroll the panel");
    const zoomAfter = await ui.page.evaluate(() => document.querySelector('[data-testid="flow-canvas"] > g').getAttribute("transform"));
    assert(zoomAfter === zoomBefore, `the canvas moved under the panel: ${zoomBefore} -> ${zoomAfter}`);
  },

  async "with the setting off, a run leaves every node shut"(ui, assert) {
    await boot(ui, pipeline(), { ...CATALOG, follow_run: false });
    await startAgent(ui);
    await ui.event("graph.node.output", { node_id: "agent", channel: "answer", text: "Oi" });
    await settle(ui, 900);
    assert((await ui.page.locator('[data-testid="output-agent"]').count()) === 0, "the node opened anyway");
  },

  async "the map stays after the view is framed"(ui, assert) {
    await boot(ui);
    await ui.page.click('[data-testid="frame"]');
    await settle(ui, 100);
    assert(await ui.page.locator('[data-testid="minimap"]').isVisible(), "the map is gone once everything fits");
  },

  async "a Magic Mouse slide moves the camera, and a pan writes nothing"(ui, assert) {
    await boot(ui);
    const transform = () =>
      ui.page.evaluate(() => document.querySelector('[data-testid="flow-canvas"] > g').getAttribute("transform"));
    const before = await transform();
    await ui.page.evaluate(() => {
      const canvas = document.querySelector('[data-testid="flow-canvas"]');
      // What Chromium reports for a finger on the surface: small steps, wheelDelta -3x.
      for (const deltaY of [3.5, 6.25, 9.75]) {
        canvas.dispatchEvent(
          new WheelEvent("wheel", { deltaY, wheelDeltaY: Math.round(-3 * deltaY), clientX: 400, clientY: 300, bubbles: true, cancelable: true })
        );
      }
    });
    await settle(ui, 100);
    const after = await transform();
    assert(before !== after, "the slide did not move the camera");
    assert(after.endsWith("scale(0.7)"), `the slide zoomed: ${after}`);
    assert((await ui.toHost("flowEdit")).length === 0, "panning wrote the document");
    // A notch still zooms, once the slide is over.
    await settle(ui, 400);
    await ui.page.evaluate(() => {
      const canvas = document.querySelector('[data-testid="flow-canvas"]');
      canvas.dispatchEvent(
        new WheelEvent("wheel", { deltaY: -100, wheelDeltaY: 120, clientX: 400, clientY: 300, bubbles: true, cancelable: true })
      );
    });
    await settle(ui, 300);
    assert(!(await transform()).endsWith("scale(0.7)"), "a wheel notch no longer zooms");
  },

  async "a comment starting right of another's middle is drawn in front"(ui, assert) {
    const graph = {
      ...pipeline(),
      nodes: [
        node("big", "comment", [600, 300], { title: "Direita", width: 900, height: 500, color: "#16a34a" }),
        node("left", "comment", [0, 0], { title: "Esquerda", width: 800, height: 400, color: "#2563eb" }),
        node("start", "start", [60, 120]),
      ],
      edges: [],
      view: { pan: [20, 20], zoom: 0.5 },
    };
    await boot(ui, graph);
    const order = await ui.page.evaluate(() =>
      Array.from(document.querySelectorAll(".flow-comment")).map((el) => el.getAttribute("data-node-id"))
    );
    assert(order.join() === "left,big", `painted ${order.join(", ")}`);
    await ui.shot("follow-05-comments");
  },
};
