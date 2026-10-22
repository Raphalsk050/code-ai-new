// The task board as the backend sends it (code_ai/core/board/models.py), and
// the messages between the board's editor tab and the extension host.

export type LaneRole = "todo" | "in_progress" | "done" | "";

export interface BoardLane {
  id: string;
  title: string;
  role: LaneRole;
}

export interface BoardCheck {
  id: string;
  text: string;
  done: boolean;
}

export interface BoardCriterion {
  id: string;
  text: string;
  status: "pending" | "passed" | "failed";
  note: string;
}

export type SubTaskStatus = "pending" | "queued" | "running" | "done" | "failed" | "stopped" | "blocked";

export interface BoardSubTask {
  id: string;
  title: string;
  description: string;
  status: SubTaskStatus;
  depends_on: string[];
  checklist: BoardCheck[];
  criteria: BoardCriterion[];
  agent: string;
  report: string;
  error: string;
}

export type TaskStatus =
  | "draft"
  | "planning"
  | "ready"
  | "queued"
  | "running"
  | "verifying"
  | "done"
  | "failed"
  | "stopped";

export interface BoardTask {
  id: string;
  title: string;
  lane: string;
  description: string;
  status: TaskStatus;
  subtasks: BoardSubTask[];
  checklist: BoardCheck[];
  criteria: BoardCriterion[];
  depends_on: string[];
  report: string;
  error: string;
  activity: string;
}

export interface BoardSnapshot {
  version: number;
  lanes: BoardLane[];
  tasks: BoardTask[];
  running: boolean;
  planning: boolean;
  run_id: string;
}

/** One edit, as code_ai/core/board/ops.py takes it. */
export type BoardOperation = { op: string } & Record<string, unknown>;

export interface BoardMove {
  task: string;
  from: string;
  to: string;
}

export type BoardHostToWebview =
  | { type: "boardSnapshot"; board: BoardSnapshot; moved: BoardMove[]; language?: string }
  | { type: "boardActivity"; task: string; subtask: string; agent: string; text: string }
  | { type: "boardReview"; tasks: string[]; message: string }
  | { type: "boardNotice"; level: "info" | "warn" | "error"; text: string }
  | { type: "boardCreated"; ids: string[] }
  | { type: "boardRunDone"; status: string; done: string[]; failed: string[] };

export type BoardWebviewToHost =
  | { type: "boardReady" }
  | { type: "boardEdit"; operations: BoardOperation[] }
  | { type: "boardPlan"; tasks: string[] }
  | { type: "boardPlay"; tasks: string[] }
  | { type: "boardStop" };

/** Tasks a run or a planner holds: the panel locks them. */
export const BUSY_STATUSES: ReadonlySet<TaskStatus> = new Set(["planning", "queued", "running", "verifying"]);

export function isBusy(task: BoardTask): boolean {
  return BUSY_STATUSES.has(task.status);
}
