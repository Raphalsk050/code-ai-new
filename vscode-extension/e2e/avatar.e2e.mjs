// The avatar loose in the panel: where it sits, how it is moved, what it wears.

import { choose } from "./dropdown.mjs";
import { settingsSnapshot } from "./harness.mjs";

const FLOAT = ".av-float";
const GRIP = ".av-float-grip";
const FIELD = ".composer textarea";
const ANXIOUS = `${FLOAT} .agent-av[data-pose="anxious"]`;
const IDLE = `${FLOAT} .agent-av[data-pose="idle"]`;
/** The floating one in the middle of an act, and with none on. */
const acting = (name) => `${FLOAT}[data-act="${name}"]`;
const AT_EASE = `${FLOAT}:not([data-act])`;
const SWITCH = (label) => `button[role="switch"][aria-label="${label}"]`;

let seq = 0;
function envelope(event_type, payload = {}) {
  seq += 1;
  return {
    type: "event",
    event: {
      event_id: `a${seq}`, event_type, event_version: 1, session_id: "s",
      sequence: seq, timestamp: "", source: "app", payload,
    },
  };
}

/** One of the numbers the floating avatar is drawn by. */
const drawn = (ui, name) => ui.page.$eval(FLOAT, (el, prop) => parseFloat(el.style.getPropertyValue(prop) || "0"), name);
/** What moves a glyph's eyes: the name of a loop, or none when a script has them. */
const eyes = (ui, scope) => ui.page.$eval(`${scope} .agent-av-eyes`, (el) => getComputedStyle(el).animationName);

async function type(ui, text) {
  await ui.page.click(FIELD);
  await ui.page.keyboard.type(text, { delay: 60 });
}
const COSTUME = '.settings-field:has(.settings-field-label:text-is("Avatar costume"))';

/** Where its box is once it has stopped moving. */
async function settle(ui) {
  let still = 0;
  let last = null;
  for (let i = 0; i < 100; i++) {
    const box = await ui.page.locator(FLOAT).boundingBox();
    still = last && Math.abs(box.x - last.x) < 0.1 && Math.abs(box.y - last.y) < 0.1 ? still + 1 : 0;
    if (still === 3) return box;
    last = box;
    await ui.page.waitForTimeout(50);
  }
  throw new Error("the avatar never came to rest");
}

/** Carry it by the face and put it down: held still before letting go, so it is not a throw. */
async function drag(ui, dx, dy) {
  const grip = await ui.page.locator(GRIP).boundingBox();
  const from = { x: grip.x + grip.width / 2, y: grip.y + grip.height / 2 };
  await ui.page.mouse.move(from.x, from.y);
  await ui.page.mouse.down();
  for (let i = 1; i <= 10; i++) {
    await ui.page.mouse.move(from.x + (dx * i) / 10, from.y + (dy * i) / 10);
    await ui.page.waitForTimeout(16);
  }
  await ui.page.waitForTimeout(250);
  await ui.page.mouse.up();
}

async function openChat(ui) {
  await ui.page.click(".home-new");
  await ui.page.waitForSelector(".composer");
  return settle(ui);
}

async function openSettings(ui) {
  await ui.fromHost({ type: "settings", settings: settingsSnapshot() });
  await ui.page.click('button[title="Settings"]');
  await ui.page.waitForSelector(".settings-body");
  await ui.page.click('.settings-nav-item:has-text("Agent")');
}

const near = (a, b) => Math.abs(a.x - b.x) < 1.5 && Math.abs(a.y - b.y) < 1.5;

/** The most a number it is drawn by gets to, for as long as an act is on. */
async function mostWhile(ui, act, name) {
  let most = -Infinity;
  while (await ui.page.$(acting(act))) {
    most = Math.max(most, await drawn(ui, name));
    await ui.page.waitForTimeout(40);
  }
  return most;
}

/** A turn, as the bridge reports one: asked, worked on (or not), and ended. */
async function turn(ui, ended, { tool = false } = {}) {
  await ui.fromHost(envelope("user.message", { text: "do it" }));
  await ui.fromHost(envelope("status.changed", { state: "CALLING_MODEL" }));
  if (tool) {
    await ui.fromHost(envelope("status.changed", { state: "EXECUTING_TOOL" }));
    await ui.fromHost(envelope("tool.call.started", { tool_call_id: `t${seq}`, name: "edit_code", target: "src/parser.py" }));
    await ui.fromHost(envelope("tool.call.completed", { tool_call_id: `t${seq - 1}` }));
    await ui.fromHost(envelope("status.changed", { state: "CALLING_MODEL" }));
  }
  await ui.fromHost(envelope("status.changed", { state: ended }));
}

/**
 * Thrown: one move a frame, and let go on the frame that saw the last. Done
 * from inside the page, or how fast it leaves the hand would depend on how
 * long the test takes to say so.
 */
async function fling(ui, step, moves) {
  const grip = await ui.page.locator(GRIP).boundingBox();
  const from = { x: grip.x + grip.width / 2, y: grip.y + grip.height / 2 };
  await ui.page.mouse.move(from.x, from.y);
  await ui.page.mouse.down();
  await ui.page.waitForTimeout(350);
  await ui.page.evaluate(
    ([selector, from, step, moves]) =>
      new Promise((done) => {
        const grip = document.querySelector(selector);
        const tell = (type, i) =>
          grip.dispatchEvent(new PointerEvent(type, { bubbles: true, clientX: from.x + step * i, clientY: from.y }));
        let made = 0;
        const frame = () => {
          if (made === moves) {
            tell("pointerup", made);
            done();
            return;
          }
          made += 1;
          tell("pointermove", made);
          requestAnimationFrame(frame);
        };
        requestAnimationFrame(frame);
      }),
    [GRIP, from, step, moves]
  );
  await ui.page.mouse.up();
}

async function askApproval(ui) {
  await ui.fromHost(envelope("user.message", { text: "clean up" }));
  await ui.fromHost(envelope("status.changed", { state: "WAITING_APPROVAL" }));
  await ui.fromHost(
    envelope("tool.approval.requested", { call_id: "ap", tool_name: "execute_command", arguments: { command: "rm -rf build" } })
  );
  await ui.page.waitForSelector(".approval-card");
}

/** The middle of its face, and of the seat the home screen keeps for it. */
const faceOf = (box) => ({ x: box.x + box.width * 0.464, y: box.y + box.height * 0.571 });
async function seatOf(ui) {
  const seat = await ui.page.locator(".spark-dock").boundingBox();
  return { x: seat.x + seat.width / 2, y: seat.y + seat.height / 2 };
}

export const tests = {
  async "it floats in its corner and goes where it is carried"(ui, assert) {
    const home = await openChat(ui);
    const panel = ui.page.viewportSize();
    assert(home.x > panel.width / 2 && home.y > panel.height / 2, "it starts in the bottom right");
    const composer = await ui.page.locator(".composer").boundingBox();
    assert(home.y + home.height <= composer.y, "its corner is above the composer");

    await drag(ui, -300, -250);
    const put = await settle(ui);
    assert(near(put, { x: home.x - 300, y: home.y - 250 }), `put down at ${put.x},${put.y}`);
    await ui.shot("avatar-floating");
  },

  async "thrown, it keeps going and stays inside the panel"(ui, assert) {
    const home = await openChat(ui);
    const grip = await ui.page.locator(GRIP).boundingBox();
    const from = { x: grip.x + grip.width / 2, y: grip.y + grip.height / 2 };
    await ui.page.mouse.move(from.x, from.y);
    await ui.page.mouse.down();
    for (let i = 1; i <= 8; i++) {
      await ui.page.mouse.move(from.x - i * 40, from.y - i * 30);
      await ui.page.waitForTimeout(12);
    }
    const released = await ui.page.locator(FLOAT).boundingBox();
    await ui.page.mouse.up();
    const rest = await settle(ui);
    assert(rest.x < released.x - 20, `it stopped where it was let go: ${released.x} -> ${rest.x}`);
    const panel = ui.page.viewportSize();
    assert(rest.x >= 0 && rest.y >= 0 && rest.x + rest.width <= panel.width && rest.y + rest.height <= panel.height, "it left the panel");
    assert(rest.x < home.x, "it went the way it was thrown");
  },

  async "where it was left is remembered, on every screen"(ui, assert) {
    await openChat(ui);
    await drag(ui, -200, -450);
    const left = await settle(ui);
    await ui.page.waitForFunction(() => window.__state?.prefs?.avatarAt);
    const place = await ui.page.evaluate(() => window.__state.prefs.avatarAt);
    assert(place.right === true && place.bottom === false, `measured from the top right: ${JSON.stringify(place)}`);

    await ui.page.click('button[title="Back to the history"]');
    await ui.page.waitForSelector(".home-new");
    assert(near(faceOf(await settle(ui)), await seatOf(ui)), "on the home screen it did not take its seat");
    await ui.page.click(".home-new");
    await ui.page.waitForSelector(".composer");
    assert(near(await settle(ui), left), "back in a conversation it did not return to where it was left");
    const kept = await ui.page.evaluate(() => window.__state.prefs.avatarAt);
    assert(JSON.stringify(kept) === JSON.stringify(place), "its seat was remembered as its place");
  },

  async "on the home screen it sits where the mark was, and goes back there when moved"(ui, assert) {
    const seated = await settle(ui);
    assert(near(faceOf(seated), await seatOf(ui)), "it is not on its seat");
    assert(!(await ui.page.$(".home-hero .spark .agent-av")), "the mark is still drawn under it");

    await drag(ui, 200, 260);
    assert(near(await settle(ui), seated), "let go, it did not go back to its seat");
    assert(!(await ui.page.evaluate(() => window.__state?.prefs?.avatarAt)), "a seat is not a place to remember");
    await ui.shot("avatar-seated");
  },

  async "on its seat it scrolls with the home screen, and comes back with it"(ui, assert) {
    // A panel short enough for the home screen to scroll, as a long history makes it.
    await ui.page.setViewportSize({ width: 900, height: 300 });
    await ui.page.waitForTimeout(150);
    await settle(ui);
    assert(near(faceOf(await settle(ui)), await seatOf(ui)), "it is not on its seat to begin with");
    const scrollTo = (top) =>
      ui.page.evaluate((top) => {
        const home = document.querySelector(".home");
        home.scrollTop = top;
        return new Promise((done) => requestAnimationFrame(() => done(home.scrollTop)));
      }, top);

    const scrolled = await scrollTo(120);
    assert(scrolled > 40, `the home screen does not scroll here (${scrolled})`);
    const away = await ui.page.locator(FLOAT).boundingBox();
    assert(near(faceOf(away), await seatOf(ui)), "it stayed behind, over what scrolled under it");

    // Moved while the page is scrolled, it still goes back to its seat, not off the screen for good.
    await scrollTo(30);
    await drag(ui, 150, 60);
    assert(near(faceOf(await settle(ui)), await seatOf(ui)), "let go, it did not go back to its seat");
    await scrollTo(0);
    assert(near(faceOf(await ui.page.locator(FLOAT).boundingBox()), await seatOf(ui)), "scrolled back, it is not where it sat");
  },

  async "the composer is a floor: it is never under the glass"(ui, assert) {
    await openChat(ui);
    await drag(ui, -200, 400);
    const rest = await settle(ui);
    const composer = await ui.page.locator(".composer-wrap").boundingBox();
    assert(rest.y + rest.height <= composer.y + 0.5, `it went ${rest.y + rest.height - composer.y}px under the composer`);
  },

  async "a panel that changes size keeps it by the same corner"(ui, assert) {
    const floor = async () => (await ui.page.locator(".composer-wrap").boundingBox()).y;
    const before = await openChat(ui);
    const above = (await floor()) - before.y;
    await ui.page.setViewportSize({ width: 620, height: 700 });
    await ui.page.waitForTimeout(100);
    const after = await settle(ui);
    // Its corner is the right edge and the composer's top, wherever those now are.
    assert(near({ x: 900 - before.x, y: above }, { x: 620 - after.x, y: (await floor()) - after.y }), "it did not follow its corner");
  },

  async "only the face takes the pointer"(ui, assert) {
    const box = await openChat(ui);
    const takes = (x, y) => ui.page.evaluate(([x, y]) => !!document.elementFromPoint(x, y)?.closest(".av-float"), [x, y]);
    assert(await takes(box.x + box.width * 0.46, box.y + box.height * 0.57), "the face cannot be grabbed");
    assert(!(await takes(box.x + 3, box.y + 3)), "the corner of its box takes a click meant for the panel");
    assert(!(await takes(box.x + box.width - 3, box.y + box.height - 3)), "the corner of its box takes a click meant for the panel");
  },

  async "keys move it too, and Home sends it back"(ui, assert) {
    const home = await openChat(ui);
    await ui.page.focus(GRIP);
    await ui.page.keyboard.press("ArrowLeft");
    const pushed = await settle(ui);
    assert(pushed.x < home.x - 20, "an arrow did not push it");
    await ui.page.keyboard.press("Home");
    assert(near(await settle(ui), home), "Home did not send it back");
  },

  async "the settings switch it off, and it stays off"(ui, assert) {
    await openSettings(ui);
    assert(await ui.page.$(FLOAT), "it is there to begin with");
    await ui.page.click('button[role="switch"][aria-label="Floating avatar"]');
    await ui.page.waitForSelector(FLOAT, { state: "detached" });
    assert((await ui.page.evaluate(() => window.__state.prefs.floatingAvatar)) === false, "the choice was not saved");
  },

  async "it wears what the settings say"(ui, assert) {
    await openSettings(ui);
    await choose(ui, COSTUME,"halloween");
    await ui.page.waitForSelector(`${FLOAT} .agent-av[data-skin="halloween"]`);
    await ui.shot("avatar-costume");
    await choose(ui, COSTUME,"default");
    await ui.page.waitForSelector(`${FLOAT} .agent-av:not([data-skin])`);
  },

  async "idle, it looks at the pointer, and stops when told not to"(ui, assert) {
    const box = await openChat(ui);
    const look = () => ui.page.$eval(FLOAT, (el) => parseFloat(el.style.getPropertyValue("--av-look-x") || "0"));
    const face = { x: box.x + box.width * 0.46, y: box.y + box.height * 0.57 };

    await ui.page.mouse.move(face.x - 300, face.y);
    await ui.page.waitForFunction(() => document.querySelector(".av-float").hasAttribute("data-watching"));
    await settle(ui);
    await ui.page.waitForTimeout(600);
    assert((await look()) < -1.2, `the eyes did not go left: ${await look()}`);

    await ui.page.mouse.move(face.x + 80, face.y - 200);
    await ui.page.waitForTimeout(700);
    assert((await look()) > 0.3, `the eyes did not come across: ${await look()}`);
    await ui.shot("avatar-watching");

    // The pointer leaves the page: back to its own business.
    await ui.page.evaluate(() => document.documentElement.dispatchEvent(new MouseEvent("mouseleave")));
    await ui.page.waitForFunction(() => !document.querySelector(".av-float").hasAttribute("data-watching"));
    await ui.page.waitForTimeout(700);
    assert(Math.abs(await look()) < 0.05, `the eyes stayed where the pointer was: ${await look()}`);

    await ui.page.click('button[title="Back to the history"]');
    await openSettings(ui);
    await ui.page.click('button[role="switch"][aria-label="Avatar looks at the pointer"]');
    await ui.page.mouse.move(200, 200);
    await ui.page.waitForTimeout(500);
    assert(!(await ui.page.$(`${FLOAT}[data-watching]`)), "it watches although it was told not to");
    assert((await ui.page.evaluate(() => window.__state.prefs.avatarWatches)) === false, "the choice was not saved");
  },

  async "it is drawn in the colour picked in the settings"(ui, assert) {
    await openSettings(ui);
    assert(!(await ui.page.$(".agent-av[data-color]")), "a colour before one was picked");
    await ui.page.click('.swatch[aria-label="Green"]');
    await ui.page.waitForSelector(`${FLOAT} .agent-av[data-color="green"]`);
    const drawn = await ui.page.$eval(`${FLOAT} .agent-av`, (el) => getComputedStyle(el).color);
    assert(drawn === "rgb(46, 160, 67)", `drawn in ${drawn}`);
    assert((await ui.page.evaluate(() => window.__state.prefs.avatarColor)) === "green", "the choice was not saved");
    await ui.shot("avatar-colour");
    await ui.page.click(`.swatch[aria-label="Theme's accent"]`);
    await ui.page.waitForSelector(`${FLOAT} .agent-av:not([data-color])`);
  },

  async "left to the calendar, it dresses for the season"(ui, assert) {
    await ui.page.clock.setFixedTime(new Date(2026, 11, 10, 12));
    await ui.page.reload();
    await ui.page.waitForSelector(`${FLOAT} .agent-av[data-skin="christmas"]`);
    await ui.shot("avatar-christmas");
    await ui.page.clock.setFixedTime(new Date(2026, 6, 10, 12));
    await ui.page.reload();
    await ui.page.waitForSelector(`${FLOAT} .agent-av:not([data-skin])`);
    assert(!(await ui.page.$(".agent-av[data-skin]")), "a costume out of season");
  },

  async "typed at, it is anxious and watches the caret"(ui, assert) {
    await openChat(ui);
    assert((await ui.page.$$(`${FLOAT} .agent-av-fx-sweat`)).length === 1, "it has no drop to sweat");
    await type(ui, "Refactor the parser");
    await ui.page.waitForSelector(ANXIOUS);
    await ui.page.waitForSelector(`${FLOAT}[data-rapt]`);
    await ui.page.waitForTimeout(400);
    // The composer is below it, and what is written there to its left.
    const look = { x: await drawn(ui, "--av-look-x"), y: await drawn(ui, "--av-look-y") };
    assert(look.x < -0.4 && look.y > 0.2, `it looks to ${look.x}, ${look.y}`);
    assert((await drawn(ui, "--av-nerves")) > 0.2, "typing did not wind it up");
    assert((await eyes(ui, FLOAT)) === "none", "the loop moves its eyes while it watches the caret");
    // The one in the header has no keys to count: it plays the loop.
    await ui.page.waitForSelector('.brand-live .agent-av[data-pose="anxious"]');
    assert((await eyes(ui, ".brand-live")) === "av-a-gaze", `the small one plays ${await eyes(ui, ".brand-live")}`);
    await ui.shot("avatar-anxious");
  },

  async "more typing winds it up until it sweats"(ui, assert) {
    await openChat(ui);
    const sweat = () => ui.page.$eval(`${FLOAT} .agent-av-fx-sweat`, (el) => parseFloat(getComputedStyle(el).opacity));
    await type(ui, "Re");
    await ui.page.waitForSelector(ANXIOUS);
    const early = await drawn(ui, "--av-nerves");
    assert((await sweat()) === 0, "it sweats at the first key");
    await ui.page.keyboard.type("factor the parser of the arguments, but run the tests before you do", { delay: 60 });
    const late = await drawn(ui, "--av-nerves");
    assert(late > early + 0.3 && late <= 1, `from ${early} to ${late}`);
    await ui.page.waitForFunction(
      (float) => parseFloat(getComputedStyle(document.querySelector(`${float} .agent-av-fx-sweat`)).opacity) > 0.9,
      FLOAT
    );
    await ui.shot("avatar-anxious-sweat");
  },

  async "when the keys stop it looks up, and after a while gives up"(ui, assert) {
    await openChat(ui);
    await type(ui, "Refactor");
    await ui.page.waitForTimeout(1300);
    assert((await drawn(ui, "--av-look-y")) < -0.2, `it does not look up: ${await drawn(ui, "--av-look-y")}`);
    await ui.page.waitForSelector(IDLE, { timeout: 8000 });
    await ui.page.waitForSelector(`${FLOAT}:not([data-rapt])`);
    // Idle again, it would look at the pointer, which the typing left on the composer.
    await ui.page.evaluate(() => document.documentElement.dispatchEvent(new MouseEvent("mouseleave")));
    await ui.page.waitForTimeout(900);
    assert(Math.abs(await drawn(ui, "--av-look-y")) < 0.05, "its eyes stayed on the text");
  },

  async "erased to nothing or sent, there is nothing left to be anxious about"(ui, assert) {
    await openChat(ui);
    await type(ui, "abc");
    await ui.page.waitForSelector(ANXIOUS);
    for (let i = 0; i < 3; i++) await ui.page.keyboard.press("Backspace");
    await ui.page.waitForSelector(IDLE, { timeout: 1500 });

    await ui.page.keyboard.type("hello", { delay: 60 });
    await ui.page.waitForSelector(ANXIOUS);
    await ui.page.keyboard.press("Enter");
    await ui.page.waitForSelector(`${FLOAT} .agent-av:not([data-pose="anxious"])`, { timeout: 1500 });
    assert((await ui.toHost("submit")).length === 1, "it was not sent");
    await ui.page.waitForSelector(`${FLOAT}:not([data-rapt])`);
  },

  async "at work it has other things to do than be anxious"(ui, assert) {
    await openChat(ui);
    await ui.fromHost(envelope("status.changed", { state: "CALLING_MODEL" }));
    await ui.page.waitForSelector(`${FLOAT} .agent-av[data-pose="think"]`);
    await type(ui, "and then run the tests");
    await ui.page.waitForTimeout(300);
    assert(await ui.page.$(`${FLOAT} .agent-av[data-pose="think"]`), "typing took it from its work");
    assert(!(await ui.page.$(`${FLOAT}[data-rapt]`)), "it watches the caret while it works");
  },

  async "told not to be anxious, it is not"(ui, assert) {
    await openSettings(ui);
    await ui.page.click('button[role="switch"][aria-label="Avatar gets anxious while you type"]');
    assert((await ui.page.evaluate(() => window.__state.prefs.avatarAnxious)) === false, "the choice was not saved");
    await ui.page.click('button[title="Back"]');
    await openChat(ui);
    await type(ui, "Refactor the parser");
    await ui.page.waitForSelector(AT_EASE);
    await ui.page.waitForTimeout(300);
    assert(await ui.page.$(IDLE), "it is anxious although it was told not to be");
    assert(!(await ui.page.$(`${FLOAT}[data-rapt]`)), "it watches the caret although it was told not to");
  },

  async "asked for less motion, it takes the pose and holds it still"(ui, assert) {
    await ui.page.emulateMedia({ reducedMotion: "reduce" });
    await openChat(ui);
    await type(ui, "Refactor the parser");
    await ui.page.waitForSelector(ANXIOUS);
    await ui.page.waitForTimeout(300);
    assert(!(await ui.page.$(`${FLOAT}[data-rapt]`)), "it follows the caret");
    assert((await drawn(ui, "--av-nerves")) === 0, "the keys wound it up");
    const moving = await ui.page.$eval(`${FLOAT} .agent-av`, (el) => el.getAnimations({ subtree: true }).length);
    assert(moving === 0, `${moving} animations still run`);
  },

  async "the panel opens with it greeting from its seat, and it never leaves it"(ui, assert) {
    await ui.page.waitForSelector(acting("greets"));
    const seat = await seatOf(ui);
    let glad = 0;
    while (await ui.page.$(acting("greets"))) {
      const face = faceOf(await ui.page.locator(FLOAT).boundingBox());
      // A little jump, straight up: nothing else takes it off its seat.
      assert(Math.abs(face.x - seat.x) < 1.5 && face.y < seat.y + 1.5 && face.y > seat.y - 9, `greeting at ${face.x},${face.y}`);
      glad = Math.max(glad, await drawn(ui, "--av-glad"));
      await ui.page.waitForTimeout(40);
    }
    assert(glad > 0.9, `its eyes never arched: ${glad}`);
    assert(near(faceOf(await settle(ui)), seat), "the greeting left it off its seat");
    await ui.page.waitForSelector(IDLE);
  },

  async "a new conversation takes it from its seat to its place, in one piece, and it greets on arriving"(ui, assert) {
    // The panel has just opened: its first hello, on its seat, comes before this one.
    await ui.page.waitForSelector(acting("greets"));
    await ui.page.waitForSelector(AT_EASE);
    const seated = await settle(ui);
    await ui.page.click(".home-new");
    const way = [];
    while (!(await ui.page.$(acting("greets"))) && way.length < 80) {
      way.push({
        ...(await ui.page.locator(FLOAT).boundingBox()),
        drawn: await ui.page.$eval(FLOAT, (el) => `${el.style.transform} ${getComputedStyle(el).opacity}`),
      });
      await ui.page.waitForTimeout(20);
    }
    await ui.page.waitForSelector(acting("greets"));
    const home = await settle(ui);
    assert(home.x > seated.x + 100 && home.y > seated.y + 100, "it has no place of its own to go to");
    // It is carried all the way: seen on the way, always going there, never shrunk or hidden.
    const seen = way.map((at) => Math.round(at.y)).join(" ");
    assert(way.some((at) => at.y > seated.y + 40 && at.y < home.y - 40), `it was never seen between the two: ${seen}`);
    assert(way.every((at, i) => i === 0 || at.y >= way[i - 1].y - 12), "it did not go there in one go");
    const whole = (at) => Math.abs(at.width - seated.width) < 0.5 && !at.drawn.includes("scale") && at.drawn.endsWith(" 1");
    assert(way.every(whole), `it shrank or faded on the way: ${JSON.stringify(way.find((at) => !whole(at)))}`);
    assert((await mostWhile(ui, "greets", "--av-glad")) > 0.9, "it arrived without a glad face");
    assert(near(await settle(ui), home), "the greeting moved it");
    assert(!(await ui.page.evaluate(() => window.__state?.prefs?.avatarAt)), "arriving was remembered as being put there");
  },

  async "it waits on an approval by looking at it, and is glad to be answered"(ui, assert) {
    await openChat(ui);
    await askApproval(ui);
    await ui.page.waitForSelector(acting("waits"));
    await ui.page.waitForTimeout(700);
    // The card is in the transcript, up and to its left.
    assert((await drawn(ui, "--av-look-x")) < -0.5, `it looks to ${await drawn(ui, "--av-look-x")}`);
    await ui.shot("avatar-waits");
    await ui.page.click('.approval-actions button:has-text("Allow once")');
    await ui.page.waitForSelector(acting("relieved"));
    assert((await mostWhile(ui, "relieved", "--av-glad")) > 0.9, "it is not glad of the answer");
    assert((await ui.toHost("resolveApproval"))[0]?.scope === "once", "the answer was not sent");
  },

  async "refused, it is sorry; and with nothing left to wait for it stops waiting"(ui, assert) {
    await openChat(ui);
    await askApproval(ui);
    await ui.page.waitForSelector(acting("waits"));
    await ui.page.click('.approval-actions button:has-text("Deny")');
    await ui.page.click('.approval-actions button:has-text("Send and deny")');
    await ui.page.waitForSelector(acting("laments"));
    await ui.fromHost(envelope("tool.approval.resolved", { call_id: "ap" }));
    await ui.page.waitForSelector(AT_EASE, { timeout: 6000 });
    await ui.page.waitForTimeout(300);
    assert(!(await ui.page.$(acting("waits"))), "it waits on a card that is gone");
  },

  async "a turn that did its work is cheered, one that broke is mourned, a plain answer is neither"(ui, assert) {
    await openChat(ui);
    await ui.page.waitForSelector(AT_EASE);
    await turn(ui, "READY");
    await ui.page.waitForTimeout(400);
    assert(await ui.page.$(AT_EASE), "it cheers an answer that did no work");

    await turn(ui, "READY", { tool: true });
    await ui.page.waitForSelector(acting("cheers"));
    const sparks = () => ui.page.$$eval(`${FLOAT} .av-float-fx > g:first-child path`, (all) => all.filter((el) => el.style.opacity > 0.3).length);
    await ui.page.waitForFunction((float) => document.querySelector(float).style.getPropertyValue("--av-glad") > 0.9, FLOAT);
    assert((await sparks()) > 0, "no sparks");
    await ui.shot("avatar-cheers");
    await ui.page.waitForSelector(AT_EASE, { timeout: 5000 });

    await turn(ui, "ERROR");
    await ui.page.waitForSelector(acting("laments"));
    await ui.page.waitForTimeout(1500);
    assert((await drawn(ui, "--av-leaf-lag")) > 20, `its leaf stands at ${await drawn(ui, "--av-leaf-lag")}`);
    await ui.shot("avatar-laments");

    await ui.page.waitForSelector(AT_EASE, { timeout: 5000 });
    await turn(ui, "CANCELLED", { tool: true });
    await ui.page.waitForTimeout(400);
    assert(await ui.page.$(AT_EASE), "it has feelings about a turn the user stopped");
  },

  async "poked, it gives; stroked, it purrs"(ui, assert) {
    await openChat(ui);
    await ui.page.waitForSelector(AT_EASE);
    const grip = await ui.page.locator(GRIP).boundingBox();
    const face = { x: grip.x + grip.width / 2, y: grip.y + grip.height / 2 };
    const place = await ui.page.locator(FLOAT).boundingBox();
    await ui.page.mouse.click(face.x, face.y);
    await ui.page.waitForSelector(acting("poked"));
    await ui.page.waitForSelector(AT_EASE, { timeout: 3000 });
    assert(near(await settle(ui), place), "a poke moved it");

    for (let i = 0; i < 10; i++) {
      await ui.page.mouse.move(face.x + (i % 2 ? 14 : -14), face.y);
      await ui.page.waitForTimeout(40);
    }
    await ui.page.waitForSelector(acting("petted"));
    assert((await mostWhile(ui, "petted", "--av-glad")) > 0.9, "it does not like it");
    assert(!(await ui.page.evaluate(() => window.__state?.prefs?.avatarAt)), "being touched was remembered as being put somewhere");
  },

  async "thrown hard at a wall, it sees stars where it lands"(ui, assert) {
    await openChat(ui);
    await ui.page.waitForSelector(AT_EASE);
    await drag(ui, -400, -200);
    await settle(ui);
    await fling(ui, -90, 3);
    await ui.page.waitForSelector(acting("dizzy"), { timeout: 3000 });
    await ui.page.waitForFunction(
      (float) => [...document.querySelectorAll(`${float} .av-float-fx > g:nth-child(2) path`)].some((el) => el.style.opacity > 0.5),
      FLOAT
    );
    await ui.shot("avatar-dizzy");
    await ui.page.waitForSelector(AT_EASE, { timeout: 5000 });
    // Off the wall and back into the room: wherever that is, it is where it now lives.
    const rest = await settle(ui);
    const panel = ui.page.viewportSize();
    assert(rest.x >= 0 && rest.x + rest.width <= panel.width, `it came to rest at ${rest.x}`);
    await ui.page.waitForFunction(() => window.__state?.prefs?.avatarAt);
  },

  async "typed at for too long it hides behind the composer, and goes back to its place when it is sent"(ui, assert) {
    const home = await openChat(ui);
    await ui.page.click(FIELD);
    const typing = ui.page.keyboard.type(
      "Refactor the parser of the arguments, run every test there is, and only then open the pull request for me",
      { delay: 70 }
    );
    await ui.page.waitForSelector(acting("hides"), { timeout: 9000 });
    await ui.page.waitForTimeout(1600);
    const hidden = await ui.page.locator(FLOAT).boundingBox();
    const glass = await ui.page.locator(".composer-wrap").boundingBox();
    const field = await ui.page.locator(".composer").boundingBox();
    assert(hidden.y + hidden.height > glass.y + 50, `only ${hidden.y + hidden.height - glass.y}px of it is under the glass`);
    assert(hidden.y < glass.y, "not even its leaf shows");
    assert(Math.abs(faceOf(hidden).x - (field.x + field.width / 2)) < 4, "it is not in the middle of the composer");
    await ui.shot("avatar-hides");
    await typing;
    assert(await ui.page.$(acting("hides")), "it came out while the keys were still coming");
    assert(!(await ui.page.evaluate(() => window.__state?.prefs?.avatarAt)), "where it hides was remembered as its place");

    await ui.page.keyboard.press("Enter");
    await ui.page.waitForSelector(acting("comes-out"));
    assert((await mostWhile(ui, "comes-out", "--av-glad")) > 0.9, "it is not glad it is over");
    assert(near(await settle(ui), home), "it did not go back to where it was");
    assert(!(await ui.page.evaluate(() => window.__state?.prefs?.avatarAt)), "coming back was remembered as being put there");
    assert((await ui.toHost("submit")).length === 1, "it was not sent");
  },

  async "picked up from behind the composer it is out, and stays where it is put"(ui, assert) {
    await openChat(ui);
    await ui.page.click(FIELD);
    const typing = ui.page.keyboard.type(
      "Refactor the parser of the arguments, run every test there is, and only then open the pull request for me, and wait",
      { delay: 70 }
    );
    await ui.page.waitForSelector(acting("hides"), { timeout: 9000 });
    await typing;
    // Up to look, and staying there: more of it over the edge than on its way up, less than on its way in or out.
    await ui.page.waitForFunction(
      (float) => {
        const shows = document.querySelector(".composer-wrap").getBoundingClientRect().top - document.querySelector(float).getBoundingClientRect().top;
        return document.querySelector(float).dataset.act === "hides" && shows > 62 && shows < 76;
      },
      FLOAT,
      { timeout: 10000 }
    );
    const box = await ui.page.locator(FLOAT).boundingBox();
    // Its face is over the edge, and can be taken: by the middle, where a duck does not take it out of the hand.
    const from = { x: box.x + box.width * 0.46, y: box.y + 42 };
    await ui.page.mouse.move(from.x, from.y);
    await ui.page.mouse.down();
    await ui.page.waitForSelector(`${FLOAT}[data-held]`);
    for (let i = 1; i <= 10; i++) {
      await ui.page.mouse.move(from.x + 12 * i, from.y - 26 * i);
      await ui.page.waitForTimeout(16);
    }
    await ui.page.waitForTimeout(250);
    await ui.page.mouse.up();
    const put = await settle(ui);
    // It trembles as it is taken: where it is put is where the hand went, to a pixel or two.
    assert(Math.abs(put.x - box.x - 120) < 4 && Math.abs(put.y - box.y + 260) < 4, `put down at ${put.x},${put.y}`);
    assert(!(await ui.page.$(acting("hides"))), "it is still hiding in mid-air");
    await ui.page.waitForFunction(() => window.__state?.prefs?.avatarAt);
    // The same typing goes on: taken out once, it does not run back.
    await ui.page.keyboard.type(" for the build to finish before you do anything else", { delay: 70 });
    assert(!(await ui.page.$(acting("hides"))), "put down, it ran straight back");
    // It hops at the keys, where it stands.
    const still = await ui.page.locator(FLOAT).boundingBox();
    assert(Math.abs(still.x - put.x) < 3 && Math.abs(still.y - put.y) < 12, `it left where it was put: ${still.x},${still.y}`);
  },

  async "told not to hide, it only gets anxious"(ui, assert) {
    await openSettings(ui);
    await ui.page.click(SWITCH("Avatar hides from your typing"));
    assert((await ui.page.evaluate(() => window.__state.prefs.avatarHides)) === false, "the choice was not saved");
    await ui.page.click('button[title="Back"]');
    await openChat(ui);
    await ui.page.click(FIELD);
    await ui.page.keyboard.type("Refactor the parser of the arguments, run every test there is, and then open it", { delay: 70 });
    assert(await ui.page.$(ANXIOUS), "it is not even anxious");
    assert(!(await ui.page.$(`${FLOAT}[data-act]`)), "it hides although it was told not to");
  },

  async "left alone for three minutes it falls asleep, and is up at the first move"(ui, assert) {
    // A clock that can be put forward: nobody waits three minutes for a test.
    await ui.page.clock.install();
    await ui.page.reload();
    await ui.page.waitForSelector(acting("greets"));
    await ui.page.waitForSelector(AT_EASE);
    await ui.page.clock.fastForward(3 * 60_000 - 8000);
    await ui.page.waitForTimeout(300);
    assert(await ui.page.$(AT_EASE), "it nods off before its time");
    await ui.page.clock.fastForward(8000);
    await ui.page.waitForSelector(acting("nods-off"));
    await ui.page.waitForSelector(`${FLOAT}[data-asleep]`, { timeout: 12000 });
    const zs = await ui.page.$eval(`${FLOAT} .av-float-zs path`, (el) => getComputedStyle(el).animationName);
    assert(zs === "av-z", `its zs play ${zs}`);
    assert((await drawn(ui, "--av-lid")) < 0.1, "it sleeps with its eyes open");
    // Settled, it sleeps on the stylesheet alone: nothing asks for a frame.
    await ui.page.waitForTimeout(4000);
    await ui.page.evaluate(() => {
      const real = window.requestAnimationFrame;
      window.__asked = 0;
      window.requestAnimationFrame = (callback) => ((window.__asked += 1), real(callback));
    });
    await ui.page.waitForTimeout(500);
    const asked = await ui.page.evaluate(() => window.__asked);
    assert(asked < 5, `asleep, it asked for ${asked} frames in half a second`);
    await ui.shot("avatar-asleep");

    await ui.page.mouse.move(300, 300);
    await ui.page.waitForSelector(acting("wakes"));
    assert(!(await ui.page.$(`${FLOAT}[data-asleep]`)), "it is awake and still marked asleep");
    await ui.page.waitForSelector(IDLE, { timeout: 5000 });
  },

  async "leaving in the middle of a hello, it says the next one where it arrives"(ui, assert) {
    await ui.page.waitForSelector(acting("greets"));
    await ui.page.click(".home-new");
    await ui.page.waitForSelector(".composer");
    await ui.page.waitForTimeout(120);
    assert(!(await ui.page.$(acting("greets"))), "it goes on greeting the seat it has left");
    await ui.page.waitForSelector(acting("greets"));
    const composer = await ui.page.locator(".composer-wrap").boundingBox();
    const box = await ui.page.locator(FLOAT).boundingBox();
    assert(box.x > 600 && composer.y - box.y - box.height < 30, `it greets from ${box.x},${box.y}`);
  },

  async "each of its manners has a switch, and the greeting obeys its own"(ui, assert) {
    await openSettings(ui);
    for (const [label, pref] of [
      ["Avatar greets you", "avatarGreets"],
      ["Avatar waits for your answer", "avatarWaits"],
      ["Avatar celebrates and laments", "avatarCheers"],
      ["Avatar falls asleep", "avatarSleeps"],
      ["Avatar reacts to touch", "avatarTouch"],
    ]) {
      await ui.page.click(SWITCH(label));
      assert((await ui.page.evaluate((key) => window.__state.prefs[key], pref)) === false, `${pref} was not saved`);
    }
    await ui.page.click('button[title="Back"]');
    await ui.page.waitForSelector(AT_EASE);
    await openChat(ui);
    await ui.page.waitForTimeout(500);
    assert(await ui.page.$(AT_EASE), "it greets although it was told not to");
    await askApproval(ui);
    await ui.page.waitForTimeout(400);
    assert(await ui.page.$(AT_EASE), "it waits although it was told not to");
    await ui.fromHost(envelope("tool.approval.resolved", { call_id: "ap" }));
    await turn(ui, "READY", { tool: true });
    await ui.page.waitForTimeout(400);
    assert(await ui.page.$(AT_EASE), "it cheers although it was told not to");
    const grip = await ui.page.locator(GRIP).boundingBox();
    await ui.page.mouse.click(grip.x + grip.width / 2, grip.y + grip.height / 2);
    await ui.page.waitForTimeout(200);
    assert(await ui.page.$(AT_EASE), "it minds a poke although it was told not to");
  },
};
