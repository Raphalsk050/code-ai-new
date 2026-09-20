import type { EventEnvelope } from "../../src/protocol";

export type GoalPhase =
  | "none"
  | "proposed"
  | "running"
  | "blocked"
  | "satisfied"
  | "stopped"
  | "exhausted";

export interface GoalState {
  phase: GoalPhase;
  objective: string;
  criteria: string[];
  met: number;
  iteration: number;
  maxIterations: number;
  note: string;
}

export const initialGoal: GoalState = {
  phase: "none",
  objective: "",
  criteria: [],
  met: 0,
  iteration: 0,
  maxIterations: 0,
  note: "",
};

export function applyGoal(state: GoalState, event: EventEnvelope): GoalState {
  if (!event.event_type.startsWith("goal.")) return state;
  const p = (event.payload ?? {}) as Record<string, any>;
  const criteria = Array.isArray(p.criteria) ? p.criteria.map((c: unknown) => String(c)) : null;

  switch (event.event_type) {
    case "goal.defined":
      return {
        ...initialGoal,
        phase: "proposed",
        objective: String(p.objective ?? ""),
        criteria: criteria ?? [],
      };
    case "goal.activated":
    case "goal.resumed":
      return {
        ...state,
        phase: "running",
        maxIterations: Number(p.max_iterations ?? state.maxIterations),
        note: "",
      };
    case "goal.iteration.started":
      return {
        ...state,
        phase: "running",
        iteration: Number(p.iteration ?? state.iteration),
        maxIterations: Number(p.max_iterations ?? state.maxIterations),
      };
    case "goal.iteration.completed":
      return {
        ...state,
        met: Number(p.met ?? state.met),
        note: String(p.summary ?? state.note),
      };
    case "goal.blocked":
      return { ...state, phase: "blocked", note: String(p.reason ?? "") };
    case "goal.satisfied":
      return { ...state, phase: "satisfied", met: Number(p.met ?? state.met), note: "" };
    case "goal.stopped":
      return { ...state, phase: "stopped", note: String(p.reason ?? "") };
    case "goal.exhausted":
      return { ...state, phase: "exhausted", note: String(p.reason ?? "iterations exhausted") };
    default:
      return state;
  }
}
