// The avatar loose in the panel: where it sits, how it is moved, what it wears.

import { settingsSnapshot } from "./harness.mjs";

const FLOAT = ".av-float";
const GRIP = ".av-float-grip";
const COSTUME = '.settings-field:has(.settings-field-label:text-is("Avatar costume")) select';

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
    await drag(ui, -200, -300);
    const left = await settle(ui);
    await ui.page.waitForFunction(() => window.__state?.prefs?.avatarAt);
    const place = await ui.page.evaluate(() => window.__state.prefs.avatarAt);
    assert(place.right === true && place.bottom === false, `measured from the top right: ${JSON.stringify(place)}`);

    await ui.page.click('button[title="Back to the history"]');
    await ui.page.waitForSelector(".home-new");
    assert(near(await settle(ui), left), "going home put it back in its corner");
    await ui.page.click(".home-new");
    await ui.page.waitForSelector(".composer");
    assert(near(await settle(ui), left), "a new conversation put it back in its corner");
  },

  async "a panel that changes size keeps it by the same corner"(ui, assert) {
    const before = await openChat(ui);
    await ui.page.setViewportSize({ width: 620, height: 700 });
    await ui.page.waitForTimeout(100);
    const after = await settle(ui);
    assert(near({ x: 900 - before.x, y: 900 - before.y }, { x: 620 - after.x, y: 700 - after.y }), "it did not follow its corner");
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
    await ui.page.selectOption(COSTUME, "halloween");
    await ui.page.waitForSelector(`${FLOAT} .agent-av[data-skin="halloween"]`);
    await ui.shot("avatar-costume");
    await ui.page.selectOption(COSTUME, "default");
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
};
