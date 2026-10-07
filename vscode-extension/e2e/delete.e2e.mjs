// Deleting one of the user's messages, with what the agent answered it.

let seq = 0;
function envelope(event_type, payload = {}) {
  seq += 1;
  return {
    type: "event",
    event: {
      event_id: `d${seq}`, event_type, event_version: 1, session_id: "s",
      sequence: seq, timestamp: "", source: "app", payload,
    },
  };
}

/** One request and the agent's turn on it. */
const exchange = (n) => [
  { kind: "user", id: `u${n}`, text: `Request ${n}: fix the parser timeout` },
  {
    kind: "tool", id: `tool${n}`, toolId: `c${n}`, name: "read_file", target: "src/parser.py",
    path: "src/parser.py", status: "done", detail: "",
  },
  { kind: "assistant", id: `a${n}`, text: `Answer ${n}: it reads the file once now.`, streaming: false },
];

async function openChat(ui, items) {
  await ui.page.click(".home-new");
  await ui.page.waitForSelector(".composer");
  await ui.fromHost(envelope("client.load", { items }));
  await ui.fromHost(envelope("status.changed", { state: "READY" }));
  await ui.clearToHost();
}

const shown = (ui) => ui.page.$$eval(".user-text, .assistant-text", (els) => els.map((el) => el.textContent.trim()));
const trash = (n) => `.block-user:has([data-user-msg="u${n}"]) .block-delete`;

export const tests = {
  async "a message is deleted with its answer, on the second click"(ui, assert) {
    await openChat(ui, [...exchange(1), ...exchange(2), ...exchange(3)]);

    await ui.page.hover(`[data-user-msg="u2"]`);
    await ui.page.click(trash(2));
    assert((await shown(ui)).length === 6, "one click deleted it");
    assert((await ui.page.innerText(trash(2))).includes("Delete?"), "the button did not ask");
    await ui.shot("delete-asking");

    await ui.page.click(trash(2));
    const left = await shown(ui);
    assert(
      JSON.stringify(left) ===
        JSON.stringify([
          "Request 1: fix the parser timeout",
          "Answer 1: it reads the file once now.",
          "Request 3: fix the parser timeout",
          "Answer 3: it reads the file once now.",
        ]),
      `left on screen: ${JSON.stringify(left)}`
    );
    assert(!(await ui.page.$('.tool-row:has-text("c2"), [data-tool-id="c2"]')), "its tool call stayed behind");

    // The backend is told which one, counted back from the newest.
    const [sent] = await ui.toHost("deleteMessage");
    assert(sent, "the host was not told");
    assert(sent.text === "Request 2: fix the parser timeout" && sent.fromEnd === 1 && sent.count === 1, JSON.stringify(sent));

    // And the saved transcript no longer has it.
    await ui.page.waitForFunction(() => {
      const saved = window.__state?.conversations?.[0]?.items ?? [];
      return saved.length === 6 && !saved.some((item) => item.id === "u2" || item.id === "a2");
    });
  },

  async "asking lapses by itself, and nothing can be deleted while the agent works"(ui, assert) {
    await openChat(ui, [...exchange(1), ...exchange(2)]);

    await ui.page.hover(`[data-user-msg="u1"]`);
    await ui.page.click(trash(1));
    await ui.page.waitForFunction(() => !document.querySelector(".block-delete.asking"), null, { timeout: 5000 });
    assert((await shown(ui)).length === 4, "it was deleted without the second click");

    await ui.fromHost(envelope("status.changed", { state: "CALLING_MODEL" }));
    assert(!(await ui.page.$(".block-delete")), "a message can be deleted mid-turn");
    assert((await ui.toHost("deleteMessage")).length === 0, "the host was told of a delete that never happened");
  },
};
