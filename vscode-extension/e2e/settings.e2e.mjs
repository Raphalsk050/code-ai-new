// The settings panel, end to end: click Salvar, answer as the bridge answers,
// read what the form is left holding.
//
// The bug these pin down: `updateSettings` answers with the configuration as
// it stands, so a field the backend refused comes back carrying the value the
// user was replacing. The panel rewrote its whole form from that snapshot and
// reported "Salvo", so a refusal read as the save button wiping the edit.

import { settingsSnapshot } from "./harness.mjs";

const SAVE = ".settings-save";

/** Open the panel with a snapshot already delivered. */
async function openPanel(ui, snapshot = settingsSnapshot()) {
  await ui.fromHost({ type: "settings", settings: snapshot });
  await ui.page.click('button[title="Configurações"]');
  await ui.page.waitForSelector(".settings-body");
  await ui.clearToHost();
  return snapshot;
}

const value = (ui, selector) => ui.page.inputValue(selector);
const baseUrlField = '.settings-field:has(> .settings-field-label:text-is("URL base")) input';
const workspaceField = '.settings-field:has(> .settings-field-label:text-is("Workspace")) input';
const modelField = ".model-input";
const apiKeyField = '.settings-field input[type="password"]';

export const tests = {
  async "the form posts every field, with the api key last"(ui, assert) {
    await openPanel(ui);

    await ui.page.fill(modelField, "modelo-novo");
    await ui.page.fill(apiKeyField, "sk-chave");
    await ui.page.click(SAVE);

    const [sent] = await ui.toHost("updateSettings");
    assert(sent, "Salvar should post updateSettings");
    assert(sent.updates.model === "modelo-novo", "the edited model should be on the wire");
    assert(sent.updates.api_key === "sk-chave", "the typed key should be on the wire");
    assert(
      Object.keys(sent.updates).at(-1) === "api_key",
      "the panel appends the key last - the backend has to cope with that"
    );
    assert("workspace" in sent.updates, "the form posts untouched fields too");
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
      (await ui.page.textContent(SAVE)).trim() === "Salvo",
      "a clean save should say so"
    );
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

  async "a refusal is named on screen instead of reading as Salvo"(ui, assert) {
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

    const label = (await ui.page.textContent(SAVE)).trim();
    assert(label !== "Salvo", `a refused save must not claim "Salvo", said ${label}`);
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

    assert((await value(ui, apiKeyField)) === "", "a stored key should not sit in the form");
  },

  async "editing after a refusal offers to save again"(ui, assert) {
    const snapshot = await openPanel(ui);

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
      (await ui.page.textContent(SAVE)).trim() === "Salvar",
      "a fresh edit puts the button back to Salvar"
    );
  },
};
