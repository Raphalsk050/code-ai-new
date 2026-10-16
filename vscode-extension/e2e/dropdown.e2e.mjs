// The dropdown the page draws itself, in place of the browser's <select>: an
// IDE's embedded browser does not always let the mouse pick from a native one.

import { held } from "./dropdown.mjs";
import { settingsSnapshot } from "./harness.mjs";

const COSTUME = '.settings-field:has(.settings-field-label:text-is("Avatar costume"))';

async function openAgentSettings(ui) {
  await ui.fromHost({ type: "settings", settings: settingsSnapshot() });
  await ui.page.click('button[title="Settings"]');
  await ui.page.waitForSelector(".settings-body");
  await ui.page.click('.settings-nav-item:has-text("Agent")');
}

export const tests = {
  async "no native select is left in the panel"(ui, assert) {
    await openAgentSettings(ui);
    for (const category of ["Model", "Agent", "Editor", "Context", "Tools and MCP", "Sub-agents", "Graph", "Advanced"]) {
      await ui.page.click(`.settings-nav-item:has-text("${category}")`);
      assert((await ui.page.$$("select")).length === 0, `${category} still has a native select`);
    }
  },

  async "it is picked from on the press of the mouse, and closes"(ui, assert) {
    await openAgentSettings(ui);
    await ui.page.click(`${COSTUME} .dropdown-button`);
    const options = await ui.page.$$eval(`${COSTUME} .dropdown-opt`, (items) => items.map((item) => item.dataset.value));
    assert(options.join() === "auto,default,halloween,christmas,easter", `it lists ${options}`);
    await ui.shot("dropdown-open");

    const option = await ui.page.locator(`${COSTUME} .dropdown-opt[data-value="easter"]`).boundingBox();
    await ui.page.mouse.move(option.x + 12, option.y + option.height / 2);
    await ui.page.mouse.down();
    assert((await held(ui, `${COSTUME} .dropdown`)) === "easter", "the press did not pick it");
    await ui.page.mouse.up();
    assert(!(await ui.page.$(`${COSTUME} .dropdown-pop`)), "it stayed open after the pick");
    assert((await ui.page.innerText(`${COSTUME} .dropdown-button`)).trim() === "Easter", "the button does not show the pick");
  },

  async "the keys work it too, and a press outside leaves it as it was"(ui, assert) {
    await openAgentSettings(ui);
    await ui.page.focus(`${COSTUME} .dropdown-button`);
    await ui.page.keyboard.press("ArrowDown");
    await ui.page.waitForSelector(`${COSTUME} .dropdown-pop`);
    await ui.page.keyboard.press("c");
    await ui.page.keyboard.press("Enter");
    assert((await held(ui, `${COSTUME} .dropdown`)) === "christmas", `a letter and Enter picked ${await held(ui, `${COSTUME} .dropdown`)}`);

    await ui.page.click(`${COSTUME} .dropdown-button`);
    await ui.page.keyboard.press("ArrowUp");
    await ui.page.keyboard.press("Escape");
    assert(!(await ui.page.$(`${COSTUME} .dropdown-pop`)), "Escape did not close it");
    assert(await ui.page.$(".settings-body"), "Escape went past the dropdown and left the settings");
    assert((await held(ui, `${COSTUME} .dropdown`)) === "christmas", "Escape changed the value");

    await ui.page.click(`${COSTUME} .dropdown-button`);
    await ui.page.mouse.click(300, 700);
    assert(!(await ui.page.$(`${COSTUME} .dropdown-pop`)), "a press outside did not close it");
    assert((await held(ui, `${COSTUME} .dropdown`)) === "christmas", "a press outside changed the value");
  },
};
