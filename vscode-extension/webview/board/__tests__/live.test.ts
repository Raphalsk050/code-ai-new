import { strict as assert } from "node:assert";
import { test } from "node:test";

import type { BoardStep } from "../../../src/board-schema";
import { agentHue, applyStep, elapsed, emptyLive, settle, toolVerb } from "../live";
import { flightsBetween, laneGroups, emptyBoard } from "../model";
import type { BoardSnapshot, BoardSubTask, BoardTask } from "../../../src/board-schema";

function step(extra: Partial<BoardStep>): BoardStep {
  return { task: "T1", subtask: "T1.2", agent: "Lovelace", kind: "tool", state: "", tool: "", target: "", call: "", text: "", at: 100, ...extra };
}

test("steps become what each agent is doing, and a feed of it", () => {
  let live = emptyLive();
  live = applyStep(live, step({ kind: "say", text: "Lovelace: Reading the routes" }));
  live = applyStep(live, step({ kind: "say", text: "Lovelace: Reading the routes first." }));
  live = applyStep(live, step({ state: "started", tool: "read_file", target: "src/routes.py", call: "c1" }));
  const agent = live.agents["T1.2"];
  assert.equal(agent.tool, "read_file");
  assert.equal(agent.steps, 1);
  // Starting a tool clears what was said before it.
  assert.equal(agent.said, "");
  live = applyStep(live, step({ state: "done", tool: "read_file", call: "c1" }));
  // One line for the burst of words, one for the tool, marked when it returned.
  assert.deepEqual(live.feed.map((item) => [item.kind, item.state]), [["say", ""], ["tool", "done"]]);
  assert.equal(live.feed[0].text, "Lovelace: Reading the routes first.");
  live = applyStep(live, step({ kind: "phase", text: "Turing: checking", subtask: "", task: "T2", agent: "Turing" }));
  assert.equal(live.agents.T2.phase, "checking");
  assert.deepEqual(settle(live).agents, {});
  assert.equal(settle(live).feed.length, 3);
});

test("tools read as verbs, clocks as mm:ss, agents keep their colour", () => {
  assert.equal(toolVerb("edit_code"), "Editing");
  assert.equal(toolVerb("search_index"), "Searching");
  assert.equal(toolVerb("something_new"), "Using");
  assert.equal(elapsed(0, 75), "1:15");
  assert.equal(elapsed(0, 3725), "1:02:05");
  assert.equal(agentHue("Turing"), agentHue("Turing"));
});

function sub(id: string, lane?: string): BoardSubTask {
  return { id, title: id, description: "", status: "pending", depends_on: [], checklist: [], criteria: [], agent: "", report: "", error: "", lane };
}
function task(id: string, lane: string, subtasks: BoardSubTask[]): BoardTask {
  return { id, title: id, lane, status: "running", description: "", subtasks, checklist: [], criteria: [], depends_on: [], report: "", error: "", activity: "" };
}
function board(tasks: BoardTask[]): BoardSnapshot {
  return { ...emptyBoard(), tasks };
}

test("a lane shows a task's card or a stub, with the sub-cards it holds", () => {
  const snapshot = board([task("T1", "in_progress", [sub("T1.1", "done"), sub("T1.2", "in_progress"), sub("T1.3", "todo")])]);
  const doing = laneGroups(snapshot, "in_progress");
  assert.equal(doing.length, 1);
  assert.equal(doing[0].full, true);
  assert.deepEqual(doing[0].subs.map((item) => item.id), ["T1.2"]);
  const todo = laneGroups(snapshot, "todo");
  assert.equal(todo[0].full, false);
  assert.deepEqual(todo[0].subs.map((item) => item.id), ["T1.3"]);
});

test("the task flies before its sub-cards, and they in their order", () => {
  const before = board([task("T1", "todo", [sub("T1.1"), sub("T1.2"), sub("T1.3")])]);
  const after = board([task("T1", "in_progress", [sub("T1.1", "in_progress"), sub("T1.2", "in_progress"), sub("T1.3", "todo")])]);
  assert.deepEqual(flightsBetween(before, after).map((move) => move.task), ["T1", "T1.1", "T1.2"]);
});
