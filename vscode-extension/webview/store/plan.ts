import type { EventEnvelope } from "../../src/protocol";

export type StepStatus = "done" | "running" | "failed" | "paused" | "pending";

export interface PlanStep {
  title: string;
  status: StepStatus;
}

export interface PlanState {
  visible: boolean;
  steps: PlanStep[];
  progress: string;
  status: string;
  currentStep: string;
  /** Evidence the planner recorded this turn, newest last. */
  evidence: { type: string; summary: string }[];
  /** Set when the planner refused a completion claim; cleared on the next plan event. */
  rejection: string;
}

export const initialPlan: PlanState = {
  visible: false,
  steps: [],
  progress: "",
  status: "",
  currentStep: "",
  evidence: [],
  rejection: "",
};

const PLAN_EVENTS = new Set([
  "planning.plan.created",
  "planning.plan.revised",
  "planning.plan.completed",
  "planning.plan.waiting",
  "planning.plan.blocked",
  "planning.plan.failed",
  "planning.step.started",
  "planning.step.completed",
  "planning.step.failed",
  "planning.step.blocked",
]);

/**
 * Rebuild the ordered step list from a plan snapshot payload. Mirrors
 * `code_ai.ui.terminal.widgets.build_plan_steps`: completed steps are always the
 * prefix, and the current step carries the live status.
 */
function buildSteps(p: Record<string, any>): PlanStep[] {
  const completed: string[] = (p.completed_steps ?? []).map((t: unknown) => String(t));
  const remaining: string[] = (p.remaining_steps ?? []).map((t: unknown) => String(t));
  const current = p.current_step == null ? null : String(p.current_step);
  const currentStatus = String(p.current_step_status ?? "");
  const waiting = String(p.status ?? "") === "WAITING";

  const steps: PlanStep[] = completed.map((title) => ({ title, status: "done" as StepStatus }));
  for (const title of remaining) {
    let status: StepStatus = "pending";
    if (current !== null && title === current) {
      status = currentStatus === "FAILED" ? "failed" : waiting ? "paused" : "running";
    }
    steps.push({ title, status });
  }
  return steps;
}

export function applyPlan(state: PlanState, event: EventEnvelope): PlanState {
  const p = (event.payload ?? {}) as Record<string, any>;
  if (event.event_type === "user.message" || event.event_type === "conversation.reset") {
    return initialPlan;
  }
  if (event.event_type === "planning.evidence.recorded") {
    const entry = {
      type: String(p.type ?? "evidence"),
      summary: String(p.summary ?? "").slice(0, 180),
    };
    return { ...state, evidence: [...state.evidence, entry].slice(-8) };
  }
  if (event.event_type === "planning.completion.rejected") {
    const missing = Array.isArray(p.missing_requirements)
      ? p.missing_requirements.map((m: unknown) => String(m)).join(", ")
      : String(p.missing_requirements ?? "");
    return { ...state, rejection: missing || "completion rejected" };
  }
  if (!PLAN_EVENTS.has(event.event_type)) return state;

  const steps = buildSteps(p);
  return {
    ...state,
    steps: steps.length ? steps : state.steps,
    visible: steps.length > 0 || state.steps.length > 0,
    progress: p.progress != null ? String(p.progress) : state.progress,
    status: p.status != null ? String(p.status) : state.status,
    currentStep: p.current_step != null ? String(p.current_step) : "",
    rejection: "",
  };
}
