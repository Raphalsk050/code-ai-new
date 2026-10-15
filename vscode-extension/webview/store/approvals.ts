import type { EventEnvelope } from "../../src/protocol";

export interface PendingApproval {
  call_id: string;
  tool_name: string;
  reason: string;
  arguments: Record<string, any>;
}

export interface ApprovalsState {
  pending: PendingApproval | null;
}

export const initialApprovals: ApprovalsState = { pending: null };

const TURN_OVER = new Set(["READY", "IDLE", "CANCELLED", "ERROR", "FAILED", "DISCONNECTED", "CLOSED"]);

export function applyApprovals(state: ApprovalsState, event: EventEnvelope): ApprovalsState {
  const p = event.payload ?? {};
  switch (event.event_type) {
    case "tool.approval.requested":
      return {
        pending: {
          call_id: String(p.call_id ?? ""),
          tool_name: String(p.tool_name ?? ""),
          reason: String(p.reason ?? ""),
          arguments: (p.arguments ?? {}) as Record<string, any>,
        },
      };
    case "tool.approval.resolved":
    case "conversation.reset":
    case "user.message":
      return state.pending ? { pending: null } : state;
    // Nothing waits on an answer once the turn is over, however it ended: a
    // card that outlived it would ask about something that will never run.
    case "status.changed":
      return state.pending && TURN_OVER.has(String(p.state ?? "")) ? { pending: null } : state;
    default:
      return state;
  }
}
