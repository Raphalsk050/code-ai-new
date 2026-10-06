// The transcript's parts: tool cards and their groups, the approval card, the
// turn footer, the banner over the composer and the empty conversation.

import { settingsSnapshot } from "./harness.mjs";

let seq = 0;
function envelope(event_type, payload = {}, timestamp = "") {
  seq += 1;
  return {
    type: "event",
    event: {
      event_id: `p${seq}`, event_type, event_version: 1, session_id: "s",
      sequence: seq, timestamp, source: "app", payload,
    },
  };
}

const at = (seconds) => new Date(Date.UTC(2026, 9, 3, 10, 0, 0) + seconds * 1000).toISOString();

async function openChat(ui) {
  await ui.page.click(".home-new");
  await ui.page.waitForSelector(".composer-wrap");
  await ui.fromHost({ type: "settings", settings: settingsSnapshot({ model: "gemma4:31b-cloud" }) });
  await ui.fromHost(envelope("status.changed", { state: "READY" }));
}

async function tool(ui, id, name, target, start, end, result, failed) {
  await ui.fromHost(envelope("tool.call.started", { tool_call_id: id, name, target, path: target }, at(start)));
  if (end == null) return;
  await ui.fromHost(
    failed
      ? envelope("tool.call.failed", { tool_call_id: id, name, message: failed }, at(end))
      : envelope("tool.call.completed", { tool_call_id: id, name, result: result ?? {} }, at(end))
  );
}

export const tests = {
  async "a call that went through shows its time, not a badge; a write shows its lines"(ui, assert) {
    await openChat(ui);
    await ui.fromHost(envelope("user.message", { text: "fix the seam" }, at(0)));
    await tool(ui, "e1", "edit_code", "src/terrain/chunk.cpp", 1, 1.42, { lines_added: 12, lines_removed: 4 });
    const card = ui.page.locator(".tool").first();
    assert((await card.locator(".tool-badge").count()) === 0, "a successful call still wears a badge");
    assert((await card.locator(".tool-time").textContent()) === "0.4s", "no duration on the card");
    assert((await card.locator(".line-delta").textContent()).replace(/\s+/g, " ") === "+12 -4", "no line counts");
    await tool(ui, "r1", "execute_command", "ctest -R chunk", 2);
    const running = ui.page.locator(".tool-running");
    assert((await running.locator(".tool-badge").textContent()) === "running", "a running call has no badge");
    await ui.shot("turns-tools");
  },

  async "a failed call is open already, with its output and a way to copy it"(ui, assert) {
    await openChat(ui);
    await ui.fromHost(envelope("user.message", { text: "run the tests" }, at(0)));
    await tool(ui, "x1", "execute_command", "ctest -R chunk", 1, 3.2, null, "1/3 tests failed\nchunk_seam ... FAILED\nexpected 0.5 got 0.49");
    assert(await ui.page.$(".tool-failed .tool-detail-block"), "the failure is not open");
    assert(await ui.page.$('.tool-failed button:has-text("Copy")'), "no copy action");
    assert(await ui.page.$('.tool-failed button:has-text("Open the terminal")'), "a failed command has no way to the terminal");
    await ui.page.click(".tool-failed .tool-head");
    assert(!(await ui.page.$(".tool-failed .tool-detail-block")), "the failure does not close on click");
    await ui.page.click(".tool-failed .tool-head");
    await ui.shot("turns-failed");
  },

  async "three calls in a row that went through fold into one line"(ui, assert) {
    await openChat(ui);
    await ui.fromHost(envelope("user.message", { text: "look around" }, at(0)));
    await tool(ui, "a", "read_file", "a.cpp", 1, 1.1);
    await tool(ui, "b", "read_file", "b.cpp", 1.1, 1.3);
    assert((await ui.page.$$(".tool-group")).length === 0, "two calls already fold");
    await tool(ui, "c", "search_code", "seam", 1.3, 1.4);
    const head = await ui.page.textContent(".tool-group-head");
    assert(head.includes("3 tools") && head.includes("0.4s"), `group reads ${head}`);
    assert((await ui.page.$$(".tool")).length === 0, "the folded cards still show");
    await ui.page.click(".tool-group-head");
    assert((await ui.page.$$(".tool-group .tool")).length === 3, "opening the group does not show the cards");
  },

  async "the approval leads with allowing once, keys inside the buttons"(ui, assert) {
    await openChat(ui);
    await ui.fromHost({ type: "editorDiff", enabled: true });
    await ui.fromHost(envelope("user.message", { text: "fix it" }, at(0)));
    const old = Array.from({ length: 20 }, (_, i) => `line ${i}`).join("\n");
    await ui.fromHost(
      envelope("tool.approval.requested", {
        call_id: "ap1", tool_name: "edit_code", reason: "Round the sample once, at the seam.",
        arguments: { path: "src/chunk.cpp", old_text: old, new_text: old.replace(/line/g, "row") },
      })
    );
    await ui.page.waitForSelector(".approval-card");
    assert((await ui.page.textContent(".approval-head .tool-badge")) === "waiting for you", "no waiting badge");
    const buttons = await ui.page.$$eval(".approval-actions button", (els) => els.map((e) => [e.className, e.textContent]));
    assert(buttons[0][0].includes("btn-primary") && buttons[0][1].includes("A"), `first button ${buttons[0]}`);
    assert(buttons[2][0].includes("btn-deny") && buttons[2][1].includes("D"), `deny button ${buttons[2]}`);
    assert(!(await ui.page.$(".deny-hint")), "the keys are still spelled out under the buttons");
    const delta = (await ui.page.textContent(".approval-card .line-delta")).replace(/\s+/g, " ");
    assert(delta === "+20 -20", `delta reads ${delta}`);
    assert(await ui.page.$(".approval-card .diff-more"), "the preview is not cut short");
    await ui.clearToHost();
    await ui.page.click('.approval-card button:has-text("Open diff")');
    assert((await ui.toHost("openDiff")).length === 1, "open diff did not ask the editor");
    await ui.shot("turns-approval");

    // In a sidebar's width the file's name is what gives way: the button keeps to one line.
    const wide = await ui.page.$eval('.approval-card button:has-text("Open diff")', (el) => el.getBoundingClientRect().height);
    await ui.page.setViewportSize({ width: 400, height: 900 });
    await ui.page.waitForTimeout(100);
    const fit = await ui.page.$eval('.approval-card .diff-path', (row) => {
      const button = row.querySelector("button").getBoundingClientRect();
      const name = row.querySelector(".diff-path-name");
      return { height: button.height, inside: button.right <= row.getBoundingClientRect().right + 0.5, cut: name.scrollWidth > name.clientWidth };
    });
    assert(Math.abs(fit.height - wide) < 0.5, `narrow, the button is ${fit.height}px tall against ${wide}px`);
    assert(fit.inside, "narrow, the button runs out of the card");
    await ui.shot("turns-approval-narrow");
    await ui.page.setViewportSize({ width: 900, height: 900 });
    await ui.page.keyboard.press("a");
    const resolved = await ui.toHost("resolveApproval");
    assert(resolved.at(-1)?.scope === "once", `A resolved ${JSON.stringify(resolved)}`);
  },

  async "a finished turn ends on a line with its time, tokens and files"(ui, assert) {
    await openChat(ui);
    await ui.fromHost(envelope("usage.updated", { cumulative: { total_tokens: 1000 } }));
    await ui.fromHost(envelope("user.message", { text: "fix the seam" }, at(0)));
    await tool(ui, "e1", "edit_code", "src/chunk.cpp", 1, 2, { lines_added: 3, lines_removed: 1 });
    await ui.fromHost(envelope("planning.step.completed", { latest_verification_passed: true }));
    await ui.fromHost(envelope("usage.updated", { cumulative: { total_tokens: 19400 } }));
    await ui.fromHost(envelope("assistant.final", { text: "Rounded once at the seam." }));
    await ui.fromHost(envelope("turn.completed", {}, at(83)));
    const footer = (await ui.page.textContent(".turn-footer")).replace(/\s+/g, " ");
    for (const want of ["1:23", "18k tokens", "chunk.cpp", "verification passed"]) {
      assert(footer.includes(want), `footer lacks ${want}: ${footer}`);
    }
    assert(await ui.page.$(".block-done"), "the footer kept the box from turning green");
    await ui.shot("turns-footer");
  },

  async "a retry is a banner, gone when the model answers"(ui, assert) {
    await openChat(ui);
    await ui.fromHost(envelope("user.message", { text: "hi" }));
    await ui.fromHost(envelope("status.changed", { state: "CALLING_MODEL" }));
    await ui.fromHost(envelope("model.request.retrying", { attempt: 2, max_attempts: 5, reason: "502 Bad Gateway", delay_s: 4 }));
    const text = await ui.page.textContent(".banner-warn");
    assert(text.includes("2/5") && text.includes("502"), `banner reads ${text}`);
    assert(!(await ui.page.$(".notice-warning")), "the retry still lands in the transcript");
    await ui.shot("turns-retry");
    await ui.fromHost(envelope("model.stream.delta", { text: "Hello" }));
    assert(!(await ui.page.$(".banner")), "the banner outlived the retry");
  },

  async "offline, the banner says why sending is off and reconnects"(ui, assert) {
    await openChat(ui);
    await ui.fromHost(envelope("status.changed", { state: "DISCONNECTED" }));
    await ui.page.fill(".composer textarea", "anyone there?");
    assert(await ui.page.$(".composer-end .send-btn:disabled"), "send is enabled with no backend");
    await ui.clearToHost();
    await ui.page.click('.banner-danger button:has-text("Reconnect")');
    assert((await ui.toHost("restartBridge")).length === 1, "reconnect did not restart the backend");
    await ui.shot("turns-offline");
  },

  async "a new conversation offers what fits the selection, and the recent ones"(ui, assert) {
    await openChat(ui);
    let labels = await ui.page.$$eval(".empty-action-label", (els) => els.map((e) => e.textContent));
    assert(labels.join("|") === "Explain this workspace|Review uncommitted changes", `no selection: ${labels}`);
    await ui.clearToHost();
    await ui.page.click('.empty-action:has-text("Review uncommitted changes")');
    const sent = await ui.toHost("submit");
    assert(sent.length === 1 && /uncommitted/.test(sent[0].text), `sent ${JSON.stringify(sent)}`);

    await ui.fromHost({
      type: "editorContext",
      context: { path: "src/chunk.cpp", language: "cpp", selection: "float s = 0.5f;", startLine: 40, endLine: 52 },
    });
    labels = await ui.page.$$eval(".empty-action-label", (els) => els.map((e) => e.textContent));
    assert(labels.join("|") === "Explain the selection|Refactor the selection", `selection: ${labels}`);
    assert((await ui.page.textContent(".empty-action-hint")) === "src/chunk.cpp:40-52", "the action does not name the selection");
    await ui.shot("turns-empty");
  },
};
