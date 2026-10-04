// The task rail: sections that say what they hold while closed, the session in
// words, the conversation's changes, the plan without its old mode buttons,
// and the icon strip a narrow panel gets instead.

import { settingsSnapshot } from "./harness.mjs";

let seq = 0;
function envelope(event_type, payload = {}) {
  seq += 1;
  return {
    type: "event",
    event: {
      event_id: `r${seq}`, event_type, event_version: 1, session_id: "s",
      sequence: seq, timestamp: "", source: "app", payload,
    },
  };
}

async function openChat(ui) {
  await ui.page.click(".home-new");
  await ui.page.waitForSelector(".composer-wrap");
  await ui.fromHost({ type: "settings", settings: settingsSnapshot({ model: "gemma4:31b-cloud" }) });
  await ui.fromHost(envelope("status.changed", { state: "READY" }));
}

const CHANGES = [
  { path: "src/terrain/chunk.cpp", added: 12, removed: 4, created: false, revertable: true },
  { path: "src/terrain/seam.hpp", added: 30, removed: 0, created: true, revertable: true },
];

async function badge(ui, title) {
  return ui.page.evaluate((title) => {
    for (const head of document.querySelectorAll(".rail-section-head")) {
      if (head.querySelector(".rail-section-title")?.textContent === title) {
        const b = head.querySelector(".rail-badge-text");
        return b ? { text: b.textContent, tone: b.className.replace("rail-badge-text", "").trim() } : null;
      }
    }
    return undefined;
  }, title);
}

export const tests = {
  async "the docked rail is wider, has Changes and no footer"(ui, assert) {
    await openChat(ui);
    const titles = await ui.page.$$eval(".rail-section-title", (els) => els.map((e) => e.textContent));
    assert(titles.join(",") === "Tasks,Session,Plan,Changes,Agents,Terminal,History", `sections: ${titles}`);
    const width = await ui.page.$eval(".rail-dock", (el) => el.getBoundingClientRect().width);
    assert(width >= 280 && width <= 296, `rail is ${width}px`);
    assert(!(await ui.page.$(".rail-foot")), "the footer is still there");
  },

  async "the session speaks in words and hides the internals"(ui, assert) {
    await openChat(ui);
    await ui.fromHost(envelope("usage.updated", { active_context_tokens: 61000, context_budget: 128000, context_threshold: 0.8 }));
    const state = await ui.page.textContent(".session-panel .kv .v");
    assert(state.trim() === "Ready", `state reads ${state}`);
    assert(await ui.page.$(".session-panel .rail-meter-mark"), "no auto-compaction mark on the meter");
    assert(!(await ui.page.$('.session-panel .kv .k:text-is("Verification")')), "verification shows before any ran");
    assert(!(await ui.page.$('.session-panel .kv .k:text-is("Model")')), "the details are not tucked away");
    await ui.page.click(".details-toggle");
    assert(await ui.page.$('.session-panel .kv .k:text-is("Model")'), "the details do not open");

    await ui.fromHost(envelope("status.changed", { state: "EXECUTING_TOOL" }));
    await ui.fromHost(envelope("tool.call.started", { tool_call_id: "t", name: "web_search" }));
    assert((await ui.page.textContent(".session-panel .kv .v")).includes("Running web_search"), "the running tool is not named");
    await ui.fromHost(envelope("tool.approval.requested", { call_id: "a", tool_name: "execute_command", arguments: { command: "rm -rf build" } }));
    const b = await badge(ui, "Session");
    assert(b?.text === "waiting for you" && b.tone.includes("warn"), `session badge ${JSON.stringify(b)}`);
    await ui.shot("rail-session");
  },

  async "changes list the files, open them, revert them and review them all"(ui, assert) {
    await openChat(ui);
    await ui.fromHost({ type: "sessionChanges", files: CHANGES });
    const b = await badge(ui, "Changes");
    assert(b?.text === "2", `changes badge ${JSON.stringify(b)}`);
    const rows = await ui.page.$$eval(".change-row", (els) => els.map((e) => e.textContent.replace(/\s+/g, " ").trim()));
    assert(rows[0].startsWith("chunk.cpp") && rows[0].includes("+12 -4"), `row ${rows[0]}`);
    await ui.clearToHost();
    await ui.page.click(".change-row >> nth=0 >> .change-open");
    assert((await ui.toHost("openChange"))[0]?.path === "src/terrain/chunk.cpp", "the row does not open its change");
    await ui.page.hover(".change-row >> nth=1");
    await ui.page.click(".change-row >> nth=1 >> .change-revert");
    assert((await ui.toHost("revertChange"))[0]?.path === "src/terrain/seam.hpp", "revert does not ask the host");
    await ui.page.click('.changes button:has-text("Review all")');
    assert((await ui.toHost("reviewChanges")).length === 1, "review all does nothing");
    await ui.shot("rail-changes");
  },

  async "the plan shows progress in its badge and no mode buttons"(ui, assert) {
    await openChat(ui);
    let b = await badge(ui, "Plan");
    assert(b?.text === "-", `empty plan badge ${JSON.stringify(b)}`);
    await ui.fromHost(envelope("user.message", { text: "go" }));
    await ui.fromHost(
      envelope("planning.step.started", {
        completed_steps: ["Read the chunk code"],
        remaining_steps: ["Round once at the seam", "Run the tests"],
        current_step: "Round once at the seam",
        current_step_status: "RUNNING",
      })
    );
    b = await badge(ui, "Plan");
    assert(b?.text === "1/3" && b.tone.includes("run"), `plan badge ${JSON.stringify(b)}`);
    await ui.page.evaluate(() => {
      for (const head of document.querySelectorAll(".rail-section-head")) {
        if (head.textContent.includes("Plan") && head.getAttribute("aria-expanded") === "false") head.click();
      }
    });
    await ui.page.waitForSelector(".plan-step.running .spinner");
    const buttons = await ui.page.$$eval(".plan-steps ~ .panel-actions button", (els) => els.map((e) => e.textContent));
    assert(!buttons.some((x) => ["auto", "plan", "act"].includes(x)), `mode buttons remain: ${buttons}`);
    await ui.page.waitForTimeout(400);
    await ui.shot("rail-plan");
  },

  async "a failed agent opens its section by itself"(ui, assert) {
    await openChat(ui);
    await ui.fromHost(envelope("subagent.started", { agent_id: "x", agent_type: "explorer", name: "seam-scout", task: "find the seam" }));
    let b = await badge(ui, "Agents");
    assert(b?.text === "1 running", `running badge ${JSON.stringify(b)}`);
    await ui.fromHost(envelope("subagent.failed", { agent_id: "x", agent_type: "explorer", message: "timed out" }));
    b = await badge(ui, "Agents");
    assert(b?.text === "1 failed" && b.tone.includes("bad"), `failed badge ${JSON.stringify(b)}`);
    await ui.page.waitForSelector(".agent-card.failed");
  },

  async "collapsed, an icon opens its own section and not the whole panel"(ui, assert) {
    await openChat(ui);
    await ui.page.click('.rail-dock-head button[aria-label="Collapse the task panel"]');
    await ui.page.waitForSelector(".rail-strip");

    await ui.page.click(".rail-btn-agents");
    await ui.page.waitForSelector(".rail-drawer.enter");
    assert(!(await ui.page.$(".rail-dock")), "the icon docked the whole panel");
    const title = await ui.page.textContent(".rail-drawer .rail-section-title");
    assert(title === "Agents", `the drawer shows ${title}`);
    assert(await ui.page.$(".rail-btn-agents.active"), "the strip does not mark the open section");
    await ui.shot("rail-collapsed-drawer");

    // Another icon swaps the section; the same one closes it.
    await ui.page.click(".rail-btn-terminal");
    await ui.page.waitForFunction(
      () => document.querySelector(".rail-drawer .rail-section-title")?.textContent === "Terminal"
    );
    await ui.page.click(".rail-btn-terminal");
    await ui.page.waitForSelector(".rail-drawer", { state: "detached" });

    // Docking with a drawer open leaves nothing behind to pop out on collapse.
    await ui.page.click(".rail-btn-agents");
    await ui.page.waitForSelector(".rail-drawer.enter");
    await ui.page.click('.rail-strip button[aria-label="Task panel"]');
    await ui.page.waitForSelector(".rail-dock");
    await ui.page.click('.rail-dock-head button[aria-label="Collapse the task panel"]');
    await ui.page.waitForSelector(".rail-strip");
    await ui.page.waitForTimeout(300);
    assert(!(await ui.page.$(".rail-drawer")), "a drawer came back with the collapse");
  },

  async "below 640px the rail is a strip with counts, dots and the twin agents icon"(ui, assert) {
    await ui.page.setViewportSize({ width: 600, height: 800 });
    await openChat(ui);
    await ui.fromHost({ type: "sessionChanges", files: CHANGES });
    await ui.fromHost(envelope("subagent.started", { agent_id: "x", agent_type: "explorer", name: "scout" }));
    assert(await ui.page.$(".rail-strip"), "no strip at 600px");
    assert(!(await ui.page.$(".rail-dock")), "the rail still docks at 600px");
    assert((await ui.page.textContent(".rail-btn-changes .rail-badge")) === "2", "no count on Changes");
    assert(await ui.page.$(".rail-btn-agents .rail-dot"), "no activity dot on Agents");
    const glyphs = await ui.page.$$eval(".rail-btn-agents svg g", (els) => els.length);
    assert(glyphs === 2, `agents icon draws ${glyphs} glyphs`);
    await ui.shot("rail-strip");
    await ui.page.click(".rail-btn-changes");
    await ui.page.waitForSelector(".rail-drawer .change-row");
    await ui.shot("rail-drawer");
  },
};
