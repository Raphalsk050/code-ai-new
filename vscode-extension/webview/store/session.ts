import type { EventEnvelope } from "../../src/protocol";

export interface SessionState {
  status: string;
  phase: string;
  plannerMode: string;
  permissionMode: string;
  verification: string;
  /** Seconds elapsed in the current turn, from the bridge heartbeat (0 = idle). */
  heartbeat: number;
  /** True while the model request is queued at the provider. */
  modelWaiting: boolean;
  /** Tools running right now, in the order they started. */
  runningTools: RunningTool[];
  /** "3/20" while a failed model request is being sent again, else "". */
  retry: string;
  indexPhase: string;
  indexDone: number;
  indexTotal: number;
}

export interface RunningTool {
  id: string;
  name: string;
  /** What it works on: the file, the command, the query. */
  target: string;
}

export const initialSession: SessionState = {
  status: "STARTING",
  phase: "starting",
  plannerMode: "auto",
  permissionMode: "ask",
  verification: "unknown",
  heartbeat: 0,
  modelWaiting: false,
  runningTools: [],
  retry: "",
  indexPhase: "",
  indexDone: 0,
  indexTotal: 0,
};

const TURN_OVER = new Set(["READY", "ERROR", "FAILED", "CANCELLED", "IDLE", "CLOSED", "DISCONNECTED"]);
// The model answering again, which ends a run of retries.
const MODEL_OUTPUT = new Set([
  "model.stream.delta",
  "model.thinking.delta",
  "model.response.completed",
  "tool.call.progress",
]);

export function applySession(state: SessionState, event: EventEnvelope): SessionState {
  const p = event.payload ?? {};
  if (state.retry && MODEL_OUTPUT.has(event.event_type)) state = { ...state, retry: "" };
  switch (event.event_type) {
    case "session.started":
      return { ...state, permissionMode: String(p.permission_mode ?? state.permissionMode) };
    case "status.changed": {
      const status = String(p.state ?? state.status);
      const over = TURN_OVER.has(status);
      return {
        ...state,
        status,
        heartbeat: over ? 0 : state.heartbeat,
        modelWaiting: status === "CALLING_MODEL" ? state.modelWaiting : false,
        runningTools: over ? [] : state.runningTools,
        retry: over ? "" : state.retry,
      };
    }
    case "tool.call.started":
      return {
        ...state,
        runningTools: [
          ...state.runningTools,
          { id: String(p.tool_call_id ?? ""), name: String(p.name ?? "tool"), target: String(p.target ?? "") },
        ],
      };
    case "tool.call.completed":
    case "tool.call.failed": {
      const id = String(p.tool_call_id ?? "");
      const left = state.runningTools.filter((tool) => tool.id !== id);
      return left.length === state.runningTools.length ? state : { ...state, runningTools: left };
    }
    case "model.request.retrying":
      return { ...state, retry: `${p.attempt ?? "?"}/${p.max_attempts ?? "?"}` };
    case "turn.heartbeat":
      return { ...state, heartbeat: Number(p.elapsed_s ?? 0) };
    case "phase.changed":
      return { ...state, phase: String(p.phase ?? state.phase) };
    case "planning.mode.changed":
      return { ...state, plannerMode: String(p.mode ?? state.plannerMode) };
    case "planning.phase.changed":
      return {
        ...state,
        phase: String(p.phase ?? state.phase),
        plannerMode: String(p.mode ?? state.plannerMode),
      };
    case "permission.mode.changed":
      return { ...state, permissionMode: String(p.mode ?? state.permissionMode) };
    case "model.request.waiting":
      return { ...state, modelWaiting: true };
    case "model.request.started":
    case "model.request.responding":
      return { ...state, modelWaiting: false };
    case "user.message":
      return { ...state, heartbeat: 0, runningTools: [], retry: "" };
    case "index.progress":
      return {
        ...state,
        indexPhase: String(p.phase ?? "indexing"),
        indexDone: Number(p.done ?? 0),
        indexTotal: Number(p.total ?? 0),
      };
    case "index.refreshed":
    case "index.embedded":
      return { ...state, indexPhase: "", indexDone: 0, indexTotal: 0 };
    case "planning.plan.created":
    case "planning.plan.revised":
    case "planning.plan.completed":
    case "planning.plan.waiting":
    case "planning.plan.blocked":
    case "planning.plan.failed":
    case "planning.step.started":
    case "planning.step.completed":
    case "planning.step.failed":
    case "planning.step.blocked": {
      // Every plan snapshot carries the phase and the planner mode; reading
      // them here is what keeps the session panel honest between the sparser
      // planning.phase.changed events.
      const verified = p.latest_verification_passed;
      const next = {
        ...state,
        phase: p.phase != null ? String(p.phase) : state.phase,
        plannerMode: p.mode != null ? String(p.mode) : state.plannerMode,
        verification:
          verified == null ? state.verification : verified ? "passed" : "not current",
      };
      return next.phase === state.phase &&
        next.plannerMode === state.plannerMode &&
        next.verification === state.verification
        ? state
        : next;
    }
    default:
      return state;
  }
}
