// Conversations kept by the host: what is saved, how little each save carries,
// and how an older panel's history gets there.

let seq = 0;
function envelope(event_type, payload = {}) {
  seq += 1;
  return {
    type: "event",
    event: {
      event_id: `h${seq}`, event_type, event_version: 1, session_id: "s",
      sequence: seq, timestamp: "", source: "app", payload,
    },
  };
}

const shown = (ui) => ui.page.$$eval(".user-text, .assistant-text", (els) => els.map((el) => el.textContent.trim()));
const kept = (ui) => ui.page.evaluate(() => window.__transcripts);

export const tests = {
  async "the host keeps the conversation, each save carrying only what changed"(ui, assert) {
    await ui.page.click(".home-new");
    await ui.page.waitForSelector(".composer");
    await ui.fromHost(envelope("status.changed", { state: "READY" }));
    await ui.fromHost(envelope("user.message", { text: "fix the parser" }));
    await ui.fromHost(envelope("model.stream.delta", { text: "It reads " }));
    await ui.fromHost(envelope("model.stream.delta", { text: "the file once now." }));
    await ui.fromHost(envelope("assistant.final", { text: "It reads the file once now." }));
    await ui.fromHost(envelope("user.message", { text: "and the tests?" }));
    await ui.page.waitForFunction(() => Object.values(window.__transcripts)[0]?.length >= 3);

    const saves = await ui.toHost("saveTranscript");
    assert(saves[0].from === 0, `the first save starts at ${saves[0].from}`);
    assert(saves.slice(1).every((save) => save.from > 0), `a later save sent it all: ${JSON.stringify(saves.map((s) => s.from))}`);
    const state = await ui.page.evaluate(() => window.__state);
    assert(state.conversations.length === 1 && state.conversations[0].items === undefined, "the items are still in the webview's state");
    assert(state.conversations[0].count >= 3, `the list has it at ${state.conversations[0].count} items`);

    // Out and back in: what is shown is what the host kept.
    await ui.page.click('button[title="Back to the history"]');
    await ui.page.click(".history-item");
    await ui.page.waitForFunction(() => document.querySelectorAll(".user-text").length === 2);
    const texts = await shown(ui);
    assert(texts.includes("fix the parser") && texts.includes("It reads the file once now."), JSON.stringify(texts));
  },

  async "an older panel's history moves to the host once, and still opens"(ui, assert) {
    const items = [
      { kind: "user", id: "u1", text: "rename the module" },
      { kind: "assistant", id: "a1", text: "Renamed, and the imports with it.", streaming: false },
    ];
    await ui.page.addInitScript((items) => {
      window.__seedState = {
        conversations: [{ id: "c-old", title: "rename the module", updatedAt: 1, items }],
        activeId: null,
        prefs: { mode: "agent" },
      };
    }, items);
    await ui.page.reload();
    await ui.page.waitForSelector("#root .app");
    await ui.page.waitForFunction(() => window.__transcripts["c-old"]?.length === 2);
    const state = await ui.page.evaluate(() => window.__state);
    assert(state.conversations[0].items === undefined && state.conversations[0].count === 2, JSON.stringify(state.conversations));

    await ui.page.click(".history-item");
    await ui.page.waitForSelector('.assistant-text:has-text("Renamed")');
    assert((await kept(ui))["c-old"].length === 2, "opening it changed what is kept");
  },
};
