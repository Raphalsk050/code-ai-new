// What the status bar says about the agent when the sidebar is closed: the
// context it has left and whether it is working or waiting. No vscode import.

import type { EventEnvelope } from "./protocol";

export interface AgentStatus {
  state: string;
  used: number | null;
  budget: number | null;
  threshold: number;
  tool: string;
  waiting: boolean;
}

export const initialAgentStatus: AgentStatus = {
  state: "STARTING",
  used: null,
  budget: null,
  threshold: 0.82,
  tool: "",
  waiting: false,
};

const BUSY = new Set(["CALLING_MODEL", "EXECUTING_TOOL", "COMPRESSING_CONTEXT", "CANCELLING"]);
const OFFLINE = new Set(["STARTING", "RECONNECTING", "DISCONNECTED"]);

export function reduceAgentStatus(status: AgentStatus, event: EventEnvelope): AgentStatus {
  const p = event.payload ?? {};
  switch (event.event_type) {
    case "status.changed":
      return { ...status, state: String(p.state ?? status.state), tool: BUSY.has(String(p.state)) ? status.tool : "" };
    case "usage.updated":
      return {
        ...status,
        used: p.active_context_tokens != null ? Number(p.active_context_tokens) : status.used,
        budget: p.context_budget != null ? Number(p.context_budget) : status.budget,
        threshold: p.context_threshold != null ? Number(p.context_threshold) : status.threshold,
      };
    case "tool.call.started":
      return { ...status, tool: String(p.name ?? "") };
    case "tool.call.completed":
    case "tool.call.failed":
      return { ...status, tool: "" };
    case "tool.approval.requested":
      return { ...status, waiting: true };
    case "tool.approval.resolved":
    case "user.message":
      return { ...status, waiting: false };
    case "conversation.reset":
      return { ...status, used: null, waiting: false, tool: "" };
    default:
      return status;
  }
}

export interface StatusView {
  text: string;
  tooltip: string;
  /** Past the compaction threshold: the item takes the warning background. */
  warn: boolean;
}

function tokens(n: number): string {
  return n < 1000 ? String(n) : `${Math.round(n / 1000)}k`;
}

export function statusView(status: AgentStatus): StatusView {
  const ratio = status.used != null && status.budget ? status.used / status.budget : null;
  const percent = ratio == null ? "" : ` ${Math.min(999, Math.round(ratio * 100))}%`;
  const context =
    ratio == null || status.used == null || !status.budget
      ? "Context not measured yet"
      : `Context ${tokens(status.used)} of ${tokens(status.budget)}`;
  let icon = "$(hubot)";
  let doing = "Ready";
  if (OFFLINE.has(status.state)) {
    icon = "$(debug-disconnect)";
    doing = status.state === "DISCONNECTED" ? "Offline" : "Connecting";
  } else if (status.waiting) {
    icon = "$(bell-dot)";
    doing = "Waiting for your approval";
  } else if (BUSY.has(status.state)) {
    icon = "$(loading~spin)";
    doing = status.tool ? `Running ${status.tool}` : status.state === "COMPRESSING_CONTEXT" ? "Compacting context" : "Working";
  }
  return {
    text: `${icon} Code-AI${percent}`,
    tooltip: `${doing} · ${context}`,
    warn: ratio != null && ratio >= status.threshold,
  };
}
