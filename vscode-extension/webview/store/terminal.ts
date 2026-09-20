import type { EventEnvelope } from "../../src/protocol";

export interface TerminalState {
  sessionId: string;
  screen: string;
  rows: number;
  cols: number;
  closed: boolean;
  /** A session has been seen this run, so the panel has something to show. */
  live: boolean;
}

export const initialTerminal: TerminalState = {
  sessionId: "",
  screen: "",
  rows: 24,
  cols: 80,
  closed: false,
  live: false,
};

export function applyTerminal(state: TerminalState, event: EventEnvelope): TerminalState {
  if (event.event_type !== "terminal.screen.updated") return state;
  const p = (event.payload ?? {}) as Record<string, any>;
  return {
    sessionId: String(p.session_id ?? state.sessionId),
    screen: String(p.screen ?? ""),
    rows: typeof p.rows === "number" ? p.rows : state.rows,
    cols: typeof p.columns === "number" ? p.columns : state.cols,
    closed: Boolean(p.closed),
    live: true,
  };
}
