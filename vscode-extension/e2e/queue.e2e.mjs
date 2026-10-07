// A message sent while the agent works waits for a turn of its own - unless
// the user asks for it to be answered now.

let seq = 0;
function envelope(event_type, payload = {}) {
  seq += 1;
  return {
    type: "event",
    event: {
      event_id: `q${seq}`, event_type, event_version: 1, session_id: "s",
      sequence: seq, timestamp: "", source: "app", payload,
    },
  };
}

export const tests = {
  async "a message in the queue offers to be answered now, and the host is told"(ui, assert) {
    await ui.page.click(".home-new");
    await ui.page.waitForSelector(".composer");
    await ui.fromHost(envelope("user.message", { text: "Fix the parser" }));
    await ui.fromHost(envelope("status.changed", { state: "CALLING_MODEL" }));
    await ui.fromHost(envelope("user.message.enqueued", { text: "And add a test for it", position: 1 }));
    await ui.fromHost(envelope("user.message.enqueued", { text: "Then update the changelog", position: 2 }));
    await ui.clearToHost();

    const buttons = await ui.page.$$(".notice-action");
    assert(buttons.length === 2, `${buttons.length} buttons for two waiting messages`);
    assert((await buttons[0].innerText()).trim() === "Answer now", "the button does not say what it does");
    await ui.shot("queue-answer-now");

    await buttons[0].click();
    const [sent] = await ui.toHost("answerNow");
    assert(sent?.text === "And add a test for it", `the host was told ${JSON.stringify(sent)}`);

    // The backend hands it over: it steers now, and the other one moves up the line.
    await ui.fromHost(envelope("user.message.promoted", { text: "And add a test for it", position: 1 }));
    const notes = await ui.page.$$eval(".notice", (els) => els.map((el) => el.textContent.trim()));
    assert(notes[0].startsWith("queued: waiting for the current step to finish"), `first note: ${notes[0]}`);
    assert(notes[1].startsWith("queued: answered once the running turn ends") && !notes[1].includes("#"), `second note: ${notes[1]}`);
    assert((await ui.page.$$(".notice-action")).length === 1, "the handed-over message still offers the button");
  },
};
