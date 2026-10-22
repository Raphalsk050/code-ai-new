// What every agent on the board is doing, worked out from the steps the backend
// sends (board.activity): the step it is on, how many it took, what it last
// said, and a feed of everything, newest last. Apart from React so it can be
// tested.

import type { BoardStep } from "../../src/board-schema";

/** One agent at work, on a sub-task or on a whole task (its key is the sub-task's id, else the task's). */
export interface LiveAgent {
  agent: string;
  /** The tool it is running, or ran last. */
  tool: string;
  target: string;
  /** "started" while the tool runs, "done" or "failed" once it returned. */
  state: BoardStep["state"];
  /** A phase it is in instead of a tool: planning, checking, fixing. */
  phase: string;
  /** Its last words, while it writes. */
  said: string;
  steps: number;
  at: number;
}

export interface LiveState {
  agents: Record<string, LiveAgent>;
  feed: BoardStep[];
}

const FEED_LIMIT = 300;

export function emptyLive(): LiveState {
  return { agents: {}, feed: [] };
}

export function liveKey(step: Pick<BoardStep, "task" | "subtask">): string {
  return step.subtask || step.task;
}

export function applyStep(state: LiveState, step: BoardStep): LiveState {
  const key = liveKey(step);
  const previous = state.agents[key];
  const base: LiveAgent =
    previous && previous.agent === step.agent
      ? previous
      : { agent: step.agent, tool: "", target: "", state: "", phase: "", said: "", steps: 0, at: step.at };
  let next: LiveAgent = { ...base, at: step.at };
  if (step.kind === "tool") {
    if (step.state === "started") {
      next = { ...next, tool: step.tool, target: step.target, state: "started", phase: "", said: "", steps: base.steps + 1 };
    } else {
      next = { ...next, state: step.state };
    }
  } else if (step.kind === "say") {
    next = { ...next, said: step.text.replace(/^[^:]+:\s*/, "") };
  } else {
    next = { ...next, phase: step.text.replace(/^[^:]+:\s*/, ""), tool: "", target: "" };
  }
  // What was said goes in the feed once a burst settles, not every second of it.
  const feed =
    step.kind === "say" && state.feed.length && state.feed[state.feed.length - 1].kind === "say" &&
    liveKey(state.feed[state.feed.length - 1]) === key
      ? [...state.feed.slice(0, -1), step]
      : step.kind === "tool" && step.state !== "started"
        ? markEnded(state.feed, step)
        : [...state.feed, step];
  return { agents: { ...state.agents, [key]: next }, feed: feed.slice(-FEED_LIMIT) };
}

/** A tool that returned marks the line it started on, rather than adding one of its own. */
function markEnded(feed: BoardStep[], step: BoardStep): BoardStep[] {
  for (let index = feed.length - 1; index >= 0; index--) {
    const line = feed[index];
    if (line.kind === "tool" && line.call && line.call === step.call && line.agent === step.agent) {
      const copy = feed.slice();
      copy[index] = { ...line, state: step.state };
      return copy;
    }
  }
  return feed;
}

/** Agents of a run that ended are no longer at work; the feed stays to be read. */
export function settle(state: LiveState): LiveState {
  return { agents: {}, feed: state.feed };
}

/** The verb a tool reads as on a card ("Editing src/api.py"); English, for t(). */
export function toolVerb(tool: string): string {
  if (tool === "read_file") return "Reading";
  if (tool === "edit_code") return "Editing";
  if (tool === "write_file") return "Writing";
  if (tool === "execute_command") return "Running";
  if (tool === "list_files") return "Listing";
  if (tool === "web_search") return "Searching the web";
  if (tool.startsWith("search_") || tool === "index_workspace") return "Searching";
  if (tool.startsWith("browser_")) return "Browsing";
  if (tool.endsWith("_review")) return "Reviewing";
  return "Using";
}

export function toolIcon(tool: string): string {
  if (tool === "read_file") return "file";
  if (tool === "edit_code" || tool === "write_file") return "edit";
  if (tool === "execute_command") return "terminal";
  if (tool === "list_files") return "list-tree";
  if (tool === "web_search" || tool.startsWith("browser_")) return "globe";
  if (tool.startsWith("search_") || tool === "index_workspace") return "search";
  if (tool.endsWith("_review")) return "eye";
  return "tools";
}

/** mm:ss, or h:mm:ss past the hour. */
export function elapsed(fromS: number, toS: number): string {
  const seconds = Math.max(0, Math.floor(toS - fromS));
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  const pad = (n: number) => String(n).padStart(2, "0");
  return h ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

/** The same colour for the same agent everywhere on the board. */
export function agentHue(agent: string): number {
  let hash = 0;
  for (const char of agent) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  return hash % 360;
}
