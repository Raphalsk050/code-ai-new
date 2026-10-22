import { strict as assert } from "node:assert";
import { test } from "node:test";

import type { BoardSnapshot, BoardSubTask, BoardTask } from "../../../src/board-schema";
import {
  agentsAt,
  awaitingReview,
  canRun,
  checksOf,
  dependencyChoices,
  dropIndex,
  emptyBoard,
  movesBetween,
  playable,
  progressOf,
} from "../model";

function sub(id: string, status: BoardSubTask["status"] = "pending", depends_on: string[] = []): BoardSubTask {
  return { id, title: id, description: "", status, depends_on, checklist: [], criteria: [], agent: "", report: "", error: "" };
}

function task(id: string, lane: string, status: BoardTask["status"], subtasks: BoardSubTask[] = []): BoardTask {
  return {
    id, title: id, lane, status, description: "", subtasks, checklist: [], criteria: [],
    depends_on: [], report: "", error: "", activity: "",
  };
}

function board(tasks: BoardTask[], running = false): BoardSnapshot {
  return { ...emptyBoard(), tasks, running };
}

test("Play takes the ready, failed and stopped tasks and plans the drafts", () => {
  const snapshot = board([
    task("T1", "todo", "ready"),
    task("T2", "todo", "draft"),
    task("T3", "in_progress", "failed"),
    task("T4", "done", "done"),
    task("T5", "lane-1", "ready"),
  ]);
  const { run, drafts } = playable(snapshot);
  assert.deepEqual(run.map((item) => item.id), ["T1", "T3"]);
  assert.deepEqual(drafts.map((item) => item.id), ["T2"]);
  assert.deepEqual(awaitingReview(snapshot).map((item) => item.id), ["T1"]);
});

test("progress, checks and the agents at work", () => {
  const working = task("T1", "in_progress", "running", [sub("T1.1", "done"), sub("T1.2", "running"), sub("T1.3", "blocked")]);
  working.checklist = [{ id: "c1", text: "a", done: true }];
  working.subtasks[0].criteria = [{ id: "v1", text: "b", status: "passed", note: "" }];
  assert.deepEqual(progressOf(working), { done: 1, total: 3, running: 1, failed: 1 });
  assert.deepEqual(checksOf(working), { checked: 1, checks: 1, passed: 1, criteria: 1 });
  const snapshot = board([working, task("T2", "in_progress", "verifying"), task("T3", "todo", "planning")], true);
  assert.equal(agentsAt(snapshot), 3);
});

test("moves between two boards, for cards the run moved", () => {
  const before = board([task("T1", "todo", "ready"), task("T2", "in_progress", "running")]);
  const after = board([task("T1", "in_progress", "running"), task("T2", "done", "done"), task("T3", "todo", "draft")]);
  assert.deepEqual(movesBetween(before, after), [
    { task: "T1", from: "todo", to: "in_progress" },
    { task: "T2", from: "in_progress", to: "done" },
  ]);
});

test("a drop lands before the first card below the pointer", () => {
  assert.equal(dropIndex([100, 200, 300], 50), 0);
  assert.equal(dropIndex([100, 200, 300], 250), 2);
  assert.equal(dropIndex([100, 200, 300], 400), 3);
  assert.equal(dropIndex([], 10), 0);
});

test("a sub-task cannot be made to wait on what waits on it", () => {
  const item = task("T1", "todo", "ready", [sub("T1.1"), sub("T1.2", "pending", ["T1.1"]), sub("T1.3", "pending", ["T1.2"])]);
  assert.deepEqual(dependencyChoices(item, item.subtasks[0]).map((other) => other.id), []);
  assert.deepEqual(dependencyChoices(item, item.subtasks[2]).map((other) => other.id), ["T1.1", "T1.2"]);
});

test("a task runs only when nothing holds it", () => {
  const ready = task("T1", "todo", "ready");
  assert.equal(canRun(ready, board([ready])), true);
  assert.equal(canRun(ready, board([ready], true)), false);
  const done = task("T2", "done", "done");
  assert.equal(canRun(done, board([done])), false);
  const busy = task("T3", "in_progress", "running");
  assert.equal(canRun(busy, board([busy])), false);
});
