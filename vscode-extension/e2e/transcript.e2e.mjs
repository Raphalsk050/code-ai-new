// The chat as boxes by speaker: the user's on the right, the agent's work in
// one box per turn, the request the view is in pinned at the top, and the
// glyph that replaced the old "CI" lettermark.

const BRAND = "M12 3.6c5.3"; // how the new glyph's body path starts
const OLD_GLYPH = "M12.4 7.6"; // the retired lettermark
// A 1x1 PNG: enough for the <img> to decode.
const PNG =
  "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";

let seq = 0;
function envelope(event_type, payload = {}) {
  seq += 1;
  return {
    type: "event",
    event: {
      event_id: `t${seq}`, event_type, event_version: 1, session_id: "s",
      sequence: seq, timestamp: "", source: "app", payload,
    },
  };
}

const prose = (paragraphs) =>
  Array.from({ length: paragraphs }, (_, i) =>
    `Paragraph ${i + 1}. ` + "The parser reads the config and times out on large files. ".repeat(4)
  ).join("\n\n");

/** One request and the agent's whole turn on it. */
function exchange(n, paragraphs = 6) {
  return [
    { kind: "user", id: `u${n}`, text: `Request ${n}: fix the parser timeout` },
    { kind: "thinking", id: `th${n}`, text: "Looking at the parser first.", streaming: false },
    {
      kind: "tool", id: `tool${n}`, toolId: `c${n}`, name: "read_file", target: "src/parser.py",
      path: "src/parser.py", status: "done", detail: "",
    },
    { kind: "assistant", id: `a${n}`, text: prose(paragraphs), streaming: false },
  ];
}

async function openChat(ui) {
  await ui.page.click(".home-new");
  await ui.page.waitForSelector(".transcript");
}

/** Scroll events, the pin's frame and React's commit, all delivered. */
async function settle(ui, ms = 40) {
  await ui.page.evaluate(
    (wait) => new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(() => setTimeout(done, wait)))),
    ms
  );
}

/** Put `selector`'s top `offset` px below the transcript's top edge (negative: above it). */
async function scrollTopTo(ui, selector, offset) {
  await ui.page.evaluate(
    ({ selector, offset }) => {
      const el = document.querySelector(".transcript");
      const target = document.querySelector(selector);
      el.scrollTop += target.getBoundingClientRect().top - el.getBoundingClientRect().top - offset;
    },
    { selector, offset }
  );
  await settle(ui);
}

async function pin(ui) {
  return ui.page.evaluate(() => {
    const button = document.querySelector(".prompt-pin");
    return {
      shown: button.classList.contains("shown"),
      text: button.querySelector(".prompt-pin-text").textContent,
    };
  });
}

export const tests = {
  async "the new glyph is everywhere the old one was"(ui, assert) {
    const heroPath = await ui.page.getAttribute(".home-hero .spark path", "d");
    assert(heroPath.startsWith(BRAND), `home hero draws ${heroPath.slice(0, 16)}`);
    await ui.shot("home-hero");
    await openChat(ui);
    for (const where of [".empty .spark", ".mode-switch"]) {
      const d = await ui.page.getAttribute(`${where} path`, "d");
      assert(d.startsWith(BRAND), `${where} draws ${d.slice(0, 16)}`);
    }
    await ui.fromHost(envelope("client.load", { items: exchange(1, 1) }));
    const avatar = await ui.page.getAttribute(".block-agent .avatar-assistant path", "d");
    assert(avatar.startsWith(BRAND), "the agent's avatar draws the new glyph");
    const old = await ui.page.$$(`path[d^="${OLD_GLYPH}"]`);
    assert(old.length === 0, `${old.length} old lettermark(s) still on screen`);
  },

  async "messages from one side share a box, and the user's sits on the right"(ui, assert) {
    await openChat(ui);
    const items = [
      { kind: "user", id: "u1", text: "Olá" },
      { kind: "user", id: "u2", text: "Tudo bem?" },
      { kind: "thinking", id: "th", text: "The user is greeting me.", streaming: false },
      {
        kind: "tool", id: "tl", toolId: "c1", name: "list_files", target: ".", path: "",
        status: "done", detail: "12 entries",
      },
      { kind: "assistant", id: "a1", text: "Olá!", streaming: false },
      { kind: "assistant", id: "a2", text: "Em que posso ajudar?", streaming: false },
      { kind: "user", id: "u3", text: "Corrija o timeout do parser" },
    ];
    await ui.fromHost(envelope("client.load", { items }));
    await settle(ui);

    const shape = await ui.page.$$eval(".transcript-inner > .block", (blocks) =>
      blocks.map((b) => ({
        side: b.classList.contains("block-user") ? "user" : "agent",
        sends: b.querySelectorAll(".user-text").length,
        parts: b.querySelector(".block-body").children.length,
      }))
    );
    assert(JSON.stringify(shape.map((b) => b.side)) === '["user","agent","user"]', `blocks: ${JSON.stringify(shape)}`);
    assert(shape[0].sends === 2, "both sends in one user box, one line each");
    assert(shape[1].parts === 4, "reasoning, tool and both answers in one agent box");

    const geo = await ui.page.evaluate(() => {
      const inner = document.querySelector(".transcript-inner");
      const box = inner.getBoundingClientRect();
      const right = box.right - parseFloat(getComputedStyle(inner).paddingRight);
      const user = document.querySelector(".block-user").getBoundingClientRect();
      const agent = document.querySelector(".block-agent").getBoundingClientRect();
      return { right, userLeft: user.left, userRight: user.right, agentLeft: agent.left, agentRight: agent.right };
    });
    assert(Math.abs(geo.userRight - geo.right) < 1.5, `user box ends at ${geo.userRight}, column at ${geo.right}`);
    assert(geo.userLeft > geo.agentLeft + 40, "the user's box starts well right of the agent's");
    assert(Math.abs(geo.agentRight - geo.right) < 1.5, "the agent's box takes the column's width");
    await ui.shot("transcript-blocks");

    // The same, in Light Modern: the boxes have to hold up on a white page too.
    await ui.page.addStyleTag({
      content: `:root {
        --vscode-foreground: #3b3b3b; --vscode-editor-background: #ffffff;
        --vscode-editor-foreground: #3b3b3b; --vscode-editorWidget-background: #f8f8f8;
        --vscode-panel-border: #e5e5e5; --vscode-descriptionForeground: #616161;
        --vscode-textLink-foreground: #005fb8; --vscode-input-background: #ffffff;
        --vscode-input-border: #cecece; --vscode-widget-shadow: rgba(0,0,0,.16);
      }`,
    });
    await settle(ui);
    await ui.shot("transcript-blocks-light");
  },

  async "the typing line opens the agent's box, and the reply lands in the same one"(ui, assert) {
    await openChat(ui);
    await ui.fromHost(envelope("client.load", { items: [{ kind: "user", id: "u1", text: "Run the tests" }] }));
    await ui.fromHost(envelope("status.changed", { state: "CALLING_MODEL" }));
    await ui.page.waitForSelector(".block-agent .typing");
    await ui.page.evaluate(() => { document.querySelector(".block-agent").dataset.probe = "same"; });

    await ui.fromHost(envelope("model.thinking.delta", { text: "Which runner?" }));
    await ui.fromHost(envelope("model.stream.delta", { channel: "answer", text: "Running them now." }));
    await settle(ui);
    const blocks = await ui.page.$$eval(".block-agent", (all) => all.map((b) => b.dataset.probe ?? ""));
    assert(blocks.length === 1, `${blocks.length} agent boxes for one turn`);
    assert(blocks[0] === "same", "the box was rebuilt when the first reply arrived");
  },

  async "the pin names the request the view is in, and a click lands on it"(ui, assert) {
    await openChat(ui);
    // The last answer outgrows the panel, so at the end its request is off screen.
    await ui.fromHost(envelope("client.load", { items: [...exchange(1), ...exchange(2), ...exchange(3, 16)] }));
    await settle(ui, 80);

    // A loaded conversation opens at its end: the latest request is the one pinned.
    let state = await pin(ui);
    assert(state.shown && state.text.startsWith("Request 3"), `at the bottom: ${JSON.stringify(state)}`);

    await ui.page.evaluate(() => { document.querySelector(".transcript").scrollTop = 0; });
    await settle(ui);
    assert(!(await pin(ui)).shown, "nothing to pin at the top");
    const heightHidden = await ui.page.evaluate(() => document.querySelector(".transcript").scrollHeight);
    const offsetsHidden = await ui.page.$$eval(".block", (all) => all.map((b) => b.offsetTop));

    // Inside the first answer, then the second.
    await scrollTopTo(ui, '[data-user-block="user:u1"] + .block-agent', -160);
    state = await pin(ui);
    assert(state.shown && state.text.startsWith("Request 1"), `in answer 1: ${JSON.stringify(state)}`);

    // Showing it moved nothing: the pin lies over the transcript.
    const heightShown = await ui.page.evaluate(() => document.querySelector(".transcript").scrollHeight);
    const offsetsShown = await ui.page.$$eval(".block", (all) => all.map((b) => b.offsetTop));
    assert(heightShown === heightHidden, `scroll height ${heightHidden} -> ${heightShown}`);
    assert(JSON.stringify(offsetsShown) === JSON.stringify(offsetsHidden), "a block moved when the pin appeared");
    await settle(ui, 250); // past the fade-in, so the shot shows the pin at rest
    await ui.shot("transcript-pin");

    // The next request reaching the top takes over from the pin, not under it.
    await scrollTopTo(ui, '[data-user-block="user:u2"]', 20);
    assert(!(await pin(ui)).shown, "pinned an old request over the next one");

    await scrollTopTo(ui, '[data-user-block="user:u2"] + .block-agent', -300);
    state = await pin(ui);
    assert(state.shown && state.text.startsWith("Request 2"), `in answer 2: ${JSON.stringify(state)}`);

    // The click: watch every frame of the way back. The pin must stay down
    // the whole way instead of flicking through what it passes.
    const frames = await ui.page.evaluate(
      () =>
        new Promise((done) => {
          const seen = [];
          const button = document.querySelector(".prompt-pin");
          const start = performance.now();
          const tick = () => {
            seen.push(button.classList.contains("shown") ? button.textContent : "");
            if (performance.now() - start < 1200) requestAnimationFrame(tick);
            else done(seen);
          };
          button.click();
          requestAnimationFrame(tick);
        })
    );
    const flickers = frames.filter(Boolean);
    assert(flickers.length === 0, `the pin showed mid-scroll: ${flickers[0]}`);

    const landed = await ui.page.evaluate(() => {
      const el = document.querySelector(".transcript");
      const block = document.querySelector('[data-user-block="user:u2"]');
      return {
        offset: block.getBoundingClientRect().top - el.getBoundingClientRect().top,
        flashed: document.querySelector('[data-user-msg="u2"]').classList.contains("flash"),
      };
    });
    assert(landed.offset >= 0 && landed.offset <= 24, `request 2 landed ${landed.offset}px from the top`);
    assert(!(await pin(ui)).shown, "the pin stays down with its request on screen");
    assert(landed.flashed, "the landing is marked");
    await ui.shot("transcript-landed");
  },

  async "while an answer streams at the bottom, the pin moves forward once and never back"(ui, assert) {
    await openChat(ui);
    await ui.fromHost(
      envelope("client.load", {
        items: [...exchange(1, 8), { kind: "user", id: "u2", text: "Request 2: now add a test" }],
      })
    );
    await ui.fromHost(envelope("status.changed", { state: "CALLING_MODEL" }));
    await settle(ui, 60);

    const seen = [];
    for (let i = 0; i < 40; i++) {
      await ui.fromHost(envelope("model.stream.delta", { channel: "answer", text: `Line ${i} of the new test.\n\n` }));
      await settle(ui, 5);
      const state = await pin(ui);
      const label = state.shown ? state.text.slice(0, 9) : "hidden";
      if (seen[seen.length - 1] !== label) seen.push(label);
    }
    // Request 1 while its answer fills the top, a gap while request 2 passes
    // under the pin's place, then request 2 - in that order, at most once each.
    const order = ["Request 1", "hidden", "Request 2"];
    let at = 0;
    for (const label of seen) {
      const found = order.indexOf(label, at);
      assert(found >= 0, `the pin went back or flickered: ${seen.join(" -> ")}`);
      at = found;
    }
    assert(seen[seen.length - 1] === "Request 2", `ends on ${seen[seen.length - 1]}: ${seen.join(" -> ")}`);
  },

  async "the user's GitHub picture stands in for the icon, and a broken one falls back"(ui, assert) {
    await openChat(ui);
    await ui.fromHost(envelope("client.load", { items: exchange(1, 1) }));
    assert(await ui.page.$(".block-user .avatar-user svg"), "the icon without a profile");

    await ui.fromHost({ type: "profile", login: "rafael", name: "Rafael", avatar: PNG });
    await ui.page.waitForSelector(".block-user img.avatar-photo");
    await ui.shot("transcript-avatar");

    await ui.fromHost({ type: "profile", login: "rafael", name: "Rafael", avatar: "https://example.com/a.png" });
    assert(await ui.page.$(".block-user .avatar-user svg"), "only a data: image is taken");

    await ui.fromHost({ type: "profile", login: "rafael", name: "Rafael", avatar: "data:image/png;base64,AAAA" });
    await ui.page.waitForSelector(".block-user .avatar-user svg");
  },
};
