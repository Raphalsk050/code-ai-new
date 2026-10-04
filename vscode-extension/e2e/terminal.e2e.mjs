// The shared terminal as command blocks: who ran what, how it ended, the
// output folded until it matters, and the prompt handed back from the agent.

import { settingsSnapshot } from "./harness.mjs";

let seq = 0;
function screen(payload) {
  seq += 1;
  return {
    type: "event",
    event: {
      event_id: `x${seq}`, event_type: "terminal.screen.updated", event_version: 1, session_id: "s",
      sequence: seq, timestamp: "", source: "terminal.poller",
      payload: { session_id: "3f2a9c1e-77", rows: 24, columns: 100, screen: "$ ", closed: false, ...payload },
    },
  };
}

const block = (id, command, author, exit, output, extra = {}) => ({
  id, command, author, cwd: "/w/untrodden", started_at: 1_000_000, ended_at: exit === undefined ? null : 1_002_400,
  exit_code: exit ?? null, running: exit === undefined, output, truncated: false, ...extra,
});

async function openTerminal(ui, payload) {
  await ui.page.click(".home-new");
  await ui.page.waitForSelector(".composer-wrap");
  await ui.fromHost({ type: "settings", settings: settingsSnapshot() });
  await ui.fromHost(screen(payload));
  await ui.page.evaluate(() => {
    for (const head of document.querySelectorAll(".rail-section-head")) {
      if (head.textContent.includes("Terminal") && head.getAttribute("aria-expanded") === "false") head.click();
    }
  });
  await ui.page.waitForSelector(".term");
}

const INTEGRATED = {
  integrated: true, shell: "bash", cwd: "/w/untrodden", running: false, running_author: "",
  blocks: [
    block(1, "cmake --build build", "user", 0, "[100%] Built target terrain"),
    block(2, "ctest -R chunk", "agent", 1, "1/3 Test #2: chunk_seam ...\x1b[31m***Failed\x1b[0m"),
  ],
};

export const tests = {
  async "commands are blocks: success folded, failure open with its exit code"(ui, assert) {
    await openTerminal(ui, INTEGRATED);
    const heads = await ui.page.$$eval(".term-block", (els) =>
      els.map((e) => [e.className, e.querySelector(".term-command").textContent, !!e.querySelector(".term-output")])
    );
    assert(heads[0][1] === "cmake --build build" && !heads[0][2], `first block ${heads[0]}`);
    assert(heads[1][0].includes("failed") && heads[1][2], `second block ${heads[1]}`);
    assert((await ui.page.textContent(".term-block.failed .term-exit")) === "exit 1", "no exit code");
    assert(await ui.page.$(".term-block.failed .term-author.agent svg"), "the agent's block lacks its mark");
    assert(await ui.page.$(".term-block.done .term-author.user .codicon-account"), "the user's block lacks theirs");
    assert((await ui.page.textContent(".term-output .ansi-red")) === "***Failed", "colours are lost");
    assert(!(await ui.page.$(".term-screen")), "the raw screen still shows");
    const badge = await ui.page.evaluate(() =>
      [...document.querySelectorAll(".rail-section-head")].find((h) => h.textContent.includes("Terminal"))
        ?.querySelector(".rail-badge-text")?.textContent
    );
    assert(badge === "exit 1", `terminal badge ${badge}`);
    await ui.page.click(".term-block.done .term-block-head");
    assert(await ui.page.$(".term-block.done .term-output"), "a click does not open the output");
    await ui.page.waitForTimeout(400);
    await ui.shot("terminal-blocks");
  },

  async "while the agent runs a command, the prompt offers to take over"(ui, assert) {
    await openTerminal(ui, {
      ...INTEGRATED, running: true, running_author: "agent",
      blocks: [...INTEGRATED.blocks, block(3, "npm run dev", "agent", undefined, "ready on :5173", { started_at: Date.now() - 65_000 })],
    });
    assert(!(await ui.page.$(".term-input")), "the input is open under the agent's command");
    assert(await ui.page.$(".term-block.running .spinner"), "the running block has no spinner");
    await ui.clearToHost();
    await ui.page.click('.term-agent button:has-text("Take over")');
    const sent = await ui.toHost("terminal");
    assert(sent[0]?.request?.action === "ctrl" && sent[0].request.key === "c", `take over sent ${JSON.stringify(sent)}`);
    await ui.shot("terminal-agent");
  },

  async "the user's own command can be interrupted from the bar"(ui, assert) {
    await openTerminal(ui, {
      ...INTEGRATED, running: true, running_author: "user",
      blocks: [block(4, "python -m http.server", "user", undefined, "")],
    });
    await ui.clearToHost();
    await ui.page.click('.term-bar button:has-text("Interrupt")');
    assert((await ui.toHost("terminal"))[0]?.request?.key === "c", "interrupt did not send Ctrl+C");
    assert(await ui.page.$(".term-input"), "the input closed for the user's own command");
  },

  async "a shell without marks keeps the raw screen; the menu holds the rest"(ui, assert) {
    await openTerminal(ui, { screen: "C:\\w> dir\n Volume in drive C" });
    assert((await ui.page.textContent(".term-screen")).includes("Volume in drive C"), "no raw screen");
    for (const gone of ["Ctrl+D", "End", "Refresh"]) {
      assert(!(await ui.page.$(`.term > .panel-actions button:has-text("${gone}")`)), `${gone} is still a button`);
    }
    await ui.page.click('.term-more button[aria-label="More actions"]');
    const items = await ui.page.$$eval(".term-menu-item", (els) => els.map((e) => e.textContent));
    assert(items.join("|") === "Copy output|Refresh the screen|Send EOF (Ctrl+D)|Clear (Ctrl+L)|End the session", `menu: ${items}`);
    await ui.shot("terminal-menu");
    await ui.clearToHost();
    await ui.page.click('.term-menu-item:has-text("End the session")');
    assert((await ui.toHost("terminal"))[0]?.request?.action === "kill", "end did not kill the session");
  },

  async "an ended session says so and starts another"(ui, assert) {
    await openTerminal(ui, { ...INTEGRATED, closed: true });
    assert(await ui.page.$(".term-ended"), "no ended state");
    await ui.clearToHost();
    await ui.page.click('.term-ended button:has-text("New session")');
    assert((await ui.toHost("terminal"))[0]?.request?.action === "start", "new session did not start one");
  },
};
