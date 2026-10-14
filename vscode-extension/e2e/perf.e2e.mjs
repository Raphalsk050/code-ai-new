// A long conversation must not make the panel, or the editor around it, slow.
// Each spec checks a cost that grew with the conversation before it was cut:
// measured in headless Chromium, with software rendering on a shared machine,
// so the limits sit well above what a run takes and only catch a regression.

const TURNS = 300;

let seq = 0;
function envelope(event_type, payload = {}) {
  seq += 1;
  return {
    type: "event",
    event: {
      event_id: `p${seq}`, event_type, event_version: 1, session_id: "s",
      sequence: seq, timestamp: "", source: "app", payload,
    },
  };
}

/** A turn the way the panel keeps it: a request, some thinking, tool calls, and a markdown answer. */
function turn(n) {
  const code = "```python\ndef parse(path):\n    with open(path) as f:\n        return f.read()\n```";
  return [
    { kind: "user", id: `u${n}`, text: `Request ${n}: make the parser read the file once` },
    { kind: "thinking", id: `th${n}`, text: "The parser opens the file twice.", streaming: false },
    {
      kind: "tool", id: `t${n}`, toolId: `c${n}`, name: "read_file", target: "src/parser.py",
      path: "src/parser.py", status: "done", detail: "",
    },
    {
      kind: "assistant", id: `a${n}`, streaming: false,
      text: `## Turn ${n}\n\nThe parser now reads the file **once**.\n\n${code}\n\n| file | change |\n|---|---|\n| parser.py | one read |\n\n- faster\n- simpler`,
    },
  ];
}

/** A conversation of `TURNS` turns, kept by the host, opened from the history. */
async function openLong(ui) {
  const items = Array.from({ length: TURNS }, (_, i) => turn(i + 1)).flat();
  await ui.page.addInitScript((items) => {
    window.__seedTranscripts = { "c-long": items };
    window.__seedState = {
      conversations: [{ id: "c-long", title: "make the parser read the file once", updatedAt: 1, count: items.length }],
      activeId: null,
      prefs: { mode: "agent" },
    };
    window.__long = [];
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) window.__long.push(Math.round(entry.duration));
    }).observe({ type: "longtask", buffered: false });
  }, items);
  await ui.page.reload();
  await ui.page.waitForSelector("#root .app");
  await ui.fromHost(envelope("status.changed", { state: "READY" }));
  const started = Date.now();
  await ui.page.click(".history-item");
  await ui.page.waitForSelector('.assistant-text:has-text("Turn 300")');
  const firstScreen = Date.now() - started;
  // Every turn drawn, the oldest last.
  await ui.page.waitForSelector('.assistant-text:has-text("Turn 1.")', { state: "attached", timeout: 60_000 }).catch(() => undefined);
  await ui.page.waitForFunction((n) => document.querySelectorAll(".block-user").length === n, TURNS, { timeout: 60_000 });
  return { firstScreen, items };
}

const longTasks = (ui) => ui.page.evaluate(() => window.__long.splice(0));

export const tests = {
  async "a long conversation opens without holding the panel up"(ui, assert) {
    const { firstScreen } = await openLong(ui);
    const long = await longTasks(ui);
    assert(firstScreen < 3000, `the newest turn took ${firstScreen} ms to show`);
    // Drawn a slice at a time: no single task anywhere near the whole of it.
    assert(Math.max(0, ...long) < 600, `opening blocked for ${Math.max(...long)} ms at once (${long.length} long tasks)`);
  },

  async "an entrance leaves nothing behind once it is over"(ui, assert) {
    await openLong(ui);
    await ui.page.waitForTimeout(800);
    // A fill that outlives the animation keeps a compositing layer per message.
    const kept = await ui.page.evaluate(
      () => document.getAnimations().filter((a) => a.playState === "finished").length
    );
    assert(kept < 10, `${kept} finished animations still hold their elements`);
  },

  async "an answer streams into a long conversation without long tasks"(ui, assert) {
    await openLong(ui);
    await longTasks(ui);
    await ui.fromHost(envelope("user.message", { text: "and the tests?" }));
    await ui.fromHost(envelope("status.changed", { state: "CALLING_MODEL" }));
    const words = "The tests now cover the single read and the error path. ".split(" ");
    for (let i = 0; i < 200; i++) {
      await ui.fromHost(envelope("model.stream.delta", { text: words[i % words.length] + " " }));
    }
    await ui.fromHost(envelope("status.changed", { state: "READY" }));
    await ui.page.waitForTimeout(300);
    const long = await longTasks(ui);
    assert(long.length <= 3, `${long.length} long tasks while streaming: ${JSON.stringify(long)}`);
  },

  async "the panel's own saved state stays small however long the conversation"(ui, assert) {
    await openLong(ui);
    await ui.fromHost(envelope("user.message", { text: "one more" }));
    await ui.page.waitForFunction(() => window.__state?.conversations?.[0]?.count > 0);
    const bytes = await ui.page.evaluate(() => JSON.stringify(window.__state).length);
    assert(bytes < 16_000, `the webview state is ${bytes} bytes`);
  },

  async "left for the editor with nothing running, the avatars hold still"(ui, assert) {
    await ui.page.click(".home-new");
    await ui.page.waitForSelector(".composer");
    await ui.fromHost(envelope("status.changed", { state: "READY" }));
    await ui.page.evaluate(() => {
      document.hasFocus = () => false;
      window.dispatchEvent(new Event("blur"));
    });
    await ui.page.waitForSelector(":root[data-still]");
    // A handover already under way (avatar-blend.ts) finishes its 360 ms.
    await ui.page.waitForTimeout(500);
    const running = await ui.page.evaluate(
      () => document.getAnimations().filter((a) => a.playState === "running" && a.effect?.target?.closest?.(".agent-av, .av-float")).length
    );
    assert(running === 0, `${running} avatar animations still run`);

    // A turn starting brings them back.
    await ui.fromHost(envelope("status.changed", { state: "CALLING_MODEL" }));
    await ui.page.waitForSelector(":root:not([data-still])");
  },
};
