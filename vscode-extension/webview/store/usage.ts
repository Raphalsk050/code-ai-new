import type { EventEnvelope } from "../../src/protocol";

export interface UsageState {
  used: number | null;
  budget: number | null;
  threshold: number;
  estimated: boolean;
  cumulative: number;
}

export const initialUsage: UsageState = {
  used: null,
  budget: null,
  threshold: 0.82,
  estimated: false,
  cumulative: 0,
};

export function applyUsage(state: UsageState, event: EventEnvelope): UsageState {
  if (event.event_type === "conversation.reset") return initialUsage;
  if (event.event_type !== "usage.updated") return state;
  const p = event.payload ?? {};
  const next = { ...state };
  if (p.active_context_tokens != null) {
    next.used = Number(p.active_context_tokens);
    next.estimated = Boolean(p.active_context_estimated);
  }
  if (p.context_budget != null) next.budget = Number(p.context_budget);
  if (p.context_threshold != null) next.threshold = Number(p.context_threshold);
  const cumulative = p.cumulative;
  if (cumulative && typeof cumulative === "object") {
    next.cumulative = Number((cumulative as any).total_tokens ?? state.cumulative);
  }
  return next;
}

/** Compact token count: 128000 -> "128k". */
export function formatTokens(n: number): string {
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(n < 10_000 ? 1 : 0)}k`;
  return `${(n / 1_000_000).toFixed(1)}M`;
}
