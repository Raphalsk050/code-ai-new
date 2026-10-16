// The settings panel, end to end: click Save, answer as the bridge answers,
// read what the form is left holding. Save lives on the bar that appears while
// something is unsaved; the header's status says Saved or Not saved.
//
// The bug these pin down: `updateSettings` answers with the configuration as
// it stands, so a field the backend refused comes back carrying the value the
// user was replacing. The panel rewrote its whole form from that snapshot and
// reported "Saved", so a refusal read as the save button wiping the edit.

import { choose, held, listed } from "./dropdown.mjs";
import { settingsSnapshot } from "./harness.mjs";

const SAVE = ".settings-save";
const STATUS = ".settings-status";

/** Settings live in categories; a field is on screen once its category is. */
const goTo = (ui, category) => ui.page.click(`.settings-nav-item:has-text("${category}")`);

/** Open the panel with a snapshot already delivered. */
async function openPanel(ui, snapshot = settingsSnapshot()) {
  await ui.fromHost({ type: "settings", settings: snapshot });
  // The gear's tooltip follows the language, so a pt-BR snapshot opens by the
  // pt-BR title.
  const gear = /^pt/i.test(snapshot.language ?? "en") ? "Configurações" : "Settings";
  await ui.page.click(`button[title="${gear}"]`);
  await ui.page.waitForSelector(".settings-body");
  await ui.clearToHost();
  return snapshot;
}

const value = (ui, selector) => ui.page.inputValue(selector);
const baseUrlField = '.settings-field:has(> .settings-field-label:text-is("Base URL")) input';
const workspaceField = '.settings-field:has(> .settings-field-label:text-is("Workspace")) input';
const modelField = ".model-input";
const apiKeyField = '.settings-field:has(> .settings-field-label:text-is("API key")) input[type="password"]';

export const tests = {
  async "the form posts every field, with the api key last"(ui, assert) {
    await openPanel(ui);

    await ui.page.fill(modelField, "modelo-novo");
    await ui.page.fill(apiKeyField, "sk-chave");
    await ui.page.click(SAVE);

    const [sent] = await ui.toHost("updateSettings");
    assert(sent, "Save should post updateSettings");
    assert(sent.updates.model === "modelo-novo", "the edited model should be on the wire");
    assert(sent.updates.api_key === "sk-chave", "the typed key should be on the wire");
    assert(
      Object.keys(sent.updates).at(-1) === "api_key",
      "the panel appends the key last - the backend has to cope with that"
    );
    assert("workspace" in sent.updates, "the form posts untouched fields too");
  },

  async "the Graph tab switches off following a run"(ui, assert) {
    await openPanel(ui, settingsSnapshot({ graph_follow_run: true }));
    await goTo(ui, "Graph");
    const toggle = '[role="switch"][aria-label="Follow the run on the canvas"]';
    await ui.page.waitForSelector(toggle);
    assert((await ui.page.getAttribute(toggle, "aria-checked")) === "true", "the switch starts off");
    await ui.page.click(toggle);
    await ui.shot("settings-graph-tab");
    await ui.page.click(SAVE);
    const [sent] = await ui.toHost("updateSettings");
    assert(sent?.updates.graph_follow_run === false, "the switch did not reach the backend");
  },

  async "sub-agent models are picked from the provider's list"(ui, assert) {
    await openPanel(ui);
    await goTo(ui, "Sub-agents");
    await ui.page.click('.settings-field:has(> .settings-field-label:text-is("Sub-agent models")) .model-list-btn');
    assert((await ui.toHost("listModels")).length === 1, "the list was not asked for");
    await ui.fromHost({ type: "modelsListed", models: ["qwen3-coder", "gpt-oss:20b", "llama3.1"] });
    const option = (name) => `.model-options[aria-label="Sub-agent models"] .model-option:has-text("${name}")`;
    await ui.page.click(option("gpt-oss:20b"));
    await ui.page.click(option("qwen3-coder"));
    await ui.page.click(option("llama3.1"));
    await ui.page.click(option("gpt-oss:20b")); // and out again
    assert(
      (await value(ui, '.settings-field:has(> .settings-field-label:text-is("Sub-agent models")) input')) === "qwen3-coder, llama3.1",
      "the field does not hold the picks in order"
    );
    await ui.page.click(SAVE);
    const [sent] = await ui.toHost("updateSettings");
    assert(JSON.stringify(sent.updates.subagent_models) === JSON.stringify(["qwen3-coder", "llama3.1"]), JSON.stringify(sent.updates.subagent_models));
  },

  async "a saved field keeps the value the backend confirmed"(ui, assert) {
    const snapshot = await openPanel(ui);

    await ui.page.fill(modelField, "modelo-novo");
    await ui.page.click(SAVE);
    await ui.fromHost({
      type: "settingsUpdated",
      result: {
        applied: ["model"],
        restart_required: [],
        errors: {},
        settings: { ...snapshot, model: "modelo-novo" },
      },
    });

    assert(
      (await value(ui, modelField)) === "modelo-novo",
      "the saved model should stay in the form"
    );
    assert(
      (await ui.page.textContent(STATUS)).trim() === "Saved",
      "a clean save should say so"
    );
    assert(!(await ui.page.$(SAVE)), "the save bar outlived the save");
    assert(
      (await ui.page.locator(".settings-errors").count()) === 0,
      "a clean save shows no error block"
    );
  },

  async "a refused field keeps what the user typed, not what the backend kept"(ui, assert) {
    const snapshot = await openPanel(ui);

    await ui.page.fill(baseUrlField, "https://llm.empresa.example/v1");
    await ui.page.fill(modelField, "modelo-novo");
    await ui.page.click(SAVE);
    // What the bridge answers when the endpoint is refused and the rest lands:
    // the snapshot still holds the previous URL.
    await ui.fromHost({
      type: "settingsUpdated",
      result: {
        applied: ["model"],
        restart_required: [],
        errors: { base_url: "api_key is required for non-local OpenAI-compatible endpoints." },
        settings: { ...snapshot, model: "modelo-novo" },
      },
    });

    assert(
      (await value(ui, baseUrlField)) === "https://llm.empresa.example/v1",
      `the refused URL must survive so it can be corrected, got ${await value(ui, baseUrlField)}`
    );
    assert(
      (await value(ui, modelField)) === "modelo-novo",
      "the field that did save keeps the saved value"
    );
  },

  async "a refusal is named on screen instead of reading as Saved"(ui, assert) {
    const snapshot = await openPanel(ui);

    await ui.page.fill(baseUrlField, "https://llm.empresa.example/v1");
    await ui.page.click(SAVE);
    await ui.fromHost({
      type: "settingsUpdated",
      result: {
        applied: [],
        restart_required: [],
        errors: { base_url: "api_key is required for non-local OpenAI-compatible endpoints." },
        settings: snapshot,
      },
    });

    const label = (await ui.page.textContent(STATUS)).trim();
    assert(label !== "Saved", `a refused save must not claim "Saved", said ${label}`);
    assert(await ui.page.$(SAVE), "the refused field is unsaved, so the bar stays");
    const shown = await ui.page.textContent('.settings-error[data-field="base_url"]');
    assert(
      shown.includes("api_key is required"),
      `the backend's reason should be on screen, got ${shown}`
    );
  },

  async "a typed api key survives its own refusal"(ui, assert) {
    const snapshot = await openPanel(ui);

    await ui.page.fill(apiKeyField, "sk-errada");
    await ui.page.click(SAVE);
    await ui.fromHost({
      type: "settingsUpdated",
      result: {
        applied: [],
        restart_required: [],
        errors: { api_key: "api_key must not contain whitespace." },
        settings: snapshot,
      },
    });

    assert(
      (await value(ui, apiKeyField)) === "sk-errada",
      "retyping a long key because the panel cleared it is the wrong outcome"
    );
  },

  async "a clean save clears the api key field"(ui, assert) {
    const snapshot = await openPanel(ui);

    await ui.page.fill(apiKeyField, "sk-chave");
    await ui.page.click(SAVE);
    await ui.fromHost({
      type: "settingsUpdated",
      result: {
        applied: ["api_key"],
        restart_required: [],
        errors: {},
        settings: { ...snapshot, api_key_set: true },
      },
    });

    assert(!(await ui.page.$(apiKeyField)), "a stored key should not sit in the form");
    assert(await ui.page.$(".key-stored"), "a stored key says it is stored");
    await ui.page.click('.key-stored button:has-text("Change")');
    assert((await value(ui, apiKeyField)) === "", "changing the key starts from an empty field");
  },

  async "editing after a refusal offers to save again"(ui, assert) {
    const snapshot = await openPanel(ui);

    await goTo(ui, "Context");
    await ui.page.fill(workspaceField, "C:/projetos/outro");
    await ui.page.click(SAVE);
    await ui.fromHost({
      type: "settingsUpdated",
      result: {
        applied: [],
        restart_required: [],
        errors: { workspace: "unknown setting" },
        settings: snapshot,
      },
    });
    assert(
      (await value(ui, workspaceField)) === "C:/projetos/outro",
      "the refused workspace must survive"
    );

    await ui.page.fill(workspaceField, "C:/projetos/terceiro");
    assert(
      (await ui.page.textContent(SAVE)).trim() === "Save",
      "a fresh edit puts the button back to Save"
    );
  },

  async "the panel speaks Portuguese only when Portuguese is the chosen language"(ui, assert) {
    // English is what the source carries; the table is applied only once the
    // backend says the user picked another language.
    await ui.fromHost({ type: "settings", settings: settingsSnapshot({ language: "pt-BR" }) });
    await ui.page.click('button[title="Configurações"]');
    await ui.page.waitForSelector(".settings-body");

    const title = await ui.page.textContent(".settings-title");
    assert(title.trim() === "Configurações", `the title read ${title}`);
    assert(
      (await ui.page.textContent(STATUS)).trim() === "Salvo",
      "the status stayed English with pt-BR selected"
    );
  },

  async "the language field offers English and Portuguese"(ui, assert) {
    await openPanel(ui);
    await goTo(ui, "Agent");

    const options = await listed(ui, ".language-select");
    assert(options.length === 2, `the dropdown had ${options.length} options`);
    assert(
      options[0].value === "en" && options[0].label === "English",
      `first option was ${JSON.stringify(options[0])}`
    );
    assert(
      options[1].value === "pt-BR" && options[1].label.startsWith("Portugu"),
      `second option was ${JSON.stringify(options[1])}`
    );
    assert(
      (await held(ui, ".language-select")) === "en",
      "the dropdown did not show the language the backend reported"
    );
    // The field sits below the fold, so the shot has to go to it.
    await ui.page.locator(".language-select").scrollIntoViewIfNeeded();
    await ui.page.waitForTimeout(80);
    await ui.shot("settings-language-dropdown");
  },

  async "a language the panel has no translation for keeps its own option"(ui, assert) {
    // A config edited by hand must not be silently rewritten to English the
    // first time the panel is opened.
    await openPanel(ui, settingsSnapshot({ language: "es-ES" }));
    await goTo(ui, "Agent");

    const values = (await listed(ui, ".language-select")).map((option) => option.value);
    assert(values.includes("es-ES"), `the dropdown dropped the stored value: ${values}`);
    assert(
      (await held(ui, ".language-select")) === "es-ES",
      "the stored language was not selected"
    );
  },

  async "picking Portuguese and saving turns the panel Portuguese, with no reload"(
    ui,
    assert
  ) {
    const snapshot = await openPanel(ui);
    assert(
      (await ui.page.textContent(STATUS)).trim() === "Saved",
      "the panel did not start in English"
    );

    await goTo(ui, "Agent");
    await choose(ui, ".language-select","pt-BR");
    assert((await ui.page.textContent(SAVE)).trim() === "Save", "an edit does not offer Save");
    await ui.page.click(SAVE);

    // What the panel puts on the wire is the language, not a label.
    const [sent] = await ui.toHost("updateSettings");
    assert(sent, "Save posted no updateSettings");
    assert(sent.updates.language === "pt-BR", `the wire carried ${sent.updates.language}`);

    // The backend answers with the settings as they now stand; the panel has to
    // follow that without the window being reloaded.
    await ui.fromHost({
      type: "settingsUpdated",
      result: {
        applied: ["language"],
        restart_required: [],
        errors: {},
        settings: { ...snapshot, language: "pt-BR" },
      },
    });

    await ui.page.waitForFunction(
      () => document.querySelector(".settings-title")?.textContent?.trim() === "Configurações"
    );
    assert(
      (await ui.page.textContent(STATUS)).trim() === "Salvo",
      `the status read ${await ui.page.textContent(STATUS)}`
    );
    assert(
      (await held(ui, ".language-select")) === "pt-BR",
      "the dropdown lost the language that was just saved"
    );
    await ui.page.locator(".language-select").scrollIntoViewIfNeeded();
    await ui.page.waitForTimeout(80);
    await ui.shot("settings-language-pt");
  },

  async "switching back to English repaints the panel in English"(ui, assert) {
    const snapshot = await openPanel(ui, settingsSnapshot({ language: "pt-BR" }));
    assert(
      (await ui.page.textContent(STATUS)).trim() === "Salvo",
      "the panel did not start in Portuguese"
    );

    await goTo(ui, "Agente");
    await choose(ui, ".language-select","en");
    await ui.page.click(SAVE);
    await ui.fromHost({
      type: "settingsUpdated",
      result: {
        applied: ["language"],
        restart_required: [],
        errors: {},
        settings: { ...snapshot, language: "en" },
      },
    });

    await ui.page.waitForFunction(
      () => document.querySelector(".settings-title")?.textContent?.trim() === "Settings"
    );
    assert((await ui.page.textContent(STATUS)).trim() === "Saved", "the status stayed Portuguese");
  },

  async "a refused language leaves the panel in the language it still has"(ui, assert) {
    const snapshot = await openPanel(ui);

    await goTo(ui, "Agent");
    await choose(ui, ".language-select","pt-BR");
    await ui.page.click(SAVE);
    // The backend refused it, so its snapshot still says English.
    await ui.fromHost({
      type: "settingsUpdated",
      result: {
        applied: [],
        restart_required: [],
        errors: { language: "unsupported language tag" },
        settings: snapshot,
      },
    });

    assert(
      (await ui.page.textContent(".settings-title")).trim() === "Settings",
      "the panel translated itself on a refusal"
    );
    assert(
      (await held(ui, ".language-select")) === "pt-BR",
      "the refused choice must survive so it can be corrected"
    );
  },
};
