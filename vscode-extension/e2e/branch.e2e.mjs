// The workspace's git branch, beside the chat's title and in the session panel.

export const tests = {
  async "the branch shows beside the title and follows a checkout"(ui, assert) {
    await ui.page.click(".home-new");
    await ui.page.waitForSelector(".statusbar");
    assert(!(await ui.page.$(".branch-chip")), "no chip before the bridge names a branch");

    await ui.fromHost({
      type: "event",
      event: {
        event_id: "b1", event_type: "workspace.branch", event_version: 1, session_id: "s",
        sequence: 1, timestamp: "", source: "app", payload: { branch: "feature/orchestration-test" },
      },
    });
    const chip = await ui.page.waitForSelector(".branch-chip");
    assert((await chip.innerText()).includes("feature/orchestration-test"), "chip names the branch");
    await ui.shot("branch-chip");

    await ui.fromHost({
      type: "event",
      event: {
        event_id: "b2", event_type: "workspace.branch", event_version: 1, session_id: "s",
        sequence: 2, timestamp: "", source: "app", payload: { branch: "main" },
      },
    });
    assert((await ui.page.innerText(".branch-chip")).trim() === "main", "a checkout updates it");
  },
};
