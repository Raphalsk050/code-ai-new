// The avatar loose on the desktop, from both ends: the panel that gives up its
// own and says where its logo is, and the desktop page that sits it there.

import { settingsSnapshot } from "./harness.mjs";

const FLOAT = ".av-float";
const GRIP = ".av-float-grip";
const SPARK = ".home-hero .spark";
const SWITCH = 'button[role="switch"][aria-label="Avatar on the desktop"]';

async function withSettings(ui, overrides) {
  await ui.fromHost({ type: "settings", settings: settingsSnapshot(overrides) });
}

/** Wait for the seat messages to stop changing, and give the last one. */
async function lastSeat(ui) {
  let last;
  for (let i = 0; i < 40; i++) {
    const sent = await ui.toHost("desktopAvatarSeat");
    const now = sent.at(-1);
    if (sent.length && JSON.stringify(now) === JSON.stringify(last)) return now;
    last = now;
    await ui.page.waitForTimeout(50);
  }
  return last;
}

export const tests = {
  async "off, the panel keeps its own avatar on the logo, as before"(ui, assert) {
    await withSettings(ui, { desktop_avatar: false });
    await ui.page.waitForSelector(FLOAT);
    await ui.page.mouse.move(200, 200);
    await ui.page.mouse.move(220, 240);
    await ui.page.waitForTimeout(200);

    assert(await ui.page.$(`${SPARK}.spark-dock`), "the logo did not make way for the panel's avatar");
    assert((await ui.toHost("desktopAvatarSeat")).length === 0, "the desktop was told about a seat");
  },

  async "the settings offer it under Agent, off unless switched on"(ui, assert) {
    await withSettings(ui, { desktop_avatar: false });
    await ui.page.click('button[title="Settings"]');
    await ui.page.waitForSelector(".settings-body");
    await ui.page.click('.settings-nav-item:has-text("Agent")');
    await ui.page.waitForSelector(SWITCH);

    assert((await ui.page.getAttribute(SWITCH, "aria-checked")) === "false", "it is on by default");
    await ui.page.click(SWITCH);
    await ui.page.click(".settings-save");
    const [sent] = await ui.toHost("updateSettings");
    assert(sent?.updates?.desktop_avatar === true, "Save did not switch it on");
    await ui.shot("desktop-avatar-setting");
  },

  async "on, the panel gives up its avatar and says where the logo is on the screen"(ui, assert) {
    await withSettings(ui, { desktop_avatar: true });
    await ui.page.waitForSelector(FLOAT, { state: "detached" });
    assert((await ui.toHost("desktopAvatarSeat")).length === 0, "a seat was given before the panel knew where it is");

    // The pointer crossing the panel is what says where the panel is.
    await ui.page.mouse.move(300, 300);
    await ui.page.mouse.move(320, 330);
    const seat = await lastSeat(ui);
    const spark = await ui.page.locator(SPARK).boundingBox();
    const scale = await ui.page.evaluate(() => window.devicePixelRatio);

    assert(seat?.seat, "no seat was given");
    const near = (a, b) => Math.abs(a - b) <= 2;
    assert(near(seat.seat.w, spark.width * scale) && near(seat.seat.h, spark.height * scale), "the seat is not the logo's size");
    assert(await ui.page.$(`${SPARK}.spark-dock`), "the logo did not make way for the avatar");
    assert(!(await ui.page.$(`${SPARK} .agent-av`)), "the static logo is still drawn under it");
  },

  async "in a conversation the seat is gone, and back home it is given again"(ui, assert) {
    await withSettings(ui, { desktop_avatar: true });
    await ui.page.mouse.move(300, 300);
    await ui.page.mouse.move(320, 330);
    const first = await lastSeat(ui);
    assert(first?.seat, "no seat on the home screen");

    await ui.page.click(".home-new");
    await ui.page.waitForSelector(".composer");
    assert((await lastSeat(ui))?.seat === null, "leaving home did not take the seat away");

    await ui.clearToHost();
    await ui.page.click('button[title="Back to the history"]');
    await ui.page.waitForSelector(SPARK);
    await ui.page.mouse.move(340, 360);
    const again = await lastSeat(ui);
    assert(again?.seat && again.seat.w === first.seat.w, "back home, the seat was not given again");
  },
};

// ---- the desktop page --------------------------------------------------------

/** Where the face's middle is, once it has stopped moving. */
async function face(ui) {
  let last = null;
  let still = 0;
  for (let i = 0; i < 120; i++) {
    const box = await ui.page.locator(GRIP).boundingBox();
    const at = { x: box.x + box.width / 2, y: box.y + box.height / 2 };
    still = last && Math.abs(at.x - last.x) < 0.1 && Math.abs(at.y - last.y) < 0.1 ? still + 1 : 0;
    if (still === 3) return at;
    last = at;
    await ui.page.waitForTimeout(50);
  }
  throw new Error("the avatar never came to rest");
}

const say = (ui, message) => ui.page.evaluate((m) => window.desk.receive(m), message);

/** The page as a host starts it: where its window is, then where it was left. */
async function start(ui, x, y, scale) {
  await ui.page.evaluate(([fx, fy, fs]) => window.desk.frame(fx, fy, fs), [x, y, scale]);
  await say(ui, { at: null, lang: "en" });
  await say(ui, { pose: "idle", waiting: false, seat: null });
  await ui.page.waitForSelector(GRIP);
}

export const desk = {
  async "told where the logo is, it sits on it; told it is gone, it goes back"(ui, assert) {
    await start(ui, 0, 0, 1);
    const home = await face(ui);

    await say(ui, { seat: { x: 300, y: 200, w: 62, h: 62 } });
    const seated = await face(ui);
    assert(Math.abs(seated.x - 331) < 2 && Math.abs(seated.y - 231) < 2, `it sat at ${seated.x},${seated.y}`);

    await say(ui, { seat: null });
    const back = await face(ui);
    assert(Math.abs(back.x - home.x) < 2 && Math.abs(back.y - home.y) < 2, "it did not go back to its place");
    await ui.shot("desktop-avatar-back");
  },

  async "the logo's place is read through where its window is and its scale"(ui, assert) {
    // Physical pixels, a window 100,50 in at twice the scale: the same spot as above.
    await start(ui, 100, 50, 2);
    await say(ui, { seat: { x: 700, y: 450, w: 124, h: 124 } });
    const seated = await face(ui);
    assert(Math.abs(seated.x - 331) < 2 && Math.abs(seated.y - 231) < 2, `it sat at ${seated.x},${seated.y}`);
  },

  async "a logo off its screen is nowhere to sit"(ui, assert) {
    await start(ui, 0, 0, 1);
    const home = await face(ui);
    await say(ui, { seat: { x: -900, y: 200, w: 62, h: 62 } });
    const now = await face(ui);
    assert(Math.abs(now.x - home.x) < 2 && Math.abs(now.y - home.y) < 2, "it went off the screen");
  },
};
