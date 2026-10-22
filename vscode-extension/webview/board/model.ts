// What the board tab works out from a snapshot, apart from React so it can be
// tested: which cards sit in which lane, how far along a card is, what Play
// would take, and which cards moved between two snapshots.

import type {
  BoardLane,
  BoardMove,
  BoardSnapshot,
  BoardSubTask,
  BoardTask,
  TaskStatus,
} from "../../src/board-schema";
import { isBusy } from "../../src/board-schema";

export function emptyBoard(): BoardSnapshot {
  return {
    version: 1,
    lanes: [
      { id: "todo", title: "To Do", role: "todo" },
      { id: "in_progress", title: "In Progress", role: "in_progress" },
      { id: "done", title: "Done", role: "done" },
    ],
    tasks: [],
    running: false,
    planning: false,
    run_id: "",
  };
}

export function tasksIn(board: BoardSnapshot, laneId: string): BoardTask[] {
  return board.tasks.filter((task) => task.lane === laneId);
}

export function laneOfRole(board: BoardSnapshot, role: BoardLane["role"]): BoardLane | undefined {
  return board.lanes.find((lane) => lane.role === role);
}

export interface Progress {
  done: number;
  total: number;
  running: number;
  failed: number;
}

export function progressOf(task: BoardTask): Progress {
  const subs = task.subtasks;
  return {
    done: subs.filter((sub) => sub.status === "done").length,
    total: subs.length,
    running: subs.filter((sub) => sub.status === "running").length,
    failed: subs.filter((sub) => sub.status === "failed" || sub.status === "blocked").length,
  };
}

/** Every checklist item and criterion of a task, its sub-tasks' included. */
export function checksOf(task: BoardTask): { checked: number; checks: number; passed: number; criteria: number } {
  const checks = [...task.checklist, ...task.subtasks.flatMap((sub) => sub.checklist)];
  const criteria = [...task.criteria, ...task.subtasks.flatMap((sub) => sub.criteria)];
  return {
    checked: checks.filter((item) => item.done).length,
    checks: checks.length,
    passed: criteria.filter((item) => item.status === "passed").length,
    criteria: criteria.length,
  };
}

/** What the toolbar's Play would take, as the backend decides it (app/board.py). */
export function playable(board: BoardSnapshot): { run: BoardTask[]; drafts: BoardTask[] } {
  const todo = laneOfRole(board, "todo")?.id;
  const doing = laneOfRole(board, "in_progress")?.id;
  const run: BoardTask[] = [];
  const drafts: BoardTask[] = [];
  for (const task of board.tasks) {
    if (task.lane === todo && task.status === "draft") drafts.push(task);
    else if (task.lane === todo && task.status === "ready") run.push(task);
    else if ((task.lane === todo || task.lane === doing) && (task.status === "failed" || task.status === "stopped")) {
      run.push(task);
    }
  }
  return { run, drafts };
}

/** Tasks the planners or the agent left for the user to confirm. */
export function awaitingReview(board: BoardSnapshot): BoardTask[] {
  const todo = laneOfRole(board, "todo")?.id;
  return board.tasks.filter((task) => task.lane === todo && task.status === "ready");
}

export function agentsAt(board: BoardSnapshot): number {
  let count = 0;
  for (const task of board.tasks) {
    count += task.subtasks.filter((sub) => sub.status === "running").length;
    if (task.status === "verifying") count += 1;
    else if (task.status === "running" && task.subtasks.length === 0) count += 1;
    else if (task.status === "planning") count += 1;
  }
  return count;
}

/** Cards whose lane differs between two snapshots, for a run's own moves too. */
export function movesBetween(before: BoardSnapshot, after: BoardSnapshot): BoardMove[] {
  const lanes = new Map(before.tasks.map((task) => [task.id, task.lane]));
  const moves: BoardMove[] = [];
  for (const task of after.tasks) {
    const from = lanes.get(task.id);
    if (from !== undefined && from !== task.lane) moves.push({ task: task.id, from, to: task.lane });
  }
  return moves;
}

/** Where a dropped card lands in a lane: before the first card whose middle is below the pointer. */
export function dropIndex(middles: number[], pointerY: number): number {
  const index = middles.findIndex((middle) => pointerY < middle);
  return index === -1 ? middles.length : index;
}

export function isLocked(task: BoardTask): boolean {
  return isBusy(task);
}

export function canRun(task: BoardTask, board: BoardSnapshot): boolean {
  if (isBusy(task) || board.running) return false;
  const lane = board.lanes.find((item) => item.id === task.lane);
  if (lane?.role === "done") return false;
  return task.status === "ready" || task.status === "failed" || task.status === "stopped" || task.status === "draft";
}

/** Sub-tasks this one may wait on: every sibling but itself and those waiting on it. */
export function dependencyChoices(task: BoardTask, sub: BoardSubTask): BoardSubTask[] {
  const waitsOn = new Map(task.subtasks.map((item) => [item.id, item.depends_on]));
  const reaches = (from: string, target: string, seen = new Set<string>()): boolean => {
    if (from === target) return true;
    if (seen.has(from)) return false;
    seen.add(from);
    return (waitsOn.get(from) ?? []).some((next) => reaches(next, target, seen));
  };
  return task.subtasks.filter((other) => other.id !== sub.id && !reaches(other.id, sub.id));
}

export const STATUS_LABEL: Record<TaskStatus, string> = {
  draft: "Draft",
  planning: "Planning",
  ready: "Ready",
  queued: "Queued",
  running: "Running",
  verifying: "Checking",
  done: "Done",
  failed: "Failed",
  stopped: "Stopped",
};

export const SUB_ICON: Record<BoardSubTask["status"], string> = {
  pending: "circle-large-outline",
  queued: "clock",
  running: "loading",
  done: "pass-filled",
  failed: "error",
  stopped: "debug-pause",
  blocked: "circle-slash",
};

/** The three role lanes keep their default names translated until the user renames them. */
export const DEFAULT_TITLES = new Set(["To Do", "In Progress", "Done"]);
