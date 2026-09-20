// One event in, every slice asked. A slice that does not care returns the same
// reference, so React only re-renders the panels whose slice actually moved.

import type { EventEnvelope } from "../../src/protocol";
import { AgentsState, applyAgents, initialAgents } from "./agents";
import { ApprovalsState, applyApprovals, initialApprovals } from "./approvals";
import { applyCode, CodeStreamState, initialCode } from "./code";
import { applyGoal, GoalState, initialGoal } from "./goal";
import { applyPlan, initialPlan, PlanState } from "./plan";
import { applyQuestions, initialQuestions, QuestionsState } from "./questions";
import { applySession, initialSession, SessionState } from "./session";
import { applyTerminal, initialTerminal, TerminalState } from "./terminal";
import { applyTranscript, initialTranscript, TranscriptState } from "./transcript";
import { applyUsage, initialUsage, UsageState } from "./usage";

export interface AppState {
  transcript: TranscriptState;
  session: SessionState;
  usage: UsageState;
  approvals: ApprovalsState;
  plan: PlanState;
  agents: AgentsState;
  terminal: TerminalState;
  questions: QuestionsState;
  code: CodeStreamState;
  goal: GoalState;
}

export const initialAppState: AppState = {
  transcript: initialTranscript,
  session: initialSession,
  usage: initialUsage,
  approvals: initialApprovals,
  plan: initialPlan,
  agents: initialAgents,
  terminal: initialTerminal,
  questions: initialQuestions,
  code: initialCode,
  goal: initialGoal,
};

export function applyEvent(state: AppState, event: EventEnvelope): AppState {
  const transcript = applyTranscript(state.transcript, event);
  const session = applySession(state.session, event);
  const usage = applyUsage(state.usage, event);
  const approvals = applyApprovals(state.approvals, event);
  const plan = applyPlan(state.plan, event);
  const agents = applyAgents(state.agents, event);
  const terminal = applyTerminal(state.terminal, event);
  const questions = applyQuestions(state.questions, event);
  const code = applyCode(state.code, event);
  const goal = applyGoal(state.goal, event);

  if (
    transcript === state.transcript &&
    session === state.session &&
    usage === state.usage &&
    approvals === state.approvals &&
    plan === state.plan &&
    agents === state.agents &&
    terminal === state.terminal &&
    questions === state.questions &&
    code === state.code &&
    goal === state.goal
  ) {
    return state;
  }
  return {
    transcript,
    session,
    usage,
    approvals,
    plan,
    agents,
    terminal,
    questions,
    code,
    goal,
  };
}

export * from "./agents";
export * from "./approvals";
export * from "./code";
export * from "./goal";
export * from "./plan";
export * from "./questions";
export * from "./session";
export * from "./terminal";
export * from "./transcript";
export * from "./usage";
