import type { EventEnvelope } from "../../src/protocol";

export interface CodeStreamState {
  visible: boolean;
  tool: string;
  path: string;
  key: string;
  reason: string;
  code: string;
  complete: boolean;
  /** Chars seen so far when the payload carries no code, only a size. */
  chars: number;
  lines: number;
}

export const initialCode: CodeStreamState = {
  visible: false,
  tool: "",
  path: "",
  key: "",
  reason: "",
  code: "",
  complete: false,
  chars: 0,
  lines: 0,
};

const TURN_OVER = new Set(["READY", "ERROR", "CANCELLED", "IDLE"]);

export function applyCode(state: CodeStreamState, event: EventEnvelope): CodeStreamState {
  const p = (event.payload ?? {}) as Record<string, any>;
  switch (event.event_type) {
    case "user.message":
    case "conversation.reset":
    case "tool.call.interrupted":
      return state.visible ? initialCode : state;
    case "status.changed":
      // The turn ended, so nothing is being written any more: a finished file
      // left up would park over the answer the user is waiting for.
      return TURN_OVER.has(String(p.state ?? "")) && state.visible ? initialCode : state;
    case "tool.call.completed":
    case "tool.call.failed":
      return state.visible && String(p.name ?? "") === state.tool
        ? { ...state, complete: true }
        : state;
    case "tool.call.progress": {
      const name = String(p.name ?? "tool");
      const path = String(p.path ?? "").trim();
      let next = state;
      if (p.call_started) {
        if (!p.writes) return state.visible ? initialCode : state;
        next = { ...initialCode, visible: true, tool: name };
      } else if (!state.visible) {
        return state;
      }
      next = {
        ...next,
        path: path || next.path,
        reason: typeof p.reason === "string" && p.reason.trim() ? p.reason.trim() : next.reason,
        chars: typeof p.chars === "number" ? p.chars : next.chars,
        lines: typeof p.lines === "number" ? p.lines : next.lines,
      };
      const delta = p.code_delta;
      if (typeof delta !== "string") return next;
      const key = String(p.code_key ?? next.key);
      const offset = typeof p.code_offset === "number" ? p.code_offset : 0;
      if (offset === 0) return { ...next, key, code: delta };
      if (offset !== next.code.length) {
        // An update went missing: splicing here would corrupt the file, so hold
        // the last coherent state instead.
        return next;
      }
      return { ...next, key, code: next.code + delta };
    }
    default:
      return state;
  }
}
