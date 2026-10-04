// The settings screen's layout: categories, search, what is unsaved and where,
// the controls that replaced the selects, and the extension's own settings.

import { settingsSnapshot } from "./harness.mjs";

async function openPanel(ui, snapshot = settingsSnapshot()) {
  await ui.fromHost({ type: "settings", settings: snapshot });
  await ui.fromHost({ type: "editorSettings", settings: { diffInEditor: true, command: "", args: [] } });
  await ui.page.click('button[title="Settings"]');
  await ui.page.waitForSelector(".settings-body");
  await ui.clearToHost();
  return snapshot;
}

const row = (id) => `.setting-row[data-setting="${id}"]`;
const goTo = (ui, category) => ui.page.click(`.settings-nav-item:has-text("${category}")`);

export const tests = {
  async "an edit is marked on its row, its category and the header, and can be discarded"(ui, assert) {
    await openPanel(ui);
    const nav = await ui.page.$$eval(".settings-nav-item", (els) => els.map((e) => e.textContent));
    assert(nav.join("|") === "Model|Agent|Editor|Context|Tools and MCP|Sub-agents|Advanced", `nav: ${nav}`);
    assert(!(await ui.page.$(".save-bar")), "the save bar shows with nothing to save");
    await ui.page.fill(`${row("base_url")} input`, "http://gpu-box:11434/v1");
    assert(await ui.page.$(`${row("base_url")} .dirty-dot`), "the row is not marked");
    assert(await ui.page.$('.settings-nav-item.active .dirty-dot'), "the category is not marked");
    assert((await ui.page.textContent(".settings-status")).trim() === "1 unsaved", "the header does not count it");
    const bar = await ui.page.textContent(".save-bar");
    assert(bar.includes("1 unsaved change") && bar.includes("1 reopens the panel"), `bar reads ${bar}`);
    await ui.shot("settings-dirty");
    await ui.page.click('.save-bar button:has-text("Discard")');
    assert((await ui.page.inputValue(`${row("base_url")} input`)) === "http://localhost:11434/v1", "discard kept the edit");
    assert(!(await ui.page.$(".save-bar")), "the bar outlived the discard");
  },

  async "search finds a setting in any category"(ui, assert) {
    await openPanel(ui);
    await ui.page.fill(".settings-search input", "secret");
    const found = await ui.page.$$eval(".setting-row", (els) => els.map((e) => e.dataset.setting));
    assert(found.join(",") === "commands_see_secrets", `search found ${found}`);
    assert(await ui.page.$(`${row("commands_see_secrets")}.risk .risk-tag`), "the risky row is not flagged");
    assert((await ui.page.textContent(".settings-group-title")) === "Advanced", "the match does not name its category");
    await ui.shot("settings-search");
  },

  async "small enums are segmented, and Ctrl+S saves them"(ui, assert) {
    await openPanel(ui);
    await goTo(ui, "Agent");
    const options = await ui.page.$$eval(`${row("permission_mode")} .segmented button`, (els) => els.map((e) => e.textContent));
    assert(options.join(",") === "always,ask,never", `permission options ${options}`);
    assert(await ui.page.$(`${row("followup_reasoning_effort")}`) === null, "follow-up effort left the Model category");
    await ui.page.click(`${row("permission_mode")} .segmented button:has-text("never")`);
    await ui.page.keyboard.press("Control+s");
    const [sent] = await ui.toHost("updateSettings");
    assert(sent?.updates.permission_mode === "never", `saved ${JSON.stringify(sent?.updates)}`);
  },

  async "the model list marks the current model, and the connection test times the provider"(ui, assert) {
    await openPanel(ui);
    await ui.page.click(".model-list-btn");
    assert((await ui.toHost("listModels")).length === 1, "listing did not ask the host");
    await ui.fromHost({ type: "modelsListed", models: ["qwen2.5-coder:7b", "llama3.1:8b"] });
    assert((await ui.page.textContent(".model-option.current")).includes("qwen2.5-coder:7b"), "the current model is not marked");
    await ui.page.click('.model-option:has-text("llama3.1:8b")');
    assert((await ui.page.inputValue(".model-input")) === "llama3.1:8b", "picking a model does not set it");

    await ui.clearToHost();
    await ui.page.click(`${row("connection")} button:has-text("Test")`);
    assert((await ui.toHost("listModels")).length === 1, "the test did not ask the provider");
    await ui.fromHost({ type: "modelsListed", models: ["a", "b", "c"] });
    const result = await ui.page.textContent(".probe-result");
    assert(/Connected in \d+ ms · 3 models/.test(result), `probe reads ${result}`);
    await ui.shot("settings-model");
  },

  async "a stored key shows as stored, with a way to change it"(ui, assert) {
    await openPanel(ui, settingsSnapshot({ api_key_set: true }));
    assert(await ui.page.$(`${row("api_key")} .key-stored`), "a stored key is an empty field again");
    await ui.page.click(`${row("api_key")} button:has-text("Change")`);
    await ui.page.fill(`${row("api_key")} input`, "sk-new");
    await ui.page.click(".settings-save");
    const [sent] = await ui.toHost("updateSettings");
    assert(sent.updates.api_key === "sk-new", "the new key did not travel");
  },

  async "the extension's own settings save to the editor, not the backend"(ui, assert) {
    await openPanel(ui);
    await goTo(ui, "Editor");
    await ui.page.click(`${row("diff_in_editor")} .switch`);
    await ui.page.click(".settings-save");
    const [sent] = await ui.toHost("updateEditorSettings");
    assert(sent?.updates.diffInEditor === false, `sent ${JSON.stringify(sent)}`);
    assert((await ui.toHost("updateSettings")).length === 0, "an editor-only change went to the backend");
  },

  async "a saved setting that needs a restart offers one"(ui, assert) {
    const snapshot = await openPanel(ui);
    await goTo(ui, "Context");
    await ui.page.fill(`${row("max_context_tokens")} input`, "65536");
    await ui.page.click(".settings-save");
    await ui.fromHost({
      type: "settingsUpdated",
      result: { applied: ["max_context_tokens"], restart_required: [], errors: {}, settings: { ...snapshot, max_context_tokens: 65536 } },
    });
    await ui.clearToHost();
    await ui.page.click('.settings-restart-note button:has-text("Restart now")');
    assert((await ui.toHost("restartBridge")).length === 1, "Restart now does not restart");
  },

  async "narrow, the categories are tabs and the rows stack"(ui, assert) {
    await ui.page.setViewportSize({ width: 440, height: 800 });
    await openPanel(ui);
    assert(await ui.page.$(".settings.narrow"), "the narrow layout is not on");
    const direction = await ui.page.$eval(".settings-nav", (el) => getComputedStyle(el).flexDirection);
    assert(direction === "row", `the nav runs ${direction}`);
    await ui.page.fill(`${row("base_url")} input`, "http://gpu-box:11434/v1");
    await ui.shot("settings-narrow");
  },
};
