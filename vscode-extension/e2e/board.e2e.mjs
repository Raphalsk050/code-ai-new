// The task board tab (webview/board/): lanes, cards, the review banner, the
// detail drawer, drag and drop, and the flight a card takes between lanes.

const LANES = [
  { id: "todo", title: "To Do", role: "todo" },
  { id: "in_progress", title: "In Progress", role: "in_progress" },
  { id: "lane-1", title: "Review", role: "" },
  { id: "done", title: "Done", role: "done" },
];

function sub(id, title, status = "pending", extra = {}) {
  return { id, title, description: "", status, depends_on: [], checklist: [], criteria: [], agent: "", report: "", error: "", ...extra };
}

function task(id, title, lane, status, extra = {}) {
  return {
    id, title, lane, status, description: "", subtasks: [], checklist: [], criteria: [],
    depends_on: [], report: "", error: "", activity: "", ...extra,
  };
}

export function sampleBoard(overrides = {}) {
  return {
    version: 1,
    lanes: LANES,
    tasks: [
      task("T1", "Login with email and password", "todo", "ready", {
        description: "A form, the API call and the session cookie.",
        subtasks: [
          sub("T1.1", "Form component", "pending", { checklist: [{ id: "c1", text: "labels on every field", done: false }] }),
          sub("T1.2", "POST /login endpoint", "pending", { criteria: [{ id: "v1", text: "401 on a wrong password", status: "pending", note: "" }] }),
          sub("T1.3", "Tests", "pending", { depends_on: ["T1.1", "T1.2"] }),
        ],
        checklist: [{ id: "c2", text: "README mentions login", done: false }],
        criteria: [{ id: "v2", text: "a user can sign in", status: "pending", note: "" }],
      }),
      task("T2", "Dark mode", "todo", "draft"),
      task("T3", "Search the items", "in_progress", "running", {
        subtasks: [
          sub("T3.1", "Index", "done", { agent: "Turing" }),
          sub("T3.2", "Query API", "running", { agent: "Lovelace", activity: "edit_code src/search.py" }),
        ],
        activity: "Lovelace: edit_code src/search.py",
      }),
      task("T4", "Export to CSV", "done", "done", {
        subtasks: [sub("T4.1", "Writer", "done")],
        criteria: [{ id: "v3", text: "opens in Excel", status: "passed", note: "checked" }],
      }),
    ],
    running: true,
    planning: false,
    run_id: "r1",
    ...overrides,
  };
}

async function boot(ui, board = sampleBoard(), language = "en") {
  await ui.fromHost({ type: "boardSnapshot", board, moved: [], language });
  await ui.page.waitForSelector('[data-card="T1"]');
  await ui.clearToHost();
}

async function lastEdit(ui) {
  const edits = await ui.toHost("boardEdit");
  if (!edits.length) throw new Error("the panel sent no boardEdit");
  return edits[edits.length - 1].operations;
}

const settle = (ui, ms = 120) => ui.page.waitForTimeout(ms);

export const tests = {
  async "lanes, cards and the running state are drawn"(ui, assert) {
    assert((await ui.toHost("boardReady")).length === 1, "the tab did not say it was ready");
    await boot(ui);
    const lanes = await ui.page.$$eval(".lane-title", (items) => items.map((item) => item.textContent));
    assert(JSON.stringify(lanes) === JSON.stringify(["To Do", "In Progress", "Review", "Done"]), `lanes: ${lanes}`);
    const todo = await ui.page.$$eval('[data-lane="todo"] .card[data-card]', (items) => items.map((item) => item.dataset.card));
    assert(todo.join() === "T1,T2", `To Do holds ${todo}`);
    // Sub-tasks are sub-cards nested under their task.
    const nested = await ui.page.$$eval('[data-group="T1"] .subcard', (items) => items.map((item) => item.dataset.card));
    assert(nested.join() === "T1.1,T1.2,T1.3", `T1's sub-cards: ${nested}`);
    const meta = await ui.page.textContent('[data-card="T1"] .card-meta');
    assert(meta.includes("0/3") && meta.includes("0/2"), `T1 meta: ${meta}`);
    // What its agent is doing shows on the sub-card it is working.
    const working = await ui.page.textContent('[data-card="T3.2"]');
    assert(working.includes("Lovelace") && working.includes("src/search.py"), `sub-card: ${working}`);
    assert(await ui.page.isVisible('[data-testid="board-stop"]'), "a running board shows Stop");
    const state = await ui.page.textContent('[data-testid="board-state"]');
    assert(state.includes("1 agent"), `state: ${state}`);
    await ui.shot("board-running");
  },

  async "planned tasks wait for the user's Play"(ui, assert) {
    await boot(ui, sampleBoard({ running: false, tasks: sampleBoard().tasks.filter((item) => item.id !== "T3") }));
    await ui.fromHost({ type: "boardReview", tasks: ["T1"], message: "Check how the login was split." });
    const banner = await ui.page.textContent('[data-testid="board-review"]');
    assert(banner.includes("1 task(s) waiting for your confirmation") && banner.includes("Check how the login"), banner);
    await ui.shot("board-review");
    await ui.page.click('[data-testid="board-play"]');
    const plays = await ui.toHost("boardPlay");
    assert(plays.length === 1 && plays[0].tasks.length === 0, JSON.stringify(plays));
  },

  async "a task is added from its lane"(ui, assert) {
    await boot(ui);
    await ui.page.click('[data-testid="add-task-todo"]');
    await ui.page.fill('[data-lane="todo"] .lane-add-form input', "Password reset");
    await ui.page.keyboard.press("Enter");
    const ops = await lastEdit(ui);
    assert(ops[0].op === "add_task" && ops[0].title === "Password reset" && ops[0].lane === "todo", JSON.stringify(ops));
  },

  async "the drawer edits sub-tasks, checklist and criteria"(ui, assert) {
    await boot(ui, sampleBoard({ running: false }));
    await ui.page.click('[data-card="T1"]');
    await ui.page.waitForSelector('[data-testid="board-detail"]');
    await ui.page.fill('[aria-label="Add a sub-task"]', "Session cookie");
    await ui.page.keyboard.press("Enter");
    let ops = await lastEdit(ui);
    assert(ops[0].op === "add_subtask" && ops[0].task === "T1" && ops[0].title === "Session cookie", JSON.stringify(ops));

    await ui.page.fill('section:has(h4:text("Checklist")) [aria-label="Add a checklist item"]', "no password in logs");
    await ui.page.keyboard.press("Enter");
    ops = await lastEdit(ui);
    assert(ops[0].op === "add_item" && ops[0].list === "checklist" && !ops[0].subtask, JSON.stringify(ops));

    await ui.page.click('[data-sub="T1.2"] .sub-head .title');
    await ui.page.fill('[data-sub="T1.2"] [aria-label="Add a validation criterion"]', "rate limited");
    await ui.page.keyboard.press("Enter");
    ops = await lastEdit(ui);
    assert(ops[0].op === "add_item" && ops[0].list === "criteria" && ops[0].subtask === "T1.2", JSON.stringify(ops));

    // The box follows the board the host sends back, so only the edit is checked here.
    await ui.page.click('[data-sub="T1.2"] .deps input >> nth=0');
    ops = await lastEdit(ui);
    assert(ops[0].op === "update_subtask" && ops[0].depends_on.includes("T1.1"), JSON.stringify(ops));
    await ui.shot("board-detail");
  },

  async "a running task is locked in the drawer"(ui, assert) {
    await boot(ui);
    await ui.page.click('[data-card="T3"]');
    await ui.page.waitForSelector('[data-testid="board-detail"] .locked');
    assert(await ui.page.isDisabled('[data-testid="board-detail"] .detail-title'), "the title is editable");
    assert(await ui.page.isDisabled('[aria-label="Add a sub-task"]'), "sub-tasks can be added");
    assert((await ui.page.getAttribute('[data-card="T3"]', "draggable")) === "false", "a running card drags");
  },

  async "lanes are added, renamed and removed"(ui, assert) {
    await boot(ui);
    await ui.page.click('.board-toolbar button:has-text("Lane")');
    await ui.page.fill('.lane.new input', "QA");
    await ui.page.keyboard.press("Enter");
    let ops = await lastEdit(ui);
    assert(ops[0].op === "add_lane" && ops[0].title === "QA", JSON.stringify(ops));

    await ui.page.dblclick('[data-lane="lane-1"] .lane-title');
    await ui.page.fill('[data-lane="lane-1"] .lane-head input', "Code review");
    await ui.page.keyboard.press("Enter");
    ops = await lastEdit(ui);
    assert(ops[0].op === "rename_lane" && ops[0].title === "Code review", JSON.stringify(ops));

    // The default lanes offer no remove; the user's own asks twice.
    await ui.page.click('[data-lane="todo"] [aria-label="Lane actions"]');
    assert(!(await ui.page.isVisible('[data-lane="todo"] .lane-menu button:has-text("Remove lane")')), "To Do can be removed");
    await ui.page.click('[data-lane="todo"] [aria-label="Lane actions"]');
    await ui.clearToHost();
    await ui.page.click('[data-lane="lane-1"] [aria-label="Lane actions"]');
    await ui.page.click('[data-lane="lane-1"] .lane-menu button:has-text("Remove lane")');
    assert((await ui.toHost("boardEdit")).length === 0, "removed on the first click");
    await ui.page.click('[data-lane="lane-1"] .lane-menu button:has-text("Click again to remove")');
    ops = await lastEdit(ui);
    assert(ops[0].op === "remove_lane" && ops[0].lane === "lane-1", JSON.stringify(ops));
  },

  async "a card is dragged to another lane"(ui, assert) {
    await boot(ui, sampleBoard({ running: false }));
    await ui.page.dragAndDrop('[data-card="T2"]', '[data-lane="lane-1"] .lane-cards');
    const ops = await lastEdit(ui);
    assert(ops[0].op === "move_task" && ops[0].task === "T2" && ops[0].lane === "lane-1" && ops[0].index === 0, JSON.stringify(ops));
  },

  async "a finished card flies to Done and lands"(ui, assert) {
    const board = sampleBoard();
    await boot(ui, board);
    const finished = structuredClone(board);
    const card = finished.tasks.find((item) => item.id === "T3");
    card.lane = "done";
    card.status = "done";
    card.subtasks.forEach((item) => { item.status = "done"; });
    finished.tasks = [card, ...finished.tasks.filter((item) => item.id !== "T3")];
    await ui.fromHost({ type: "boardSnapshot", board: finished, moved: [{ task: "T3", from: "in_progress", to: "done" }] });
    await settle(ui, 250);
    assert(await ui.page.$(".card-ghost"), "no card in flight");
    await ui.shot("board-flight");
    // The card lands first and flashes; its sub-cards follow, one after another.
    await settle(ui, 850);
    const landed = await ui.page.$eval('[data-card="T3"]', (element) => ({
      lane: element.closest("[data-lane]").dataset.lane,
      visible: element.style.visibility !== "hidden",
      landed: element.classList.contains("landed"),
    }));
    assert(landed.lane === "done" && landed.visible && landed.landed, JSON.stringify(landed));
    await ui.shot("board-landed");
    await settle(ui, 1400);
    assert(!(await ui.page.$(".card-ghost")), "a sub-card never landed");
  },

  async "the board speaks Portuguese"(ui, assert) {
    await boot(ui, sampleBoard({ running: false }), "pt-BR");
    const lanes = await ui.page.$$eval(".lane-title", (items) => items.map((item) => item.textContent));
    assert(lanes[0] === "A fazer" && lanes[1] === "Em andamento" && lanes[3] === "Concluído", `lanes: ${lanes}`);
    await ui.page.click('[data-card="T1"]');
    await ui.page.waitForSelector('[data-testid="board-detail"]');
    await ui.shot("board-pt");
  },

  async "sub-cards are pulled across the lanes and say what their agent does"(ui, assert) {
    const board = sampleBoard();
    const t3 = board.tasks.find((item) => item.id === "T3");
    t3.subtasks = [
      sub("T3.1", "Index", "done", { agent: "Turing", lane: "done", started_at: 1000, finished_at: 1090 }),
      sub("T3.2", "Query API", "running", { agent: "Lovelace", lane: "in_progress", started_at: Date.now() / 1000 - 75 }),
      sub("T3.3", "Tests", "pending", { lane: "todo", depends_on: ["T3.2"] }),
    ];
    await boot(ui, board);
    // The task is in In Progress with the piece being worked; the others went their way, under a stub.
    const at = async (id) => ui.page.$eval(`[data-card="${id}"]`, (element) => element.closest("[data-lane]").dataset.lane);
    assert((await at("T3")) === "in_progress" && (await at("T3.2")) === "in_progress", "T3 or T3.2 misplaced");
    assert((await at("T3.1")) === "done" && (await at("T3.3")) === "todo", "a pulled sub-card is misplaced");
    assert(await ui.page.$('[data-lane="done"] [data-stub="T3"]'), "no stub for T3 in Done");
    assert(await ui.page.$('[data-lane="todo"] [data-stub="T3"]'), "no stub for T3 in To Do");

    const step = (extra) => ({
      task: "T3", subtask: "T3.2", agent: "Lovelace", kind: "tool", state: "", tool: "", target: "", call: "", text: "",
      at: Date.now() / 1000, ...extra,
    });
    await ui.fromHost({ type: "boardActivity", step: step({ kind: "say", text: "Lovelace: Adding the 401 branch." }) });
    await ui.fromHost({ type: "boardActivity", step: step({ state: "started", tool: "edit_code", target: "src/search.py", call: "c1" }) });
    const card = await ui.page.textContent('[data-card="T3.2"]');
    assert(card.includes("Lovelace") && card.includes("Editing") && card.includes("src/search.py"), card);
    assert(/1:1\d/.test(card) && card.includes("1 step"), `clock or steps missing: ${card}`);
    await ui.fromHost({ type: "boardActivity", step: step({ state: "done", tool: "edit_code", call: "c1" }) });

    // The feed tells the story, each step marked once it returned. A running
    // board opened it already.
    await ui.page.waitForSelector('[data-testid="board-activity"]');
    const rows = await ui.page.$$eval(".activity-row", (items) => items.map((item) => item.textContent));
    assert(rows.length === 2 && rows[0].includes("Adding the 401 branch") && rows[1].includes("src/search.py"), JSON.stringify(rows));
    assert(await ui.page.$(".activity-row .state.ok"), "the finished step is not marked");
    await ui.shot("board-subcards");

    // A sub-card opens its task's drawer with that sub-task open.
    await ui.page.click('[data-card="T3.3"]');
    await ui.page.waitForSelector('[data-testid="board-detail"] [data-sub="T3.3"] .sub-body');
  },

  async "the run pulls the task first, then its sub-tasks one after another"(ui, assert) {
    const board = sampleBoard({ running: false });
    board.tasks = board.tasks.filter((item) => item.id !== "T3");
    await boot(ui, board);
    const pulled = structuredClone(board);
    const t1 = pulled.tasks.find((item) => item.id === "T1");
    t1.lane = "in_progress";
    t1.status = "running";
    t1.subtasks[0] = { ...t1.subtasks[0], status: "running", lane: "in_progress", agent: "Turing" };
    t1.subtasks[1] = { ...t1.subtasks[1], status: "running", lane: "in_progress", agent: "Hopper" };
    t1.subtasks[2] = { ...t1.subtasks[2], lane: "todo" };
    pulled.running = true;
    await ui.fromHost({ type: "boardSnapshot", board: pulled, moved: [{ task: "T1", from: "todo", to: "in_progress" }] });
    await settle(ui, 60);
    // They leave in turn: the second sub-card still waits where it was.
    const waiting = await ui.page.$eval('[data-card="T1.2"]', (element) => element.style.visibility);
    assert(waiting === "hidden", `T1.2 flew with the others: ${waiting}`);
    assert((await ui.page.$$(".card-ghost")).length === 3, "not every pulled card has its flight");
    // A run that starts opens the feed.
    assert(await ui.page.$('[data-testid="board-activity"]'), "the activity feed did not open");
    await settle(ui, 2000);
    assert((await ui.page.$$(".card-ghost")).length === 0, "a flight never landed");
    const lane = await ui.page.$eval('[data-card="T1.3"]', (element) => element.closest("[data-lane]").dataset.lane);
    assert(lane === "todo", `the waiting sub-task went to ${lane}`);
  },

  async "files are attached, opened and removed from the drawer"(ui, assert) {
    const board = sampleBoard({ running: false });
    board.tasks[0].attachments = [
      { id: "a1", name: "mockup.png", path: "C:/board-files/T1/a1-mockup.png", size: 20480, mime: "image/png" },
      { id: "a2", name: "spec.md", path: "C:/board-files/T1/a2-spec.md", size: 900, mime: "text/markdown" },
    ];
    await boot(ui, board);
    const meta = await ui.page.textContent('[data-card="T1"] .card-meta');
    assert(meta.includes("2"), `the card does not count its attachments: ${meta}`);
    await ui.page.click('[data-card="T1"]');
    await ui.page.waitForSelector('[data-testid="board-attachments"] .attachment');
    const names = await ui.page.$$eval(".attachment .text", (items) => items.map((item) => item.textContent));
    assert(names.join() === "mockup.png,spec.md", names.join());
    assert((await ui.page.textContent(".attachments")).includes("20 KB"), "no size");

    await ui.page.click(".attachment .text >> text=spec.md");
    const opened = await ui.toHost("boardOpenFile");
    assert(opened.length === 1 && opened[0].path.endsWith("a2-spec.md"), JSON.stringify(opened));
    await ui.page.click('[data-testid="board-attachments"] button:has-text("Attach files")');
    assert((await ui.toHost("boardAttachPick"))[0]?.task === "T1", "Attach files did not ask the host");
    await ui.page.hover(".attachment >> nth=0");
    await ui.page.click(".attachment >> nth=0 >> .remove");
    const ops = await lastEdit(ui);
    assert(ops[0].op === "remove_attachment" && ops[0].attachment === "a1", JSON.stringify(ops));

    // A picture pasted into the drawer is attached as it is.
    await ui.page.evaluate(() => {
      const data = new DataTransfer();
      data.items.add(new File([new Uint8Array([137, 80, 78, 71])], "shot.png", { type: "image/png" }));
      const target = document.querySelector('[data-testid="board-attachments"]');
      target.dispatchEvent(new ClipboardEvent("paste", { clipboardData: data, bubbles: true }));
    });
    await settle(ui, 150);
    const sent = await ui.toHost("boardAttach");
    assert(sent.length === 1 && sent[0].name === "shot.png" && sent[0].data === "iVBORw==", JSON.stringify(sent));
    await ui.shot("board-attachments");
  },
};

