// The board from the chat: the header's button opens its tab, and /board in the
// composer runs the same subcommands the terminal has.

import { settingsSnapshot } from "./harness.mjs";

const BOARD_COMMANDS = [
  { command: "/board", description: "Open the task board", completion: "/board", available: true, kind: "command" },
  { command: "/board add <title>", description: "Add a task", completion: "/board add ", available: true, kind: "command" },
  { command: "/board play", description: "Implement", completion: "/board play", available: true, kind: "command" },
];

async function openChat(ui) {
  await ui.page.click(".home-new");
  await ui.page.waitForSelector(".composer-wrap");
  await ui.fromHost({ type: "settings", settings: settingsSnapshot() });
  await ui.fromHost({ type: "slashCommands", commands: BOARD_COMMANDS });
  await ui.clearToHost();
}

async function typeCommand(ui, text) {
  const box = ui.page.locator(".composer-wrap textarea");
  await box.fill(text);
  await box.press("Escape");
  await box.press("Enter");
  await ui.page.waitForTimeout(100);
}

export const tests = {
  async "the header's board button opens the board's tab"(ui, assert) {
    await openChat(ui);
    await ui.page.click('.statusbar button[title="Task board"]');
    assert((await ui.toHost("openBoard")).length === 1, "no openBoard was sent");
  },

  async "/board opens it and /board add goes to the backend"(ui, assert) {
    await openChat(ui);
    await typeCommand(ui, "/board");
    assert((await ui.toHost("openBoard")).length === 1, "bare /board did not open the tab");
    await typeCommand(ui, "/board add Password reset");
    const sent = await ui.toHost("boardCommand");
    assert(sent.length === 1 && sent[0].text === "add Password reset", JSON.stringify(sent));
    await typeCommand(ui, "/board play");
    const again = await ui.toHost("boardCommand");
    assert(again.length === 2 && again[1].text === "play", JSON.stringify(again));
    assert((await ui.toHost("submit")).length === 0, "a board command went to the agent");
  },
};
