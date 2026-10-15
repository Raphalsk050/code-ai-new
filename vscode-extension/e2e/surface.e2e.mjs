// The chat moved into a window of its own and back (popOut in src/extension.ts):
// what a page hands over, what the one left behind shows, and how the one
// taking over picks the conversation up.

let seq = 0;
function envelope(event_type, payload = {}) {
  seq += 1;
  return {
    type: "event",
    event: {
      event_id: `w${seq}`, event_type, event_version: 1, session_id: "s",
      sequence: seq, timestamp: "", source: "app", payload,
    },
  };
}

export const tests = {
  async "popping out hands over the list, the preferences and the open conversation"(ui, assert) {
    await ui.page.click(".home-new");
    await ui.page.waitForSelector(".composer");
    await ui.fromHost(envelope("status.changed", { state: "READY" }));
    await ui.fromHost(envelope("user.message", { text: "fix the parser" }));
    await ui.fromHost(envelope("assistant.final", { text: "Fixed." }));
    await ui.clearToHost();

    await ui.fromHost({ type: "popOutRequested" });
    const [handed] = await ui.toHost("popOut");
    assert(handed, "nothing was handed over");
    const active = handed.state.conversations.find((c) => c.id === handed.conversation);
    assert(active && active.count >= 2 && active.title === "fix the parser", JSON.stringify(handed));
    assert(handed.state.prefs, "the preferences stayed behind");
    // What was waiting to be saved reached the host first.
    assert(((await ui.page.evaluate(() => window.__transcripts))[handed.conversation] ?? []).length >= 2, "the host's copy is behind");
  },

  async "the sidebar left behind says where the chat went, and calls it back"(ui, assert) {
    await ui.fromHost({ type: "surface", active: false });
    await ui.page.waitForSelector(".app.elsewhere");
    await ui.clearToHost();
    await ui.page.click('.app.elsewhere button:has-text("Bring it back here")');
    assert((await ui.toHost("popIn")).length === 1, "the host was not asked for the chat back");
    await ui.fromHost({ type: "surface", active: true });
    await ui.page.waitForSelector(".app.elsewhere", { state: "detached" });
  },

  async "the surface taking over opens the conversation without reloading the backend"(ui, assert) {
    const items = [
      { kind: "user", id: "u1", text: "rename the module" },
      { kind: "assistant", id: "a1", text: "Renamed, imports too.", streaming: false },
    ];
    await ui.page.evaluate((items) => { window.__transcripts["c-live"] = items; }, items);
    await ui.clearToHost();
    const state = {
      conversations: [{ id: "c-live", title: "rename the module", updatedAt: 5, count: 2 }],
      activeId: null,
      prefs: { mode: "agent", avatarColor: "green" },
    };
    await ui.fromHost({ type: "surface", active: true, state, conversation: "c-live" });
    await ui.page.waitForSelector('.assistant-text:has-text("Renamed")');
    assert((await ui.toHost("loadConversation")).length === 0, "the backend was told to load it again mid-turn");
    assert((await ui.page.evaluate(() => window.__state.prefs.avatarColor)) === "green", "the preferences were not taken over");
  },
};
