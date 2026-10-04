import type { EventEnvelope } from "../../src/protocol";

/** One command in the shared terminal, from the shell's own marks. */
export interface CommandBlock {
  id: number;
  command: string;
  author: "agent" | "user";
  cwd: string;
  /** Epoch ms. */
  startedAt: number;
  endedAt: number | null;
  /** null while running, or when the shell does not report it (cmd.exe). */
  exitCode: number | null;
  running: boolean;
  /** The output with its colours (SGR) kept; the tail when long. */
  output: string;
  truncated: boolean;
}

export interface TerminalState {
  sessionId: string;
  screen: string;
  rows: number;
  cols: number;
  closed: boolean;
  /** A session has been seen this run, so the panel has something to show. */
  live: boolean;
  /** The shell marks its commands: blocks replace the raw screen. */
  integrated: boolean;
  shell: string;
  cwd: string;
  blocks: CommandBlock[];
  /** Who owns the command running now; "" when the prompt is free. */
  runningAuthor: "" | "agent" | "user";
}

export const initialTerminal: TerminalState = {
  sessionId: "",
  screen: "",
  rows: 24,
  cols: 80,
  closed: false,
  live: false,
  integrated: false,
  shell: "",
  cwd: "",
  blocks: [],
  runningAuthor: "",
};

function blockOf(raw: Record<string, any>): CommandBlock {
  return {
    id: Number(raw.id ?? 0),
    command: String(raw.command ?? ""),
    author: raw.author === "user" ? "user" : "agent",
    cwd: String(raw.cwd ?? ""),
    startedAt: Number(raw.started_at ?? 0),
    endedAt: raw.ended_at == null ? null : Number(raw.ended_at),
    exitCode: raw.exit_code == null ? null : Number(raw.exit_code),
    running: Boolean(raw.running),
    output: String(raw.output ?? ""),
    truncated: Boolean(raw.truncated),
  };
}

export function applyTerminal(state: TerminalState, event: EventEnvelope): TerminalState {
  if (event.event_type !== "terminal.screen.updated") return state;
  const p = (event.payload ?? {}) as Record<string, any>;
  const sessionId = String(p.session_id ?? state.sessionId);
  const sameSession = sessionId === state.sessionId;
  const blocks = Array.isArray(p.blocks) ? p.blocks.map(blockOf) : sameSession ? state.blocks : [];
  const closed = Boolean(p.closed);
  const author = String(p.running_author ?? "");
  return {
    sessionId,
    screen: String(p.screen ?? ""),
    rows: typeof p.rows === "number" ? p.rows : state.rows,
    cols: typeof p.columns === "number" ? p.columns : state.cols,
    closed,
    live: true,
    integrated: p.integrated == null ? sameSession && state.integrated : Boolean(p.integrated),
    shell: String(p.shell ?? (sameSession ? state.shell : "")),
    cwd: String(p.cwd ?? (sameSession ? state.cwd : "")),
    blocks,
    runningAuthor: closed || !p.running ? "" : author === "user" ? "user" : "agent",
  };
}
