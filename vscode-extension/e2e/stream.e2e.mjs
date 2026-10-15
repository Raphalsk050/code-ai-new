// An answer arriving in fragments: what is on screen stays put, formulas read
// as formulas, and the scroll belongs to the reader.

let seq = 0;
function envelope(event_type, payload = {}) {
  seq += 1;
  return {
    type: "event",
    event: {
      event_id: `s${seq}`, event_type, event_version: 1, session_id: "s",
      sequence: seq, timestamp: "", source: "app", payload,
    },
  };
}

const delta = (text) => envelope("model.stream.delta", { channel: "answer", text });
const paragraph = (n) => `Paragraph ${n}. ` + "The parser reads the config and times out on large files. ".repeat(4) + "\n\n";

async function openChat(ui) {
  await ui.page.click(".home-new");
  await ui.page.waitForSelector(".transcript");
}

async function settle(ui, ms = 40) {
  await ui.page.evaluate(
    (wait) => new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(() => setTimeout(done, wait)))),
    ms
  );
}

/** A turn in progress with `paragraphs` of answer already on screen. */
async function streaming(ui, paragraphs = 14) {
  await openChat(ui);
  await ui.fromHost(envelope("user.message", { text: "Explain the parser" }));
  await ui.fromHost(envelope("status.changed", { state: "CALLING_MODEL" }));
  for (let i = 1; i <= paragraphs; i++) await ui.fromHost(delta(paragraph(i)));
  await settle(ui);
}

const where = (ui) =>
  ui.page.evaluate(() => {
    const el = document.querySelector(".transcript");
    return { top: el.scrollTop, below: el.scrollHeight - el.scrollTop - el.clientHeight };
  });

async function wheel(ui, dy) {
  const box = await ui.page.locator(".transcript").boundingBox();
  await ui.page.mouse.move(box.x + box.width / 2, box.y + box.height / 3);
  await ui.page.mouse.wheel(0, dy);
  await settle(ui, 250); // a wheel scrolls smoothly
}

const jumpShown = (ui) => ui.page.$(".jump-btn").then((el) => el !== null);

export const tests = {
  async "the reasoning box keeps up with the reasoning once it is full"(ui, assert) {
    await openChat(ui);
    await ui.fromHost(envelope("user.message", { text: "Explain the parser" }));
    await ui.fromHost(envelope("status.changed", { state: "CALLING_MODEL" }));
    // Well past its cap: the box itself stops growing long before the text does.
    for (let i = 1; i <= 60; i++) {
      await ui.fromHost(envelope("model.thinking.delta", { text: `Step ${i}: the config is read twice, once per section.\n` }));
    }
    await settle(ui);
    const box = await ui.page.$eval(".thinking-body", (el) => ({
      overflow: el.scrollHeight > el.clientHeight + 50,
      below: el.scrollHeight - el.scrollTop - el.clientHeight,
    }));
    assert(box.overflow, "the reasoning never filled its box");
    assert(box.below < 2, `the reasoning box was left ${box.below}px above its end`);
  },

  async "blocks already on screen are left alone while the answer streams"(ui, assert) {
    await openChat(ui);
    await ui.fromHost(envelope("user.message", { text: "Show me" }));
    await ui.fromHost(envelope("status.changed", { state: "CALLING_MODEL" }));
    await ui.fromHost(delta("# Title\n\n```js\nconst answer = 42;\nreturn answer;\n```\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\nFirst"));

    // Highlighted and numbered in the same paint the block appears in.
    const first = await ui.page.evaluate(() => {
      const pre = document.querySelector(".markdown pre");
      return {
        keyword: !!pre.querySelector(".hljs-keyword"),
        gutter: pre.querySelector(".code-gutter")?.textContent,
        numbered: pre.classList.contains("has-line-numbers"),
      };
    });
    assert(first.keyword && first.numbered && first.gutter === "1\n2", `the code block arrived bare: ${JSON.stringify(first)}`);

    await ui.page.evaluate(() => {
      const blocks = document.querySelectorAll(".markdown > .md-block");
      window.__kept = [blocks[0].firstElementChild, blocks[1].firstElementChild, blocks[2].firstElementChild];
      window.__touched = [];
      new MutationObserver((records) => {
        for (const record of records) {
          const block = (record.target.nodeType === 1 ? record.target : record.target.parentElement)?.closest(".md-block");
          const index = Array.from(document.querySelectorAll(".markdown > .md-block")).indexOf(block);
          window.__touched.push(index);
        }
      }).observe(document.querySelector(".markdown"), { childList: true, subtree: true, characterData: true, attributes: true });
    });

    for (const piece of [" paragraph", " keeps", " growing.", "\n\nAnd a second one", " follows."]) {
      await ui.fromHost(delta(piece));
    }
    await settle(ui);

    const after = await ui.page.evaluate(() => {
      const blocks = document.querySelectorAll(".markdown > .md-block");
      return {
        count: blocks.length,
        same: window.__kept.map((node, i) => blocks[i].firstElementChild === node),
        touched: [...new Set(window.__touched)].sort(),
      };
    });
    assert(after.count === 5, `expected 5 blocks, got ${after.count}`);
    assert(after.same.every(Boolean), `the heading, code or table was rebuilt: ${after.same}`);
    assert(after.touched.every((i) => i < 0 || i >= 3), `finished blocks were touched: ${after.touched}`);
  },

  async "formulas are typeset, and money is left alone"(ui, assert) {
    await openChat(ui);
    const text = [
      "A energia de repouso é $E = mc^2$, e custa $5 ou $10 medir.",
      "",
      "$$",
      "x = \\frac{-b \\pm \\sqrt{b^2 - 4ac}}{2a}",
      "$$",
      "",
      "Em forma de soma: \\( \\sum_{i=1}^{n} i = \\frac{n(n+1)}{2} \\), e a integral",
      "",
      "\\[ \\int_0^\\infty e^{-x^2}\\,dx = \\frac{\\sqrt{\\pi}}{2} \\]",
    ].join("\n");
    await ui.fromHost(
      envelope("client.load", {
        items: [
          { kind: "user", id: "u1", text: "Mostre as fórmulas" },
          { kind: "assistant", id: "a1", text, streaming: false },
        ],
      })
    );
    await ui.page.evaluate(() => document.fonts.ready);
    await settle(ui, 200);

    const seen = await ui.page.evaluate(() => {
      const root = document.querySelector(".markdown");
      return {
        inline: root.querySelectorAll(".math-inline .katex").length,
        display: root.querySelectorAll(".math-display .katex-display").length,
        // Laid out as a fraction: two lines tall, not a/b on one.
        stacked: root.querySelector(".math-display").getBoundingClientRect().height > 34,
        font: getComputedStyle(root.querySelector(".katex")).fontFamily,
        fontsLoaded: Array.from(document.fonts).filter((f) => f.family.includes("KaTeX") && f.status === "loaded").length,
        money: root.textContent.includes("custa $5 ou $10 medir"),
        raw: /\\frac|\\sum|\$\$/.test(root.textContent.replace(/custa \$5 ou \$10/, "")),
      };
    });
    assert(seen.inline === 2, `inline formulas typeset: ${seen.inline}`);
    assert(seen.display === 2, `display formulas typeset: ${seen.display}`);
    assert(seen.stacked, "the fraction is not laid out as one");
    assert(/KaTeX_Main/.test(seen.font) && seen.fontsLoaded > 0, `KaTeX's fonts did not load: ${seen.font} (${seen.fontsLoaded})`);
    assert(seen.money, "the prices were read as a formula");
    assert(!seen.raw, "LaTeX source is still showing");
    await ui.shot("stream-formulas");
  },

  async "half a formula shows its source until it closes"(ui, assert) {
    await openChat(ui);
    await ui.fromHost(envelope("user.message", { text: "Integral" }));
    await ui.fromHost(envelope("status.changed", { state: "CALLING_MODEL" }));
    await ui.fromHost(delta("Resultado:\n\n$$\n\\int_0^1 x^2"));
    assert(await ui.page.$(".math-pending .math-source"), "an open formula is not shown as source");
    assert(!(await ui.page.$(".katex")), "half a formula was typeset");
    await ui.fromHost(delta("\\,dx = \\frac{1}{3}\n$$\n"));
    assert(await ui.page.$(".math-display .katex-display"), "the closed formula was not typeset");
    assert(!(await ui.page.$(".math-pending")), "the pending source stayed");
  },

  async "one notch up while the answer streams is never taken back"(ui, assert) {
    await streaming(ui);
    let at = await where(ui);
    assert(at.below <= 1, `not following from the start: ${at.below}px below`);
    assert(!(await jumpShown(ui)), "the way back is offered at the end");

    // Less than the 80px the old rule snapped back from.
    await wheel(ui, -40);
    at = await where(ui);
    assert(at.below > 10 && at.below < 80, `the notch moved the view ${at.below}px from the end`);
    const top = at.top;

    for (let i = 20; i < 40; i++) await ui.fromHost(delta(paragraph(i)));
    await settle(ui);
    at = await where(ui);
    assert(Math.abs(at.top - top) <= 1, `the stream moved the reader from ${top} to ${at.top}`);
    assert(at.below > 500, "nothing arrived below");
    assert(await jumpShown(ui), "no way back offered while away");

    // Further up, mid-stream: still theirs.
    await wheel(ui, -600);
    const higher = (await where(ui)).top;
    assert(higher < top - 300, `the reader could not scroll further up: ${higher}`);
    for (let i = 40; i < 50; i++) await ui.fromHost(delta(paragraph(i)));
    await settle(ui);
    assert(Math.abs((await where(ui)).top - higher) <= 1, "the stream moved the reader on the second scroll");
  },

  async "coming back down to the end follows again"(ui, assert) {
    await streaming(ui);
    await wheel(ui, -400);
    assert(await jumpShown(ui), "not away after scrolling up");

    // Part of the way down is still away.
    await wheel(ui, 150);
    await ui.fromHost(delta(paragraph(30)));
    await settle(ui);
    assert((await where(ui)).below > 100, "followed before reaching the end");

    await wheel(ui, 5000);
    await ui.fromHost(delta(paragraph(31)));
    await ui.fromHost(delta(paragraph(32)));
    await settle(ui);
    const at = await where(ui);
    assert(at.below <= 1, `back at the end but not following: ${at.below}px below`);
    assert(!(await jumpShown(ui)), "the way back is still offered at the end");
  },

  async "the button and a new message both bring the end back"(ui, assert) {
    await streaming(ui);
    await wheel(ui, -500);
    await ui.page.click(".jump-btn");
    await settle(ui, 600); // the smooth scroll
    await ui.fromHost(delta(paragraph(40)));
    await settle(ui);
    assert((await where(ui)).below <= 1, "the button did not bring the end back");

    await wheel(ui, -500);
    await ui.page.fill("textarea", "and the tests?");
    await ui.page.keyboard.press("Enter");
    await ui.fromHost(envelope("user.message", { text: "and the tests?" }));
    await settle(ui);
    assert((await where(ui)).below <= 1, "sending did not bring the end back");
  },

  async "page up counts as scrolling up, typing in the composer does not"(ui, assert) {
    await streaming(ui);
    await ui.page.focus("textarea");
    await ui.page.keyboard.press("ArrowUp");
    await ui.fromHost(delta(paragraph(20)));
    await settle(ui);
    assert((await where(ui)).below <= 1, "a key in the composer stopped the follow");

    await ui.page.evaluate(() => document.activeElement.blur());
    await ui.page.locator(".transcript").hover();
    await ui.page.mouse.click(450, 200);
    await ui.page.keyboard.press("PageUp");
    await settle(ui, 300);
    const top = (await where(ui)).top;
    for (let i = 21; i < 30; i++) await ui.fromHost(delta(paragraph(i)));
    await settle(ui);
    const at = await where(ui);
    assert(at.below > 100 && Math.abs(at.top - top) <= 1, `page up was taken back: ${JSON.stringify(at)} from ${top}`);
  },

  async "a selection being dragged is not scrolled out from under the pointer"(ui, assert) {
    await streaming(ui);
    const box = await ui.page.locator(".transcript").boundingBox();
    await ui.page.mouse.move(box.x + 200, box.y + 200);
    await ui.page.mouse.down();
    const top = (await where(ui)).top;
    for (let i = 20; i < 26; i++) await ui.fromHost(delta(paragraph(i)));
    await settle(ui);
    assert(Math.abs((await where(ui)).top - top) <= 1, "the text moved under a pressed pointer");

    await ui.page.mouse.up();
    await settle(ui);
    assert((await where(ui)).below <= 1, "the follow did not resume on release");
  },

  async "the answer getting shorter does not stop the follow"(ui, assert) {
    await streaming(ui, 20);
    // A retry takes back what a broken attempt streamed.
    await ui.fromHost(
      envelope("client.load", {
        items: [
          { kind: "user", id: "u1", text: "Explain the parser" },
          { kind: "assistant", id: "a1", text: paragraph(1).repeat(9), streaming: true },
        ],
      })
    );
    await settle(ui);
    for (let i = 0; i < 8; i++) await ui.fromHost(delta(paragraph(i)));
    await settle(ui);
    const at = await where(ui);
    assert(at.below <= 1, `stopped following after the content shrank: ${at.below}px below`);
    assert(!(await jumpShown(ui)), "offered a way back though still at the end");
  },
};
