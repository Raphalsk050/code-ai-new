// The chat's chrome: header, floating composer, its chips and the context meter.

import { settingsSnapshot } from "./harness.mjs";

let seq = 0;
function envelope(event_type, payload = {}) {
  seq += 1;
  return {
    type: "event",
    event: {
      event_id: `c${seq}`, event_type, event_version: 1, session_id: "s",
      sequence: seq, timestamp: "", source: "app", payload,
    },
  };
}

const long = Array.from({ length: 14 }, (_, i) => `Paragraph ${i + 1}. The chunk edge is sampled twice and rounds differently.`).join("\n\n");

async function openChat(ui) {
  await ui.page.click(".home-new");
  await ui.page.waitForSelector(".composer-wrap");
  await ui.fromHost({ type: "settings", settings: settingsSnapshot({ model: "gemma4:31b-cloud" }) });
  await ui.fromHost(envelope("status.changed", { state: "READY" }));
}

async function busy(ui) {
  await ui.fromHost(envelope("status.changed", { state: "EXECUTING_TOOL" }));
  await ui.fromHost(envelope("tool.call.started", { tool_call_id: "t1", name: "web_search", target: "chunk seam" }));
  await ui.fromHost(envelope("turn.heartbeat", { elapsed_s: 41 }));
}

export const tests = {
  async "the header carries the mark, the title, a status glyph and the context meter"(ui, assert) {
    await openChat(ui);
    await ui.fromHost(envelope("usage.updated", { active_context_tokens: 61000, context_budget: 128000 }));
    const parts = await ui.page.$$eval(".statusbar > *", (els) => els.map((e) => e.className.split(" ")[0]));
    for (const want of ["brand-mark", "chat-title", "live-status", "context-meter", "topbar-actions"]) {
      assert(parts.includes(want), `header lacks .${want}: ${parts.join(",")}`);
    }
    const meter = await ui.page.textContent(".context-meter");
    assert(meter.includes("61k") && meter.includes("128k"), `meter reads ${meter}`);
    await busy(ui);
    const label = await ui.page.getAttribute(".live-status", "aria-label");
    assert(/web_search/.test(label) && /41s/.test(label), `status says ${label}`);
    assert(!(await ui.page.$(".live-status .heartbeat-label")), "the status shows words in the bar");
    await ui.shot("chrome-header-busy");
  },

  async "an unknown context is drawn, not hidden"(ui, assert) {
    await openChat(ui);
    assert(await ui.page.$(".context-meter.unknown"), "no unknown meter before any usage event");
  },

  async "past the threshold the meter is the compact button"(ui, assert) {
    await openChat(ui);
    await ui.fromHost(envelope("usage.updated", { active_context_tokens: 120000, context_budget: 128000, context_threshold: 0.8 }));
    await ui.clearToHost();
    await ui.page.click("button.context-meter.warn");
    const sent = await ui.toHost("compact");
    assert(sent.length === 1, "clicking the warning meter did not compact");
  },

  async "the chips set mode, plan, permissions and model"(ui, assert) {
    await openChat(ui);
    await ui.clearToHost();
    const pick = async (chip, option) => {
      await ui.page.click(`.composer-bar .chip-menu:nth-of-type(${chip}) > .chip`);
      await ui.page.click(`.chip-pop .chip-opt:has-text("${option}")`);
    };
    await pick(2, "plan");
    await pick(3, "Bypass");
    const planner = await ui.toHost("setPlannerMode");
    const perms = await ui.toHost("setPermissionMode");
    assert(planner.at(-1)?.mode === "plan", `planner: ${JSON.stringify(planner)}`);
    assert(perms.at(-1)?.mode === "bypass", `permission: ${JSON.stringify(perms)}`);

    await ui.page.click(".model-chip > .chip");
    assert((await ui.toHost("listModels")).length === 1, "opening the model chip did not list models");
    await ui.fromHost({ type: "modelsListed", models: ["gemma4:31b-cloud", "qwen3-coder:30b"] });
    await ui.shot("chrome-model-menu");
    await ui.page.click('.chip-pop .chip-opt:has-text("qwen3-coder:30b")');
    const saved = await ui.toHost("updateSettings");
    assert(saved.at(-1)?.updates?.model === "qwen3-coder:30b", `model: ${JSON.stringify(saved)}`);

    await ui.page.click(".composer-bar .chip-menu:nth-of-type(1) > .chip");
    await ui.page.click('.chip-pop .chip-opt:has-text("Explain")');
    await ui.page.waitForSelector(".mode-col .mode-foot .composer-bar");
    const back = await ui.page.$(".mode-foot .chip-menu > .chip");
    assert(back, "explain mode has no way back to the agent");
  },

  async "the menu keeps the keyboard: arrows move, Escape closes"(ui, assert) {
    await openChat(ui);
    await ui.page.focus(".composer-bar .chip-menu:nth-of-type(3) > .chip");
    await ui.page.keyboard.press("ArrowDown");
    assert(await ui.page.$(".chip-pop"), "ArrowDown did not open the menu");
    await ui.page.keyboard.press("Escape");
    assert(!(await ui.page.$(".chip-pop")), "Escape did not close the menu");
  },

  async "while the agent works the field wears the moving ring and the button stops"(ui, assert) {
    await openChat(ui);
    await busy(ui);
    assert(await ui.page.$(".composer.busy"), "the composer is not marked busy");
    const ring = await ui.page.$eval(".composer.busy .busy-ring rect", (el) => getComputedStyle(el).animationName);
    assert(ring === "ring-run", `ring animation is ${ring}`);
    const stop = await ui.page.$(".composer-end .send-btn.stop");
    assert(stop, "no stop button in the composer's corner");
    await ui.clearToHost();
    await stop.click();
    assert((await ui.toHost("cancel")).length === 1, "stop did not cancel");
    await ui.shot("chrome-composer-busy");
  },

  async "the ring keeps an even pace round a field of any shape"(ui, assert) {
    // An arc swept by an angle from the field's centre crawls along its long
    // sides and whips round the ends: 5px a step against 140px on this field.
    await openChat(ui);
    await busy(ui);
    for (const width of [900, 440]) {
      await ui.page.setViewportSize({ width, height: 800 });
      await ui.page.waitForTimeout(100);
      const pace = await ui.page.evaluate(() => {
        const field = document.querySelector(".composer").getBoundingClientRect();
        const rects = [...document.querySelectorAll(".busy-ring rect")];
        const peak = rects[rects.length - 1];
        const outline = peak.getTotalLength();
        const run = document.getAnimations().find((a) => a.animationName === "ring-run" && a.effect.target === peak);
        const duration = run.effect.getComputedTiming().duration;
        run.pause();
        // Where the arc's brightest point is on screen, at even steps of a lap.
        const steps = 48;
        const points = [];
        for (let i = 0; i < steps; i++) {
          run.currentTime = (duration * i) / steps;
          const style = getComputedStyle(peak);
          const along = parseFloat(style.getPropertyValue("--ring-from")) - parseFloat(style.strokeDashoffset);
          const at = peak.getPointAtLength((outline * (((along % 100) + 100) % 100)) / 100);
          points.push([at.x, at.y]);
        }
        const hops = points.map(([x, y], i) => {
          const [nx, ny] = points[(i + 1) % steps];
          return Math.hypot(nx - x, ny - y);
        });
        return { shape: field.width / field.height, slowest: Math.min(...hops), fastest: Math.max(...hops), outline };
      });
      assert(pace.shape > 2, `the field is ${pace.shape.toFixed(1)} times as wide as tall: no test of an uneven pace`);
      assert(pace.slowest > 0, "the ring does not move");
      assert(
        pace.fastest / pace.slowest < 1.15,
        `at ${width}px the ring covers ${pace.slowest.toFixed(1)}px to ${pace.fastest.toFixed(1)}px a step`
      );
      // One lap is one cycle: the steps add up to the outline, corners cut a little short.
      const lap = (pace.slowest + pace.fastest) / 2 * 48;
      assert(Math.abs(lap - pace.outline) / pace.outline < 0.05, `48 steps cover ${lap.toFixed(0)}px of ${pace.outline.toFixed(0)}px`);
    }
  },

  async "the send button keeps its corner at every width"(ui, assert) {
    await openChat(ui);
    const corner = async () =>
      ui.page.evaluate(() => {
        const box = document.querySelector(".composer").getBoundingClientRect();
        const btn = document.querySelector(".composer-end .send-btn").getBoundingClientRect();
        return { right: Math.round(box.right - btn.right), bottom: Math.round(box.bottom - btn.bottom) };
      });
    const wide = await corner();
    await ui.page.setViewportSize({ width: 440, height: 800 });
    await ui.page.waitForTimeout(100);
    const narrow = await corner();
    assert(JSON.stringify(wide) === JSON.stringify(narrow), `wide ${JSON.stringify(wide)} narrow ${JSON.stringify(narrow)}`);
    assert(!(await ui.page.$(".statusbar .live-status")), "the narrow header still shows the status");
    await ui.shot("chrome-narrow");
  },

  async "scrolled up, the chat shows through the glass and a button goes back down"(ui, assert) {
    await openChat(ui);
    const items = [1, 2, 3].flatMap((n) => [
      { kind: "user", id: `u${n}`, text: `Request ${n}` },
      { kind: "assistant", id: `a${n}`, text: long, streaming: false },
    ]);
    await ui.fromHost(envelope("client.load", { items }));
    await ui.page.evaluate(() => { document.querySelector(".transcript").scrollTop = 600; });
    await ui.page.dispatchEvent(".transcript", "scroll");
    await ui.page.waitForSelector(".jump-btn");
    const glass = await ui.page.$eval(".composer-wrap", (el) => getComputedStyle(el, "::before").backdropFilter);
    assert(/blur/.test(glass), `no blur behind the composer: ${glass}`);
    const gap = await ui.page.evaluate(() => {
      const box = document.querySelector(".composer").getBoundingClientRect();
      const btn = document.querySelector(".jump-btn");
      const r = btn.getBoundingClientRect();
      const cs = getComputedStyle(btn);
      return { right: Math.round(box.right - r.right), above: Math.round(box.top - r.bottom), css: [cs.position, cs.left, cs.right, cs.transform].join(" ") };
    });
    assert(gap.right >= 0 && gap.right <= 16 && gap.above >= 4, `jump button sits at ${JSON.stringify(gap)}`);
    await ui.shot("chrome-glass");
    await ui.page.click(".jump-btn");
    await ui.page.waitForSelector(".jump-btn", { state: "detached" });
  },
};
