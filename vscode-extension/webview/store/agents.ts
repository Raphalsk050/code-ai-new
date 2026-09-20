import type { EventEnvelope } from "../../src/protocol";

export type AgentStatus = "running" | "completed" | "failed" | "rejected";

export interface AgentRow {
  id: string;
  type: string;
  name: string;
  task: string;
  status: AgentStatus;
  detail: string;
}

export interface AgentsState {
  /** Dispatch order; terminal rows stay until the next user turn. */
  rows: AgentRow[];
  circuitOpen: string[];
}

export const initialAgents: AgentsState = { rows: [], circuitOpen: [] };

function progressDetail(p: Record<string, any>): string {
  const event = String(p.event ?? "");
  const tool = String(p.tool ?? "").trim();
  if (event === "tool.call.started" && tool) return `running ${tool}`;
  if (event === "tool.call.completed" && tool) return `${tool} done`;
  if (event === "tool.call.failed" && tool) return `${tool} failed`;
  if (event === "model.response.completed") return "thinking";
  return tool || "working";
}

function patch(rows: AgentRow[], id: string, next: Partial<AgentRow>): AgentRow[] {
  let found = false;
  const out = rows.map((r) => {
    if (r.id !== id) return r;
    found = true;
    return { ...r, ...next };
  });
  return found ? out : rows;
}

export function applyAgents(state: AgentsState, event: EventEnvelope): AgentsState {
  const p = (event.payload ?? {}) as Record<string, any>;
  if (event.event_type === "user.message" || event.event_type === "conversation.reset") {
    return initialAgents;
  }
  if (!event.event_type.startsWith("subagent.")) return state;

  const id = String(p.agent_id ?? "");
  const type = String(p.agent_type ?? "agent");
  const name = String(p.name ?? "");

  switch (event.event_type) {
    case "subagent.started":
      return {
        ...state,
        rows: [
          ...state.rows,
          {
            id,
            type,
            name,
            task: String(p.task ?? ""),
            status: "running",
            detail: "dispatched",
          },
        ],
      };
    case "subagent.progress":
      return { ...state, rows: patch(state.rows, id, { detail: progressDetail(p) }) };
    case "subagent.completed":
      return {
        ...state,
        rows: patch(state.rows, id, {
          status: "completed",
          detail: String(p.summary ?? "done").slice(0, 200),
        }),
      };
    case "subagent.failed":
      return {
        ...state,
        rows: patch(state.rows, id, {
          status: "failed",
          detail: String(p.message ?? p.reason ?? "failed").slice(0, 200),
        }),
      };
    case "subagent.rejected":
      return {
        ...state,
        rows: [
          ...state.rows,
          {
            id: id || `rejected-${state.rows.length}`,
            type,
            name,
            task: String(p.reason ?? ""),
            status: "rejected",
            detail: "not dispatched",
          },
        ],
      };
    case "subagent.circuit.open":
      return state.circuitOpen.includes(type)
        ? state
        : { ...state, circuitOpen: [...state.circuitOpen, type] };
    default:
      return state;
  }
}
